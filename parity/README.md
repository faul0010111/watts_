# Parity vectors

`vectors.json` holds fixed inputs and the outputs the **Python reference implementation**
produces for them. Both implementations check themselves against this one file:

* `tests/test_java_parity.py` — verifies the reference still produces these outputs, and
  that the JUnit test covers every case;
* `apps/api/src/test/java/io/watts/parity/ParityVectorsTest.java` — the same cases, run
  against the JVM control plane.

## What is covered, and why these four

| Section | Why a divergence here would be expensive |
|---|---|
| `attribution` | a routing decision built on a different prefill/decode split is a different decision |
| `policy` | a simulator that allows what production denies is the worst failure this project could have — allow/deny *and* the rule id, because the rule id is what the audit trail shows |
| `budgets` | chargeback that disagrees between two systems is chargeback nobody trusts |
| `canonical_json` | if the two serialise a record differently, every HMAC fails and the trust boundary stops working |

## Regenerating

```bash
python tools/generate_parity_vectors.py
```

Regenerate **only** when the reference behaviour changed deliberately, in the same commit
as that change. Regenerating to make a failing implementation pass removes the only thing
keeping the two honest, and does it silently.

The JVM tests currently fail with "not implemented yet" by design: the scaffold in
`apps/api/` has no engine behind it. A failing parity test is an accurate statement about
the state of that implementation, which is more useful than a passing test that asserts
nothing.
