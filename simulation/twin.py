"""WATTS digital twin.

A discrete-time simulation of GPU serving, thermals, cooling and power distribution.
Its purpose is to make the whole WATTS pipeline runnable, testable and reproducible on a
laptop - not to predict any real accelerator's behaviour.

Every number this module produces carries ``Provenance.SIMULATED`` and every record is
tagged ``simulated=True``. Nothing here is a measurement, and WATTS refuses to present
simulated output as a benchmark result (see benchmarks/harness.py).

What is modelled
----------------
* serving:  arrival queue, dynamic batching with a wait window, prefill/decode work split
* power:    idle + utilisation-scaled dynamic power per accelerator
* thermal:  lumped thermal mass, conductive removal to the cooling inlet, clock throttling
* cooling:  heat removal at a coefficient of performance that degrades with inlet
            temperature and load, plus a fixed facility overhead
* PUE:      derived from the simulated facility and IT energy, never assumed

The specs below are shaped like datacentre accelerators (idle/TDP ratio, throttle points)
but the throughput constant is arbitrary and the names say "like" for that reason.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from typing import Sequence

from services.energy_engine.model import Provenance
from services.telemetry.schema import RequestRecord, stable_hash
from services.token_engine.profiles import ModelProfile, ProfileRegistry, load_default_profiles


@dataclass(frozen=True)
class GPUSpec:
    name: str
    tdp_w: float
    idle_w: float
    work_units_per_s: float          # profile weight units processed per second at nominal clock
    memory_gb: float
    batch_saturation: int = 32
    throttle_temp_c: float = 83.0
    critical_temp_c: float = 92.0
    min_clock_factor: float = 0.62
    thermal_mass_j_per_c: float = 400.0      # lumped capacity of die + heatsink
    thermal_conductance_w_per_c: float = 7.0  # heat removed per degree above inlet
    leakage_per_c: float = 0.004             # static power rises with temperature
    leakage_ref_temp_c: float = 40.0


#: The throughput constant is expressed in profile weight units per second and is
#: arbitrary: it fixes the twin's time scale, not any vendor's performance. Names end in
#: "-like" because only the shape (idle/TDP ratio, throttle point) is realistic.
A100_LIKE = GPUSpec("a100-like", tdp_w=400, idle_w=52, work_units_per_s=12_000,
                    memory_gb=80, batch_saturation=32)
H100_LIKE = GPUSpec("h100-like", tdp_w=700, idle_w=72, work_units_per_s=27_000,
                    memory_gb=80, batch_saturation=48, thermal_mass_j_per_c=520.0,
                    thermal_conductance_w_per_c=12.0)


@dataclass(frozen=True)
class CoolingSpec:
    inlet_temp_c: float = 24.0
    cop_nominal: float = 4.2            # heat removed per unit of cooling energy
    efficiency: float = 1.0             # 1.0 = as designed; < 1 = fouled coils, failed unit
    fixed_overhead_w_per_gpu: float = 38.0   # PDU/UPS losses, lighting, network, storage


@dataclass(frozen=True)
class DatacenterConfig:
    gpu: GPUSpec = A100_LIKE
    gpus: int = 4
    cooling: CoolingSpec = field(default_factory=CoolingSpec)
    server_cores_per_gpu: int = 12
    dram_gb_per_gpu: float = 96.0


@dataclass(frozen=True)
class ServingConfig:
    max_batch: int = 8
    max_wait_s: float = 0.05            # dynamic batching window
    prefix_cache: bool = False
    response_cache: bool = False
    response_cache_ttl_s: float = 900.0
    output_cap: dict[str, int] = field(default_factory=dict)   # task_class -> max output tokens
    routing: bool = False
    default_model: str = "watts-sim-medium"
    shared_prefix_tokens: int = 900     # size of the common system/context prefix


@dataclass(frozen=True)
class TaskMix:
    task_class: str
    share: float
    min_quality_tier: int
    input_tokens: tuple[int, int]       # (mean, stdev)
    output_tokens: tuple[int, int]
    uses_shared_prefix: bool = True
    latency_slo_ms: float = 4_000.0


DEFAULT_TASK_MIX = (
    TaskMix("classification", 0.40, 1, (1_400, 300), (24, 8), True, 1_500),
    TaskMix("extraction", 0.25, 2, (2_600, 700), (180, 60), True, 3_000),
    TaskMix("summarization", 0.20, 3, (4_200, 1_500), (320, 110), False, 6_000),
    TaskMix("reasoning", 0.15, 5, (1_800, 600), (700, 260), False, 12_000),
)


@dataclass(frozen=True)
class WorkloadConfig:
    duration_s: float = 600.0
    base_rps: float = 1.5
    diurnal_amplitude: float = 0.0       # 0 = flat arrival rate
    task_mix: tuple[TaskMix, ...] = DEFAULT_TASK_MIX
    duplicate_rate: float = 0.08         # share of requests repeating a recent prompt
    failure_rate: float = 0.01
    tenant: str = "tenant-a"
    seed: int = 7


@dataclass
class SimulationResult:
    records: list[RequestRecord]
    power_series: list[dict]             # t_s, it_w, gpu_w, cooling_w, facility_w
    thermal_series: list[dict]           # t_s, per-gpu temperature and clock factor
    config: dict
    provenance: Provenance = Provenance.SIMULATED
    queue_series: list[dict] = field(default_factory=list)   # t_s, depth, busy_devices

    # --- aggregates -------------------------------------------------------

    @property
    def duration_s(self) -> float:
        return self.power_series[-1]["t_s"] if self.power_series else 0.0

    def _integrate(self, key: str) -> float:
        wh = 0.0
        for a, b in zip(self.power_series, self.power_series[1:]):
            wh += 0.5 * (a[key] + b[key]) * (b["t_s"] - a["t_s"]) / 3600.0
        return wh

    @property
    def gpu_energy_wh(self) -> float:
        return self._integrate("gpu_w")

    @property
    def it_energy_wh(self) -> float:
        return self._integrate("it_w")

    @property
    def cooling_energy_wh(self) -> float:
        return self._integrate("cooling_w")

    @property
    def facility_energy_wh(self) -> float:
        return self._integrate("facility_w")

    @property
    def pue(self) -> float:
        it = self.it_energy_wh
        return self.facility_energy_wh / it if it else float("nan")

    def summary(self) -> dict:
        recs = self.records
        ok = [r for r in recs if r.success]
        tokens = sum(r.total_tokens for r in recs)
        latencies = sorted(r.latency_ms for r in recs)
        def pct(p: float) -> float:
            if not latencies:
                return 0.0
            i = min(len(latencies) - 1, max(0, int(round(p * (len(latencies) - 1)))))
            return latencies[i]
        energy = sum(r.energy_wh or 0.0 for r in recs)
        self_critical_temp = self.config.get("datacenter", {}).get("critical_temp_c", 92.0)
        temps = [t["max_temp_c"] for t in self.thermal_series] or [0.0]
        clocks = [t["mean_clock_factor"] for t in self.thermal_series] or [1.0]
        return {
            "provenance": self.provenance.value,
            "simulated": True,
            "duration_s": self.duration_s,
            "requests": len(recs),
            "successful_requests": len(ok),
            "error_rate": 1 - len(ok) / len(recs) if recs else 0.0,
            "cache_hits": sum(1 for r in recs if r.cache_hit),
            "total_tokens": tokens,
            "tokens_per_s": tokens / self.duration_s if self.duration_s else 0.0,
            "output_tokens_per_s": (sum(r.output_tokens for r in recs) / self.duration_s
                                    if self.duration_s else 0.0),
            "gpu_energy_wh": self.gpu_energy_wh,
            "it_energy_wh": self.it_energy_wh,
            "cooling_energy_wh": self.cooling_energy_wh,
            "facility_energy_wh": self.facility_energy_wh,
            "pue": self.pue,
            "wh_per_1k_tokens": energy / (tokens / 1000.0) if tokens else 0.0,
            "facility_wh_per_1k_tokens": (self.facility_energy_wh / (tokens / 1000.0)
                                          if tokens else 0.0),
            "wh_per_request": energy / len(recs) if recs else 0.0,
            "wh_per_successful_request": energy / len(ok) if ok else float("inf"),
            "p50_latency_ms": pct(0.50),
            "p95_latency_ms": pct(0.95),
            "p99_latency_ms": pct(0.99),
            "mean_batch_size": (sum(r.batch_size for r in recs) / len(recs)) if recs else 0.0,
            "max_gpu_temp_c": max(temps),
            "mean_clock_factor": sum(clocks) / len(clocks),
            "throttled_fraction": sum(1 for c in clocks if c < 0.995) / len(clocks),
            "exceeded_critical_temp": max(temps) >= self_critical_temp,
            **self.queueing_metrics(),
        }

    def queueing_metrics(self) -> dict:
        """Arrival → queue → batch formation → service → response.

        Splitting latency into waiting and serving is what makes the batching trade-off
        legible: widening the batch window buys energy with queue time, and if p95 is
        already dominated by service time then widening it buys nothing at all.
        """
        recs = self.records
        waits = sorted(r.queue_wait_ms for r in recs if r.queue_wait_ms is not None)
        services = sorted(r.service_ms for r in recs if r.service_ms is not None)
        depths = [q["depth"] for q in self.queue_series] or [0]
        busy = [q["busy_devices"] for q in self.queue_series] or [0]
        devices = self.config.get("datacenter", {}).get("gpus", 1) or 1

        def pct(values: list[float], p: float) -> float:
            if not values:
                return 0.0
            i = min(len(values) - 1, max(0, int(round(p * (len(values) - 1)))))
            return values[i]

        mean_wait = sum(waits) / len(waits) if waits else 0.0
        mean_service = sum(services) / len(services) if services else 0.0
        return {
            "queue_wait_p50_ms": pct(waits, 0.50),
            "queue_wait_p95_ms": pct(waits, 0.95),
            "queue_wait_p99_ms": pct(waits, 0.99),
            "service_p50_ms": pct(services, 0.50),
            "service_p95_ms": pct(services, 0.95),
            "mean_queue_wait_ms": mean_wait,
            "mean_service_ms": mean_service,
            "queue_share_of_latency": (mean_wait / (mean_wait + mean_service)
                                       if (mean_wait + mean_service) else 0.0),
            "mean_queue_depth": sum(depths) / len(depths),
            "max_queue_depth": max(depths),
            "device_utilization": sum(busy) / (len(busy) * devices),
            "throughput_requests_s": len(recs) / self.duration_s if self.duration_s else 0.0,
        }


@dataclass
class _PendingRequest:
    record_seed: dict
    arrival_s: float
    work_units: float
    profile: ModelProfile


class Simulation:
    """Deterministic given ``WorkloadConfig.seed``."""

    def __init__(self, dc: DatacenterConfig | None = None, serving: ServingConfig | None = None,
                 workload: WorkloadConfig | None = None,
                 registry: ProfileRegistry | None = None, dt: float = 0.05) -> None:
        self.dc = dc or DatacenterConfig()
        self.serving = serving or ServingConfig()
        self.workload = workload or WorkloadConfig()
        self.registry = registry or load_default_profiles()
        self.dt = dt
        self._salt = f"sim-{self.workload.seed}"

    # --- workload generation ---------------------------------------------

    def _generate_requests(self) -> list[_PendingRequest]:
        rng = random.Random(self.workload.seed)
        w = self.workload
        pending: list[_PendingRequest] = []
        recent_prompts: list[str] = []
        t = 0.0
        i = 0
        shares = [m.share for m in w.task_mix]
        while t < w.duration_s:
            rate = w.base_rps * (1 + w.diurnal_amplitude * math.sin(2 * math.pi * t / w.duration_s))
            rate = max(rate, 0.05)
            t += rng.expovariate(rate)
            if t >= w.duration_s:
                break
            task = rng.choices(w.task_mix, weights=shares, k=1)[0]
            inp = max(16, int(rng.gauss(*task.input_tokens)))
            out = max(1, int(rng.gauss(*task.output_tokens)))

            if recent_prompts and rng.random() < w.duplicate_rate:
                prompt_hash = rng.choice(recent_prompts)
            else:
                prompt_hash = stable_hash(f"{task.task_class}:{i}:{inp}:{out}", self._salt)
                recent_prompts.append(prompt_hash)
                if len(recent_prompts) > 64:
                    recent_prompts.pop(0)

            pending.append(_PendingRequest(
                record_seed={
                    "request_id": f"req-{i:06d}",
                    "task_class": task.task_class,
                    "input_tokens": inp,
                    "output_tokens": out,
                    "min_quality_tier": task.min_quality_tier,
                    "uses_shared_prefix": task.uses_shared_prefix,
                    "prompt_hash": prompt_hash,
                    "latency_slo_ms": task.latency_slo_ms,
                    "success": rng.random() > w.failure_rate,
                    "session": stable_hash(f"session-{i // 7}", self._salt),
                },
                arrival_s=t, work_units=0.0, profile=self.registry.get(self.serving.default_model),
            ))
            i += 1
        return pending

    # --- model selection --------------------------------------------------

    def _select_model(self, tier: int) -> ModelProfile:
        if not self.serving.routing:
            return self.registry.get(self.serving.default_model)
        viable = [p for p in self.registry.all() if p.generative and p.quality_tier >= tier]
        if not viable:
            return self.registry.get(self.serving.default_model)
        # cheapest compute among those that meet the quality floor
        return min(viable, key=lambda p: (p.prefill_weight + p.decode_weight, p.quality_tier))

    # --- main loop --------------------------------------------------------

    def run(self) -> SimulationResult:
        s, dc = self.serving, self.dc
        pending = self._generate_requests()
        pending.sort(key=lambda p: p.arrival_s)

        n = dc.gpus
        queues: list[list[_PendingRequest]] = [[] for _ in range(n)]
        busy_until = [0.0] * n
        batch_in_flight: list[int] = [0] * n
        temps = [dc.cooling.inlet_temp_c + 6.0] * n
        clocks = [1.0] * n

        records: list[RequestRecord] = []
        queue_series: list[dict] = []
        power_series: list[dict] = []
        thermal_series: list[dict] = []
        cache: dict[str, float] = {}
        seen_prefix: set[str] = set()

        idx = 0
        t = 0.0
        end = self.workload.duration_s + 30.0
        rr = 0
        while t < end:
            # 1. arrivals
            while idx < len(pending) and pending[idx].arrival_s <= t:
                req = pending[idx]
                idx += 1
                seed = req.record_seed
                if s.response_cache:
                    hit_at = cache.get(seed["prompt_hash"])
                    if hit_at is not None and t - hit_at <= s.response_cache_ttl_s:
                        records.append(self._make_record(
                            seed, req.profile, latency_ms=4.0, energy_wh=1e-6,
                            gpu_id="cache", batch_size=0, cache_hit=True, t=t))
                        continue
                    cache[seed["prompt_hash"]] = t
                queues[rr % n].append(req)
                rr += 1

            # 2. dispatch
            for g in range(n):
                if busy_until[g] > t or not queues[g]:
                    continue
                waited = t - queues[g][0].arrival_s
                if len(queues[g]) < s.max_batch and waited < s.max_wait_s:
                    continue
                batch = [queues[g].pop(0) for _ in range(min(s.max_batch, len(queues[g])))]
                works = [self._work_units(r, seen_prefix) for r in batch]
                duration, energy_wh = self._process_batch(works, clocks[g], temps[g])
                busy_until[g] = t + duration
                batch_in_flight[g] = len(batch)
                total_work = sum(works) or 1.0
                for req, work in zip(batch, works):
                    share = work / total_work
                    seed = req.record_seed
                    queue_wait_ms = (t - req.arrival_s) * 1000.0
                    service_ms = duration * 1000.0
                    latency = queue_wait_ms + service_ms
                    profile = self._select_model(seed["min_quality_tier"])
                    records.append(self._make_record(
                        seed, profile, latency_ms=latency, energy_wh=energy_wh * share,
                        gpu_id=f"gpu-{g}", batch_size=len(batch), cache_hit=False, t=t,
                        queue_wait_ms=queue_wait_ms, service_ms=service_ms))
                    if s.prefix_cache and seed["uses_shared_prefix"]:
                        seen_prefix.add(seed["task_class"])

            # 3. power + thermal
            gpu_w = 0.0
            for g in range(n):
                if busy_until[g] > t:
                    b = max(1, batch_in_flight[g])
                    util = min(1.0, 0.30 + 0.70 * min(b, dc.gpu.batch_saturation) / dc.gpu.batch_saturation)
                else:
                    util = 0.0
                    batch_in_flight[g] = 0
                p = self._device_power(util, clocks[g], temps[g])
                gpu_w += p
                temps[g], clocks[g] = self._thermal_step(temps[g], clocks[g], p)

            host_w = n * (dc.server_cores_per_gpu * 3.0 + dc.dram_gb_per_gpu * 0.35)
            it_w = gpu_w + host_w
            cooling_w = self._cooling_power(it_w, max(temps))
            facility_w = it_w + cooling_w + n * dc.cooling.fixed_overhead_w_per_gpu

            power_series.append({"t_s": t, "gpu_w": gpu_w, "it_w": it_w,
                                 "cooling_w": cooling_w, "facility_w": facility_w})
            queue_series.append({"t_s": t, "depth": sum(len(q) for q in queues),
                                 "busy_devices": sum(1 for b in busy_until if b > t)})
            thermal_series.append({"t_s": t, "max_temp_c": max(temps),
                                   "mean_temp_c": sum(temps) / n,
                                   "mean_clock_factor": sum(clocks) / n,
                                   "temps_c": list(temps)})

            if idx >= len(pending) and all(not q for q in queues) and all(b <= t for b in busy_until):
                if t > self.workload.duration_s:
                    break
            t += self.dt

        return SimulationResult(records, power_series, thermal_series, self.config_dict(),
                                queue_series=queue_series)

    # --- helpers ----------------------------------------------------------

    def _work_units(self, req: _PendingRequest, seen_prefix: set[str]) -> float:
        seed = req.record_seed
        profile = self._select_model(seed["min_quality_tier"])
        inp = seed["input_tokens"]
        if self.serving.prefix_cache and seed["uses_shared_prefix"] and seed["task_class"] in seen_prefix:
            inp = max(16, inp - self.serving.shared_prefix_tokens)
        out = seed["output_tokens"]
        cap = self.serving.output_cap.get(seed["task_class"])
        if cap:
            out = min(out, cap)
        return profile.relative_cost(inp, out)

    def _device_power(self, util: float, clock: float, temp_c: float) -> float:
        """Idle + dynamic power, scaled by a temperature-dependent leakage term.

        Leakage is why a hot accelerator is less efficient even before it throttles: the
        same work draws more watts at 85 C than at 45 C.
        """
        g = self.dc.gpu
        leakage = 1.0 + g.leakage_per_c * max(0.0, temp_c - g.leakage_ref_temp_c)
        return (g.idle_w + (g.tdp_w - g.idle_w) * util * clock) * leakage

    def _process_batch(self, works: list[float], clock: float,
                       temp_c: float) -> tuple[float, float]:
        """Time and energy for one batch step. ``works`` already accounts for caching."""
        dc = self.dc
        work = sum(works)
        b = len(works)
        speedup = min(b, dc.gpu.batch_saturation) ** 0.45
        duration = work / (dc.gpu.work_units_per_s * speedup * max(clock, 0.1))
        util = min(1.0, 0.30 + 0.70 * min(b, dc.gpu.batch_saturation) / dc.gpu.batch_saturation)
        watts = self._device_power(util, clock, temp_c)
        return duration, watts * duration / 3600.0

    def _thermal_step(self, temp: float, clock: float, power_w: float) -> tuple[float, float]:
        g = self.dc.gpu
        c = self.dc.cooling
        removal = g.thermal_conductance_w_per_c * c.efficiency * (temp - c.inlet_temp_c)
        temp = temp + (power_w - removal) * self.dt / g.thermal_mass_j_per_c
        if temp >= g.throttle_temp_c:
            excess = temp - g.throttle_temp_c
            clock = max(g.min_clock_factor, clock - (0.004 + 0.002 * excess))
        elif temp < g.throttle_temp_c - 3 and clock < 1.0:
            clock = min(1.0, clock + 0.002)
        return temp, clock

    def _cooling_power(self, it_w: float, hot_temp_c: float) -> float:
        c = self.dc.cooling
        # CoP degrades as the hottest device runs further above the inlet temperature
        lift = max(1.0, hot_temp_c - c.inlet_temp_c)
        cop = max(1.2, c.cop_nominal * c.efficiency * (30.0 / (lift + 8.0)) ** 0.25)
        return it_w / cop

    def _make_record(self, seed: dict, profile: ModelProfile, *, latency_ms: float,
                     energy_wh: float, gpu_id: str, batch_size: int, cache_hit: bool,
                     t: float, queue_wait_ms: float = 0.0,
                     service_ms: float | None = None) -> RequestRecord:
        out = seed["output_tokens"]
        cap = self.serving.output_cap.get(seed["task_class"])
        if cap:
            out = min(out, cap)
        return RequestRecord(
            request_id=seed["request_id"],
            model=profile.name,
            provider="simulated-local",
            input_tokens=seed["input_tokens"],
            output_tokens=out,
            latency_ms=latency_ms,
            gpu_id=gpu_id,
            tenant=self.workload.tenant,
            timestamp=t,
            security_policy="simulation",
            prompt_hash=seed["prompt_hash"],
            context_prefix_hash=stable_hash(seed["task_class"], self._salt) if seed["uses_shared_prefix"] else None,
            session_id_hash=seed["session"],
            task_class=seed["task_class"],
            batch_size=batch_size,
            context_window=profile.max_context,
            success=seed["success"],
            cache_hit=cache_hit,
            energy_wh=energy_wh,
            energy_provenance=Provenance.SIMULATED.value,
            queue_wait_ms=queue_wait_ms,
            service_ms=latency_ms if service_ms is None else service_ms,
        )

    def config_dict(self) -> dict:
        return {
            "datacenter": {
                "gpu": self.dc.gpu.name, "gpus": self.dc.gpus,
                "tdp_w": self.dc.gpu.tdp_w, "idle_w": self.dc.gpu.idle_w,
                "cooling_inlet_c": self.dc.cooling.inlet_temp_c,
                "cooling_efficiency": self.dc.cooling.efficiency,
                "cop_nominal": self.dc.cooling.cop_nominal,
                "critical_temp_c": self.dc.gpu.critical_temp_c,
            },
            "serving": {
                "max_batch": self.serving.max_batch, "max_wait_s": self.serving.max_wait_s,
                "prefix_cache": self.serving.prefix_cache,
                "response_cache": self.serving.response_cache,
                "output_cap": dict(self.serving.output_cap),
                "routing": self.serving.routing, "default_model": self.serving.default_model,
            },
            "workload": {
                "duration_s": self.workload.duration_s, "base_rps": self.workload.base_rps,
                "duplicate_rate": self.workload.duplicate_rate,
                "failure_rate": self.workload.failure_rate, "seed": self.workload.seed,
            },
            "dt_s": self.dt,
        }
