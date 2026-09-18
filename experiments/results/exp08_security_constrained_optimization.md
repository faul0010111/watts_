# Experiment 08 - Security-constrained optimisation

**Research question.** How do energy optimisation and security policy combine?

*Produced by the WATTS digital twin. Valid as a statement about the model under the stated configuration, not as a hardware measurement.*

| Posture | Description | Applied | Blocked | Claimed saving Wh |
|---|---|---|---|---|
| unconstrained | No policy engine: every candidate is applied | 8 | 0 | 2340 |
| policy_enforced | WATTS default policy set | 3 | 5 | 860 |

## What the run shows

- The policy engine blocked 5 of 8 candidates, leaving 860 Wh of the 2340 Wh an unconstrained optimiser would claim.
- The 1480 Wh difference is not a saving that was lost. It is the set of changes that would have shared a cache across tenants, removed an audit control, downgraded a critical workload, and spent latency the SLO did not have.
- Blocked candidates and the rule that blocked each one: Route fraud scoring to a smaller model (WATTS-SEC-006); Defer regulated batch job to a cheaper hour (WATTS-SEC-006); Share the response cache across tenants (WATTS-SEC-003); Disable per-request audit logging (WATTS-SEC-003); Batch window 200 -> 900 ms (WATTS-SEC-007)
- Every optimisation that survives is still a proposal. Execution requires a human approval and writes an entry to the hash-chained audit log.

## Manifest

- experiment `exp08_security_constrained_optimization` v1.0 (schema 2)
- seed `11`, provenance `simulated`
- method: single-factor sweep on the WATTS digital twin, repeated across seeds
- metrics: posture, description, applied, blocked, energy_saving_wh

## Reproduce

```bash
make experiment EXP=exp08_security_constrained_optimization
```
