# Policies

OPA/Rego is the enforcement point in a deployed WATTS. `services/security/policy.py` is a
Python mirror of the same rules so the simulator, tests and CI can evaluate policy without
a running OPA.

| File | Package | Covers |
|---|---|---|
| `watts_routing.rego` | `watts.routing` | model and provider allowlists, data classification, tenant isolation |
| `watts_optimization.rego` | `watts.optimization` | control weakening, human approval, critical workloads, SLO headroom, roles |
| `watts_llm_guard.rego` | `watts.llm` | what the WATTS assistant may and may not do |

## Running

```bash
opa eval -d policies/ -i example-input.json 'data.watts.optimization.deny'
opa test policies/            # add your own _test.rego files
```

## Parity

`tests/test_policy_parity.py` asserts that the Python mirror and the rule identifiers in
the Rego files stay aligned. If you add a rule to one, add it to the other in the same
commit: a policy that exists in only one place is a policy you cannot trust.
