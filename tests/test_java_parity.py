"""The parity vectors must describe the reference implementation, and the JVM test must
cover every case in them.

Two failure modes this guards against. The vectors drifting from the Python behaviour they
claim to describe, which would silently rewrite the specification. And a case being added
to the vectors that the Java side never runs, which would look like coverage and provide
none.
"""
import json
import re
import unittest
from pathlib import Path

from conftest import ROOT
from services.optimization.budgets import Budget, BudgetState, BudgetTracker
from services.security.policy import PolicyEngine, PolicyInput
from services.telemetry.schema import RequestRecord
from services.token_engine import attribute_energy, load_default_profiles

VECTORS = ROOT / "parity" / "vectors.json"
JAVA_TEST = ROOT / "apps" / "api" / "src" / "test" / "java" / "io" / "watts" / "parity" / "ParityVectorsTest.java"


class TestVectorFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(VECTORS.read_text())
        cls.tolerance = cls.data["tolerance"]
        cls.registry = load_default_profiles()
        cls.engine = PolicyEngine()

    def test_file_exists_and_declares_its_purpose(self):
        self.assertIn("purpose", self.data)
        self.assertIn("schema_version", self.data)

    def test_attribution_vectors_match_the_reference(self):
        for case in self.data["attribution"]:
            i, e = case["input"], case["expected"]
            result = attribute_energy(i["energy_wh"], i["input_tokens"], i["output_tokens"],
                                      self.registry.get(i["model"]))
            self.assertAlmostEqual(result.input_energy_wh, e["input_energy_wh"],
                                   delta=self.tolerance, msg=case["id"])
            self.assertAlmostEqual(result.output_energy_wh, e["output_energy_wh"],
                                   delta=self.tolerance, msg=case["id"])

    def test_policy_vectors_match_the_reference(self):
        for case in self.data["policy"]:
            kwargs = {k: (tuple(v) if isinstance(v, list) else v)
                      for k, v in case["input"].items()}
            decision = self.engine.evaluate(PolicyInput(**kwargs))
            self.assertEqual(decision.allow, case["expected"]["allow"], case["id"])
            self.assertEqual(decision.rule_id, case["expected"]["rule_id"], case["id"])

    def test_budget_vectors_match_the_reference(self):
        for case in self.data["budgets"]:
            budget = Budget(**case["input"]["budget"])
            state = BudgetState(**case["input"]["state"])
            result = BudgetTracker({budget.workload: budget}).evaluate(state)
            observed = [b["dimension"] for b in result["breaches"]]
            self.assertEqual(observed, [b["dimension"] for b in case["expected"]["breaches"]],
                             case["id"])
            for got, want in zip(result["breaches"], case["expected"]["breaches"]):
                self.assertAlmostEqual(got["projected"], want["projected"],
                                       delta=self.tolerance, msg=case["id"])

    def test_canonical_json_and_signature_match_the_reference(self):
        for case in self.data["canonical_json"]:
            record = RequestRecord(**case["input"])
            self.assertEqual(record.canonical_json(), case["expected"]["canonical_json"])
            key = case["expected"]["hmac_key_utf8"].encode()
            self.assertEqual(record.signature(key), case["expected"]["signature_hex"])

    def test_policy_vectors_cover_every_rule(self):
        covered = {c["expected"]["rule_id"] for c in self.data["policy"] if c["expected"]["rule_id"]}
        from services.security.policy import default_policy_set
        for rule in default_policy_set():
            self.assertIn(rule.id, covered, f"no parity vector exercises {rule.id}")

    def test_vectors_include_an_allow_case_per_section(self):
        self.assertTrue(any(c["expected"]["allow"] for c in self.data["policy"]),
                        "vectors only cover denials; an implementation that denies "
                        "everything would pass")


class TestJavaSideCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(VECTORS.read_text())
        cls.java = JAVA_TEST.read_text()

    def test_java_test_exists_and_reads_the_same_file(self):
        self.assertIn("parity", self.java)
        self.assertIn("vectors.json", self.java)

    def test_java_test_covers_every_section(self):
        for section in ("attribution", "policy", "budgets", "canonical_json"):
            self.assertIn(f'"{section}"', self.java, section)

    def test_java_test_checks_the_rule_id_not_only_the_verdict(self):
        self.assertIn("rule_id", self.java)

    def test_java_failures_are_explicit_about_being_unimplemented(self):
        # The scaffold has no engine behind it. A failing parity test is an accurate
        # statement about that; a passing test that asserts nothing would not be.
        self.assertIn("not implemented yet", self.java)

    def test_tolerance_is_shared_rather_than_hard_coded_twice(self):
        self.assertIn("tolerance", self.java)
        self.assertNotIn("1e-9", re.sub(r'"[^"]*"', "", self.java))


if __name__ == "__main__":
    unittest.main()
