"""Hardware mode: one energy engine, several sources of truth.

                      Energy Engine
                            ▲
              ┌─────────────┴─────────────┐
      Simulation Adapter            Hardware Adapter
      (digital twin)                ├── NVML
                                    ├── DCGM
                                    └── External meter (PDU / BMS)

Every adapter returns the same ``PowerSample`` stream and declares its own provenance, so
swapping the twin for a real accelerator changes the evidence attached to every number
downstream - and nothing else. That is the whole point: the code path that produces a
report must not know or care whether it was fed a simulation, because if it did, the two
would drift apart.

The hardware adapters refuse to start rather than degrade quietly. An adapter that cannot
reach NVML must not fall back to a model: it must say so, because the difference between
measured and estimated is exactly what WATTS exists to preserve.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterator, Protocol, Sequence

from ..energy_engine.model import PowerSample, Provenance


class AdapterUnavailable(RuntimeError):
    """The adapter's data source is not reachable here. Never silently substituted."""


class TelemetryAdapter(Protocol):
    """What the energy engine needs from any source of power data."""

    name: str
    provenance: Provenance

    def device_count(self) -> int: ...

    def sample(self, device: int) -> PowerSample: ...

    def describe(self) -> dict: ...


@dataclass
class SimulationAdapter:
    """Replays a digital-twin power series as if it were a device.

    Marked ``SIMULATED``, permanently and unconditionally. There is no configuration flag
    that makes this adapter report ``MEASURED``.
    """

    power_series: Sequence[dict]
    gpus: int = 1
    name: str = "watts-digital-twin"
    provenance: Provenance = Provenance.SIMULATED

    def device_count(self) -> int:
        return self.gpus

    def stream(self, device: int = 0) -> Iterator[PowerSample]:
        for point in self.power_series:
            yield PowerSample(
                t_s=point["t_s"],
                watts=point["gpu_w"] / max(self.gpus, 1),
                provenance=Provenance.SIMULATED,
                source=self.name,
            )

    def sample(self, device: int = 0) -> PowerSample:
        return next(self.stream(device))

    def samples(self, device: int = 0) -> list[PowerSample]:
        return list(self.stream(device))

    def describe(self) -> dict:
        return {"adapter": self.name, "provenance": self.provenance.value,
                "devices": self.gpus, "points": len(self.power_series),
                "warning": "simulated data; never present these figures as measurements"}


class NVMLAdapter:
    """NVIDIA Management Library, per-device power.

    Requires ``nvidia-ml-py`` and a driver on the host (``pip install watts[gpu]``).
    Sample at 100 ms or faster: LLM serving power swings between prefill bursts and decode
    steps, and a slow scrape averages away the structure WATTS attributes to tokens.
    """

    name = "nvml"
    provenance = Provenance.MEASURED

    def __init__(self, *, min_interval_s: float = 0.1) -> None:
        try:
            import pynvml  # type: ignore
        except ImportError as exc:                       # pragma: no cover - needs hardware
            raise AdapterUnavailable(
                "pynvml is not installed. WATTS will not substitute a model for a "
                "measurement: install watts[gpu] on a host with NVIDIA drivers, or use "
                "SimulationAdapter and accept simulated provenance."
            ) from exc
        self._nvml = pynvml                              # pragma: no cover
        self._nvml.nvmlInit()                            # pragma: no cover
        self.min_interval_s = min_interval_s             # pragma: no cover

    def device_count(self) -> int:                       # pragma: no cover
        return self._nvml.nvmlDeviceGetCount()

    def sample(self, device: int = 0) -> PowerSample:    # pragma: no cover
        handle = self._nvml.nvmlDeviceGetHandleByIndex(device)
        milliwatts = self._nvml.nvmlDeviceGetPowerUsage(handle)
        return PowerSample(time.time(), milliwatts / 1000.0, Provenance.MEASURED,
                           f"nvml:device{device}")

    def describe(self) -> dict:                          # pragma: no cover
        return {"adapter": self.name, "provenance": self.provenance.value,
                "devices": self.device_count(), "min_interval_s": self.min_interval_s}


class DCGMAdapter:
    """NVIDIA DCGM exporter, scraped over HTTP.

    Preferred over raw NVML in a cluster: DCGM already aggregates per-device counters and
    exposes the total energy counter, which avoids integrating a sampled series.
    """

    name = "dcgm"
    provenance = Provenance.MEASURED

    def __init__(self, endpoint: str = "http://localhost:9400/metrics",
                 *, fetch: Callable[[str], str] | None = None) -> None:
        self.endpoint = endpoint
        self._fetch = fetch
        if fetch is None:                                # pragma: no cover - needs a cluster
            raise AdapterUnavailable(
                "no DCGM transport configured. Pass fetch=<callable returning the "
                "exporter's metrics text> so the caller owns the HTTP client, its "
                "timeouts and its credentials."
            )

    def device_count(self) -> int:
        return len(self._power_by_device())

    def sample(self, device: int = 0) -> PowerSample:
        power = self._power_by_device()
        if device not in power:
            raise AdapterUnavailable(f"DCGM reported no power for device {device}")
        return PowerSample(time.time(), power[device], Provenance.MEASURED,
                           f"dcgm:device{device}")

    def _power_by_device(self) -> dict[int, float]:
        """Parse DCGM_FI_DEV_POWER_USAGE lines out of the exporter's text format."""
        text = self._fetch(self.endpoint)
        out: dict[int, float] = {}
        for line in text.splitlines():
            if not line.startswith("DCGM_FI_DEV_POWER_USAGE"):
                continue
            try:
                labels = line[line.index("{") + 1:line.index("}")]
                value = float(line.rsplit(" ", 1)[1])
                gpu = next(part.split("=")[1].strip('"') for part in labels.split(",")
                           if part.startswith("gpu="))
                out[int(gpu)] = value
            except (ValueError, StopIteration):
                continue          # a malformed line is dropped, never guessed at
        return out

    def describe(self) -> dict:
        return {"adapter": self.name, "provenance": self.provenance.value,
                "endpoint": self.endpoint}


class ExternalMeterAdapter:
    """A PDU, rack meter or building management system.

    The only adapter that can report *facility* power rather than device power, which is
    what turns PUE from an estimate into a derived figure.
    """

    name = "external-meter"
    provenance = Provenance.MEASURED

    def __init__(self, read_watts: Callable[[], float], *, scope: str = "facility",
                 meter_id: str = "unknown") -> None:
        self._read = read_watts
        self.scope = scope
        self.meter_id = meter_id

    def device_count(self) -> int:
        return 1

    def sample(self, device: int = 0) -> PowerSample:
        return PowerSample(time.time(), float(self._read()), Provenance.MEASURED,
                           f"meter:{self.meter_id}:{self.scope}")

    def describe(self) -> dict:
        return {"adapter": self.name, "provenance": self.provenance.value,
                "scope": self.scope, "meter_id": self.meter_id}


def probe_adapters() -> list[dict]:
    """Which sources of truth are reachable here. Used by the report's provenance block."""
    results = []
    for name, factory in (("nvml", NVMLAdapter), ("dcgm", DCGMAdapter)):
        try:
            adapter = factory()                       # pragma: no cover - hardware only
            results.append({**adapter.describe(), "available": True})
        except AdapterUnavailable as exc:
            results.append({"adapter": name, "available": False, "reason": str(exc).split(".")[0]})
    results.append({"adapter": "watts-digital-twin", "available": True,
                    "provenance": Provenance.SIMULATED.value})
    return results
