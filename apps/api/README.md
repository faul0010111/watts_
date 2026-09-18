# apps/api — JVM control plane (scaffold)

This is the production target described in `docs/ARCHITECTURE.md`: Java 21, Spring Boot,
WebFlux, Spring Security, OPA. **It is a scaffold, not a running system.** The behaviour it
is being built against is specified and tested in the Python reference implementation under
`services/`, which is what `python watts.py demo` runs.

What is here: the ingest trust boundary — the part where getting it wrong is most
expensive. `TelemetryController` and `TelemetryRecord` mirror
`services/telemetry/gateway.py` and `services/telemetry/schema.py`:

* prompt and completion text are not fields on the record, so an integration cannot send
  them;
* every record is HMAC-signed per workload and checked in constant time;
* replay, clock skew and physical plausibility are checked before anything downstream sees
  the record.

When you change a rule here, change it in the Python mirror in the same commit, and keep
`tests/test_policy_parity.py` green.

```bash
cd apps/api && ./mvnw spring-boot:run     # once the remaining services are implemented
```
