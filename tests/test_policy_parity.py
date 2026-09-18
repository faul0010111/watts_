"""The Rego policies and their Python mirror must describe the same rules.

A rule that exists in only one of the two is a rule the operator cannot trust: the
simulator would allow what production denies, or the other way round.
"""
import re
import unittest
from pathlib import Path

from conftest import ROOT
from services.security.policy import default_policy_set

POLICY_DIR = ROOT / "policies"
RULE_ID = re.compile(r"WATTS-SEC-\d{3}")


class TestPolicyParity(unittest.TestCase):
    def setUp(self):
        self.rego_ids = set()
        for path in POLICY_DIR.glob("*.rego"):
            self.rego_ids |= set(RULE_ID.findall(path.read_text()))
        self.python_ids = {r.id for r in default_policy_set()}

    def test_every_python_rule_exists_in_rego(self):
        missing = self.python_ids - self.rego_ids
        self.assertFalse(missing, f"rules missing from policies/*.rego: {sorted(missing)}")

    def test_every_rego_rule_exists_in_python(self):
        missing = self.rego_ids - self.python_ids
        self.assertFalse(missing, f"rules missing from services/security/policy.py: {sorted(missing)}")

    def test_rule_ids_are_unique(self):
        ids = [r.id for r in default_policy_set()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_llm_guard_denies_execution_tools(self):
        text = (POLICY_DIR / "watts_llm_guard.rego").read_text()
        for tool in ("execute_change", "approve_optimization", "update_policy"):
            self.assertIn(tool, text)


if __name__ == "__main__":
    unittest.main()
