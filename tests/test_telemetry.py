import time
import unittest

from conftest import ROOT  # noqa: F401
from services.telemetry import (
    PROHIBITED_FIELDS, PrivacyViolation, RejectReason, RequestRecord, TelemetryGateway,
    stable_hash,
)

KEY = b"test-key"


def record(**overrides) -> RequestRecord:
    base = dict(request_id="r-1", model="m", provider="p", input_tokens=1000,
                output_tokens=100, latency_ms=800.0, gpu_id="gpu-0", tenant="t1",
                timestamp=time.time(), energy_wh=0.5)
    base.update(overrides)
    return RequestRecord(**base)


class TestPrivacy(unittest.TestCase):
    def test_prompt_metadata_rejected(self):
        for field in ("prompt", "messages", "api_key", "password", "content"):
            self.assertIn(field, PROHIBITED_FIELDS)
            with self.assertRaises(PrivacyViolation):
                record(metadata={field: "x"})

    def test_credential_shaped_value_rejected(self):
        with self.assertRaises(PrivacyViolation):
            record(metadata={"note": "sk-abcdefghijklmnopqrstuvwxyz"})

    def test_long_value_rejected(self):
        with self.assertRaises(PrivacyViolation):
            record(metadata={"note": "x" * 600})

    def test_nested_value_rejected(self):
        with self.assertRaises(PrivacyViolation):
            record(metadata={"note": {"a": 1}})

    def test_scalar_metadata_allowed(self):
        r = record(metadata={"region": "eu-west-1", "retries": 2})
        self.assertEqual(r.metadata["region"], "eu-west-1")

    def test_hash_is_salted_and_stable(self):
        self.assertEqual(stable_hash("abc", "s1"), stable_hash("abc", "s1"))
        self.assertNotEqual(stable_hash("abc", "s1"), stable_hash("abc", "s2"))


class TestGateway(unittest.TestCase):
    def setUp(self):
        self.gw = TelemetryGateway({"w": KEY})

    def test_valid_record_accepted(self):
        r = record()
        self.assertTrue(self.gw.ingest(r, r.signature(KEY), "w").accepted)

    def test_bad_signature_rejected(self):
        r = record()
        result = self.gw.ingest(r, r.signature(b"other"), "w")
        self.assertEqual(result.reason, RejectReason.BAD_SIGNATURE)

    def test_unknown_workload_rejected(self):
        r = record()
        self.assertEqual(self.gw.ingest(r, r.signature(KEY), "nope").reason,
                         RejectReason.UNKNOWN_WORKLOAD)

    def test_replay_rejected(self):
        r = record()
        self.gw.ingest(r, r.signature(KEY), "w")
        self.assertEqual(self.gw.ingest(r, r.signature(KEY), "w").reason, RejectReason.REPLAY)

    def test_clock_skew_rejected(self):
        r = record(timestamp=time.time() - 10_000)
        self.assertEqual(self.gw.ingest(r, r.signature(KEY), "w").reason, RejectReason.CLOCK_SKEW)

    def test_impossibly_cheap_energy_rejected(self):
        """A workload cannot win the efficiency leaderboard by under-reporting energy."""
        r = record(request_id="r-cheap", energy_wh=1e-12)
        self.assertEqual(self.gw.ingest(r, r.signature(KEY), "w").reason,
                         RejectReason.IMPLAUSIBLE_ENERGY)

    def test_input_beyond_context_window_rejected(self):
        r = record(request_id="r-ctx", input_tokens=9000, context_window=4096)
        self.assertEqual(self.gw.ingest(r, r.signature(KEY), "w").reason,
                         RejectReason.IMPLAUSIBLE_TOKENS)

    def test_stats_count_rejections(self):
        r = record()
        self.gw.ingest(r, "bad", "w")
        self.assertEqual(self.gw.stats()["rejected"], 1)


if __name__ == "__main__":
    unittest.main()
