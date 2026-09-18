# Roadmap

WATTS is experimental. The order below reflects what would make it useful in a real
environment, not what is easiest to build.

## Done in v0.2 (V2)

- [x] Energy provenance 2.0: value, unit, source, provenance, timestamp, confidence,
      calibration id, with arithmetic that cannot launder evidence
- [x] Calibration engine: sessions, fitting, error analysis, status, report
- [x] Request energy decomposition into baseline / prefill / decode / memory / network /
      storage / shared
- [x] Energy–latency–quality frontier with quality floors, where unknown is inadmissible
- [x] Optimisation planner with per-step re-simulation and a reported interaction error
- [x] No-action projection
- [x] Root cause analysis graded primary / contributing / possible / insufficient evidence
- [x] Detection suite with the method printed beside every score
- [x] Feature-based forecasting, four targets and budget-breach probability
- [x] Queueing model: queue wait vs service time, depth, utilisation
- [x] Energy FinOps with allocation and the double-counting rule
- [x] Audit 2.0: tenant and policy version in the hash, checkpoints, sealed retention,
      scoped export
- [x] Report generator: `watts.py demo` writes the full document, no hand-written numbers
- [x] Reproducibility: run id, seed, configuration hash, code revision, policy version
- [x] Experiment manifests and `exp09_prefill_vs_decode`
- [x] Benchmark 2.0 with quality, security compliance, admissibility and `STATUS: NOT RUN`
- [x] Command Center 2.0: five sections, fed by the same data as the report
- [x] Hardware adapters (NVML, DCGM, external meter) behind one interface
- [x] SDK `privacy_mode`, strict by default
- [x] Parity vectors checked from both the Python and the JVM side

## Now — the reference implementation (this repository)

- [x] Energy model with per-component provenance and derived PUE
- [x] Token attribution, efficiency findings, token economics
- [x] Telemetry schema that rejects content; gateway with signatures, replay and
      plausibility checks
- [x] Policy engine, RBAC, hash-chained audit, OPA/Rego mirror with parity tests
- [x] Energy-aware router, recommendations, SLOs and budgets, human-in-the-loop workflow
      with circuit breaker
- [x] Price- and carbon-aware scheduler that never defers critical work
- [x] Forecasting with residual-based intervals and backtesting
- [x] Digital twin: serving, thermals, throttling, cooling, power distribution
- [x] Benchmark harness and eight reproducible experiments
- [x] Command Center as a self-contained HTML build
- [x] Python and TypeScript telemetry SDKs

## Next — measured data

- [ ] NVML/DCGM collector at 100 ms resolution, with a node-level PDU adapter
- [ ] vLLM and TGI integrations that report per-request token counts and batch composition
- [ ] Calibration runner: exp01, exp02 and exp09 against real hardware, writing calibrated
      profiles automatically
- [ ] RAPL adapter, moving CPU and DRAM from `estimated` to `measured`
- [ ] Facility adapters: BMS, PDU, CRAC telemetry for a metered PUE
- [ ] Replay mode: run the whole pipeline over a recorded window of real telemetry

## Then — the production control plane

- [ ] Spring Boot services behind Kafka, with TimescaleDB retention policies — starting
      with the four areas the parity vectors cover, which currently fail by design
- [ ] OPA sidecar with signed policy bundles
- [ ] Next.js Command Center over WebSockets, tenant-scoped
- [ ] OpenTelemetry semantic conventions for LLM energy attributes
- [ ] Multi-tenant chargeback with an allocation method that survives scrutiny
- [ ] Kubernetes operator for energy-aware scheduling hints
- [ ] Terraform modules for the supporting infrastructure

## Later — research

- [ ] Cross-hardware transfer of model profiles: does a calibrated ratio hold on a
      different accelerator?
- [ ] Marginal cooling cost of a marginal token, measured at several inlet temperatures
- [ ] Quality-aware routing with per-task evaluation in the loop
- [ ] Fair energy allocation inside a shared batch
- [ ] Energy-aware autoscaling that spends latency headroom explicitly and reversibly
- [ ] Published, reproducible measured benchmark with full disclosure

## Explicitly out of scope

* Implementing serving-level optimisations (batching schedulers, attention kernels,
  quantisation). WATTS measures and recommends; the serving stack implements.
* Autonomous infrastructure changes without human approval.
* Carbon accounting claims or offset arithmetic. WATTS reports energy, and carbon only as
  energy multiplied by an operator-supplied intensity signal.
* A vendor efficiency leaderboard. The measurement conditions that would make one
  meaningful do not exist yet.
