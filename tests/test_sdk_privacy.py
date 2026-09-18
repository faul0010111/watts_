import unittest
import warnings

from conftest import ROOT  # noqa: F401
from sdk.llm_telemetry.client import PrivacyMode, TelemetryConfig, WattsTelemetry
from services.telemetry.schema import PrivacyViolation

SENT: list[dict] = []


def transport(payload: dict) -> None:
    SENT.append(payload)


def client(**kw) -> WattsTelemetry:
    kw.setdefault("hash_salt", "s3cret")
    return WattsTelemetry(TelemetryConfig(workload="w", tenant="acme", **kw), transport)


class TestDefaults(unittest.TestCase):
    def setUp(self):
        SENT.clear()

    def test_strict_is_the_default(self):
        self.assertIs(client().config.privacy_mode, PrivacyMode.STRICT)

    def test_missing_salt_warns_loudly(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            TelemetryConfig(workload="w", tenant="acme")
        self.assertTrue(any("hash_salt" in str(c.message) for c in caught))

    def test_mode_accepts_its_string_form(self):
        self.assertIs(client(privacy_mode="no-hashes").config.privacy_mode, PrivacyMode.NO_HASHES)

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            TelemetryConfig(workload="w", hash_salt="s", privacy_mode="send-everything")


class TestWhatLeavesTheProcess(unittest.TestCase):
    def setUp(self):
        SENT.clear()

    def _send(self, watts, **span_calls):
        with watts.track(model="m", provider="p", task_class="classification") as span:
            span.set_tokens(1000, 100)
            if span_calls.get("identity"):
                span.set_prompt_identity("a real prompt", "a shared prefix")
            if span_calls.get("labels"):
                span.set_labels(**span_calls["labels"])
        return SENT[-1]

    def test_no_field_can_carry_prompt_text(self):
        payload = self._send(client(), identity=True)
        record = payload.get("record", payload)
        self.assertNotIn("prompt", record)
        self.assertNotIn("completion", record)
        for value in record.values():
            self.assertNotEqual(value, "a real prompt")

    def test_strict_sends_hashes_not_content(self):
        record = self._send(client(), identity=True)
        record = record.get("record", record)
        self.assertTrue(record["prompt_hash"])
        self.assertNotEqual(record["prompt_hash"], "a real prompt")

    def test_no_hashes_mode_derives_nothing_from_the_prompt(self):
        record = self._send(client(privacy_mode=PrivacyMode.NO_HASHES), identity=True)
        record = record.get("record", record)
        self.assertIsNone(record["prompt_hash"])
        self.assertIsNone(record["context_prefix_hash"])

    def test_labels_require_the_metadata_mode(self):
        with self.assertRaises(PrivacyViolation):
            self._send(client(), labels={"region": "eu"})

    def test_metadata_mode_permits_scalar_labels(self):
        record = self._send(client(privacy_mode=PrivacyMode.METADATA), labels={"region": "eu"})
        record = record.get("record", record)
        self.assertEqual(record["metadata"]["region"], "eu")

    def test_metadata_mode_still_refuses_credential_shaped_values(self):
        with self.assertRaises(PrivacyViolation):
            self._send(client(privacy_mode=PrivacyMode.METADATA),
                       labels={"token": "sk-live-abcdefghijklmnopqrstuvwxyz123456"})

    def test_salt_changes_the_hash(self):
        a = self._send(client(hash_salt="one"), identity=True)
        b = self._send(client(hash_salt="two"), identity=True)
        self.assertNotEqual(a.get("record", a)["prompt_hash"], b.get("record", b)["prompt_hash"])


class TestTypescriptMirror(unittest.TestCase):
    def test_typescript_sdk_declares_the_same_modes(self):
        source = (ROOT / "sdk" / "ts" / "watts-telemetry.ts").read_text()
        for mode in ("strict", "metadata", "no-hashes"):
            self.assertIn(f'"{mode}"', source)
        self.assertIn("privacyMode", source)


if __name__ == "__main__":
    unittest.main()
