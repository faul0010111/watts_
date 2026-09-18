import unittest

from conftest import ROOT  # noqa: F401
from services.security import AuditLog, Permission, Principal


def log(n=5, policy_version="watts-policy-1", tenant="acme") -> AuditLog:
    audit = AuditLog(policy_version=policy_version)
    for i in range(n):
        audit.append(actor="sre:ana", action="apply", recommendation_id=f"rec-{i}",
                     policy_decision={"allow": True}, change={"step": i}, outcome="ok",
                     timestamp=float(i), tenant=tenant if i % 2 else "globex")
    return audit


class TestEntryFields(unittest.TestCase):
    def test_entry_carries_tenant_policy_version_and_id(self):
        entry = log(1).entries()[0]
        self.assertTrue(entry.entry_id)
        self.assertEqual(entry.policy_version, "watts-policy-1")
        self.assertEqual(entry.tenant, "globex")

    def test_tenant_and_policy_version_are_inside_the_hash(self):
        audit = log(2)
        entry = audit.entries()[1]
        tampered = type(entry)(**{**entry.__dict__, "tenant": "someone-else", "entry_hash": ""})
        self.assertNotEqual(tampered.compute_hash(), entry.entry_hash)


class TestCheckpoints(unittest.TestCase):
    def test_checkpoint_commits_to_the_head(self):
        audit = log()
        cp = audit.checkpoint(timestamp=100.0)
        self.assertEqual(cp.head, audit.head)
        ok, message = audit.verify_against(cp)
        self.assertTrue(ok, message)

    def test_rewriting_history_breaks_the_published_checkpoint(self):
        audit = log()
        cp = audit.checkpoint(timestamp=100.0)
        entries = audit.entries()
        object.__setattr__(entries[2], "outcome", "tampered")
        audit._entries[2] = entries[2]
        ok, message = audit.verify_against(cp)
        self.assertFalse(ok)
        self.assertIn("chain broken", message)

    def test_checkpoint_from_another_log_does_not_match(self):
        cp = log().checkpoint(timestamp=100.0)
        ok, message = log(tenant="other").verify_against(cp)
        self.assertFalse(ok)
        self.assertIn("committed to", message)


class TestRetention(unittest.TestCase):
    def test_pruning_leaves_a_sealed_record_that_history_existed(self):
        audit = log(6)
        segment = audit.prune(before_timestamp=3.0)
        self.assertIsNotNone(segment)
        self.assertEqual(segment.entries, 3)
        self.assertEqual(len(audit), 3)

    def test_chain_still_verifies_from_the_seal(self):
        audit = log(6)
        audit.prune(before_timestamp=3.0)
        ok, broken = audit.verify()
        self.assertTrue(ok, broken)

    def test_appending_after_a_prune_continues_the_chain(self):
        audit = log(6)
        audit.prune(before_timestamp=3.0)
        entry = audit.append(actor="sre:ana", action="apply", recommendation_id="rec-9",
                             policy_decision={}, change={}, outcome="ok", timestamp=9.0)
        self.assertEqual(entry.index, 6)
        self.assertTrue(audit.verify()[0])

    def test_pruning_nothing_returns_nothing(self):
        self.assertIsNone(log(3).prune(before_timestamp=-1.0))

    def test_checkpoint_inside_a_sealed_range_is_reported_honestly(self):
        audit = log(6)
        cp = audit.checkpoint(timestamp=1.0)
        audit.prune(before_timestamp=6.0)
        ok, message = audit.verify_against(cp)
        self.assertFalse(ok)


class TestAccessAndExport(unittest.TestCase):
    def test_read_is_tenant_scopable(self):
        rows = log(6).read(tenant="acme")
        self.assertTrue(rows)
        self.assertTrue(all(r.tenant == "acme" for r in rows))

    def test_export_requires_the_audit_permission(self):
        with self.assertRaises(PermissionError):
            log().export(principal=Principal("p", ("watts-assistant",)))

    def test_security_role_may_export(self):
        export = log().export(principal=Principal("sec", ("security",)))
        self.assertTrue(export["chain_valid"])

    def test_export_carries_its_own_verification_state(self):
        export = log().export()
        for key in ("chain_valid", "head", "policy_version", "checkpoints", "sealed_segments"):
            self.assertIn(key, export)

    def test_export_states_that_chaining_is_not_immutable_storage(self):
        self.assertIn("not immutable storage", log().export()["note"])

    def test_broken_chain_is_exported_as_broken(self):
        audit = log()
        entries = audit.entries()
        object.__setattr__(entries[1], "outcome", "tampered")
        audit._entries[1] = entries[1]
        export = audit.export()
        self.assertFalse(export["chain_valid"])
        self.assertEqual(export["first_broken_index"], 1)


if __name__ == "__main__":
    unittest.main()
