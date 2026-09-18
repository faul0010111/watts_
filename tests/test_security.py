import unittest

from conftest import ROOT  # noqa: F401
from services.security import (
    AuditLog, Permission, PolicyEngine, PolicyInput, Principal, RBAC,
)


class TestPolicy(unittest.TestCase):
    def setUp(self):
        self.engine = PolicyEngine()

    def test_unapproved_model_blocked_in_production(self):
        d = self.engine.evaluate(PolicyInput("route_workload", model="x",
                                             approved_models=("a", "b")))
        self.assertFalse(d.allow)
        self.assertEqual(d.rule_id, "WATTS-SEC-001")

    def test_approved_model_allowed(self):
        self.assertTrue(self.engine.evaluate(
            PolicyInput("route_workload", model="a", approved_models=("a",))).allow)

    def test_sensitive_data_needs_approved_provider(self):
        d = self.engine.evaluate(PolicyInput(
            "route_workload", model="a", provider="public-api", approved_models=("a",),
            approved_providers=("on-prem",), data_classification="restricted"))
        self.assertEqual(d.rule_id, "WATTS-SEC-002")

    def test_energy_never_justifies_weakening_a_control(self):
        d = self.engine.evaluate(PolicyInput("apply_optimization", weakens_control=True,
                                             energy_saving_wh=10_000))
        self.assertFalse(d.allow)
        self.assertEqual(d.rule_id, "WATTS-SEC-003")

    def test_critical_workload_never_deferred(self):
        for action in ("defer_workload", "downgrade_model", "throttle"):
            d = self.engine.evaluate(PolicyInput("apply_optimization", optimization_type=action,
                                                 workload_criticality="critical"))
            self.assertEqual(d.rule_id, "WATTS-SEC-006", action)

    def test_latency_beyond_slo_headroom_blocked(self):
        d = self.engine.evaluate(PolicyInput("apply_optimization", slo_headroom_ms=50,
                                             expected_latency_delta_ms=200))
        self.assertEqual(d.rule_id, "WATTS-SEC-007")

    def test_change_requires_human_approval(self):
        d = self.engine.evaluate(PolicyInput("execute_change", roles=("sre",),
                                             requires_human_approval=True, human_approved=False))
        self.assertEqual(d.rule_id, "WATTS-SEC-004")

    def test_change_requires_authorised_role(self):
        d = self.engine.evaluate(PolicyInput("execute_change", roles=("viewer",),
                                             requires_human_approval=False))
        self.assertEqual(d.rule_id, "WATTS-SEC-008")

    def test_cross_tenant_denied(self):
        self.assertFalse(self.engine.evaluate(
            PolicyInput("read_telemetry", cross_tenant=True)).allow)

    def test_explain_lists_every_applicable_rule(self):
        rows = self.engine.explain(PolicyInput("apply_optimization"))
        self.assertTrue(rows)
        self.assertTrue(all("rule_id" in r and "fired" in r for r in rows))


class TestRBAC(unittest.TestCase):
    def test_assistant_cannot_execute_or_approve(self):
        assistant = Principal("watts-assistant", ("watts-assistant",))
        self.assertTrue(RBAC.can(assistant, Permission.PROPOSE_OPTIMIZATION))
        self.assertFalse(RBAC.can(assistant, Permission.EXECUTE_CHANGE))
        self.assertFalse(RBAC.can(assistant, Permission.APPROVE_OPTIMIZATION))

    def test_require_raises_for_missing_permission(self):
        with self.assertRaises(PermissionError):
            RBAC.require(Principal("v", ("viewer",)), Permission.EXECUTE_CHANGE)

    def test_sre_can_approve_and_execute(self):
        sre = Principal("sre:ana", ("sre",))
        self.assertTrue(RBAC.can(sre, Permission.APPROVE_OPTIMIZATION))
        self.assertTrue(RBAC.can(sre, Permission.EXECUTE_CHANGE))


class TestAuditChain(unittest.TestCase):
    def _log(self, n=4):
        log = AuditLog()
        for i in range(n):
            log.append(actor="sre:ana", action="apply", recommendation_id=f"rec-{i}",
                       policy_decision={"allow": True}, change={"step": i}, outcome="ok",
                       timestamp=float(i))
        return log

    def test_chain_verifies(self):
        ok, broken = self._log().verify()
        self.assertTrue(ok)
        self.assertIsNone(broken)

    def test_edited_entry_breaks_the_chain(self):
        log = self._log()
        entries = log.entries()
        object.__setattr__(entries[2], "outcome", "tampered")
        log._entries[2] = entries[2]
        ok, broken = log.verify()
        self.assertFalse(ok)
        self.assertEqual(broken, 2)

    def test_removed_entry_breaks_the_chain(self):
        log = self._log()
        del log._entries[1]
        ok, broken = log.verify()
        self.assertFalse(ok)

    def test_head_advances(self):
        log = self._log(2)
        first = log.head
        log.append(actor="a", action="x", recommendation_id="r", policy_decision={},
                   change={}, outcome="ok", timestamp=9.0)
        self.assertNotEqual(first, log.head)


if __name__ == "__main__":
    unittest.main()
