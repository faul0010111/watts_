# Security

## The rule that shapes the project

> An energy optimisation is never a reason to weaken a security control.

Not weighed, not traded, not deferred: blocked. `WATTS-SEC-003` in
`policies/watts_optimization.rego` and `services/security/policy.py`. Experiment 08
quantifies what the controls cost and shows that the unconstrained saving is made of
changes that would share a cache across tenants, remove an audit control, downgrade a
critical workload and overspend the SLO.

## Policy set

| Rule | Applies to | Denies |
|---|---|---|
| `WATTS-SEC-001` | routing | production traffic to a model outside the allowlist |
| `WATTS-SEC-002` | routing | sensitive data to an unapproved model or provider |
| `WATTS-SEC-003` | optimisation, change | anything that weakens a control |
| `WATTS-SEC-004` | change | execution without a recorded human approval |
| `WATTS-SEC-005` | routing, telemetry | cross-tenant access |
| `WATTS-SEC-006` | optimisation | deferring, downgrading or throttling a critical workload |
| `WATTS-SEC-007` | optimisation | latency cost beyond the remaining SLO headroom |
| `WATTS-SEC-008` | change | execution by a principal without an operator role |

Default deny. OPA is the enforcement point; `services/security/policy.py` is a mirror so
the simulator and CI can evaluate the same rules. `tests/test_policy_parity.py` fails the
build if a rule exists in only one of the two.

## Identity and access

`services/security/rbac.py`. Roles carry explicit permissions:

| Role | Read telemetry | Simulate | Propose | Approve | Execute | Manage policy |
|---|---|---|---|---|---|---|
| viewer | ✓ | | | | | |
| analyst | ✓ | ✓ | ✓ | | | |
| sre | ✓ | ✓ | ✓ | ✓ | ✓ | |
| operator | ✓ | | | | ✓ | |
| security | ✓ | | | | | ✓ |
| **watts-assistant** | ✓ | ✓ | ✓ | | | |

The optimisation assistant is a principal like any other, with deliberately weak rights.
It cannot approve its own proposals, execute anything, change policy or read across
tenants.

## LLM-specific threats

`policies/watts_llm_guard.rego` constrains the assistant directly:

* **Prompt injection through telemetry.** Telemetry is data, never instructions. Even a
  successful injection can only produce a *proposal*, which a human reviews and the policy
  engine has already filtered. The assistant has no path to an infrastructure API.
* **Forged energy measurements.** A workload could otherwise report near-zero energy and
  win the efficiency leaderboard, or inflate a rival tenant's figures. The gateway checks
  HMAC signatures per workload, rejects replays and clock skew, and rejects physically
  implausible readings (energy implying impossible device power, Wh per 1k tokens outside
  bounds, inputs exceeding the declared context window).
* **Unauthorised routing.** Routing decisions pass the policy engine on every request, not
  once at configuration time.
* **Data exfiltration.** There is no prompt text in the system to exfiltrate. The SDKs ship
  with `privacy_mode` set to `strict`: counts, timings and per-tenant salted hashes.
  `metadata` additionally permits validated scalar labels; `no-hashes` derives nothing from
  the prompt at all, at the cost of duplicate and prefix detection. There is no mode that
  sends content, because the record type has no field for it — privacy here is a property of
  the schema, not a setting someone can flip.
* **Tool abuse.** Tool allowlist plus a per-session tool-call budget.

## Data protection

* No prompts, completions, API keys, passwords or secrets stored — enforced at the schema
  boundary, not by a downstream filter.
* Data minimisation: counts, timings, identifiers, hashes.
* Per-tenant salts for all hashes.
* Tenant isolation in storage, queries and routing.
* Retention: raw records short-lived, aggregates long-lived (see `docs/SRE_MODEL.md`).
* Encryption in transit and at rest is a deployment concern; see `infrastructure/`.

## Audit

`services/security/audit.py` keeps a hash-chained log. Each entry commits to the previous
entry's digest and carries:

```
entry_id · timestamp · actor · tenant · action · decision · policy_version ·
previous_hash · current_hash
```

`tenant` and `policy_version` are inside the hash, because a decision is only meaningful
next to the rule set that produced it, and a record written without a tenant must not later
be claimed to have had one.

**Checkpoints.** Hash chaining proves the sequence has not been rewritten *given a trusted
head*. It cannot prove that on its own: whoever can rewrite the log can rewrite every digest
in it. `checkpoint()` produces a `(index, head, timestamp)` triple to publish somewhere WATTS
cannot reach — object lock, WORM bucket, transparency log — and `verify_against()` checks
the log still contains the history that checkpoint committed to.

**Retention.** `prune()` drops entries older than a cutoff and leaves a `SealedSegment`
recording the range, the count and the hash the surviving chain continues from. Deleting
audit history silently would leave a chain that verifies perfectly while hiding that
anything was removed — the exact failure the chain exists to prevent.

**Access.** Reads are tenant-scopable and `export()` requires `audit:read`, because
exporting an audit trail is itself an action worth authorising. Every export carries its own
verification state, its checkpoints and its sealed segments; an export that omitted them
would be a document with no evidential value.

This is tamper *evidence*, not tamper proofing, and the export says so in its own note.

## Failure safety

The optimiser failing must never take the data plane with it: safe defaults (change
nothing), rollback on failed validation, a circuit breaker that opens after repeated
failures, a change rate limit, and a manual override. See
`services/optimization/workflow.py`.
