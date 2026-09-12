import copy
import json
import unittest
from datetime import datetime, timezone
from codex_quota_logger.model import *

NOW = datetime(2026, 9, 12, 4, 0, tzinfo=timezone.utc)

def wire(used=20):
    return {"rateLimits": {"limitId": "codex", "planType": "pro", "primary": {"usedPercent": used, "windowDurationMins": 300, "resetsAt": 1790000000},
                          "secondary": {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": 1790300000}},
            "rateLimitResetCredits": {"availableCount": 3, "credits": []}}

def snap(value=None):
    return normalize(project_quota(value if value is not None else wire()), {"account_fingerprint": "abc", "auth_mode": "chatgpt", "plan_type": "pro"})

class ProjectionTests(unittest.TestCase):
    def test_decimal_percent(self):
        rows = Tracker().observe(snap(wire(20.25)), NOW)
        self.assertEqual(rows[0]["primary_remaining_percent"], 79.75)
    def test_missing_null(self):
        b = project_quota({"rateLimits": {}})["rateLimits"]
        self.assertIsNone(b["primary"])
        self.assertIsNone(b["credits"])
    def test_no_bool_numbers(self):
        self.assertIsNone(number(True))
        with self.assertRaises(ShapeError): project_quota(wire(True))
    def test_no_nonfinite(self):
        for n in (float("nan"), float("inf")):
            with self.assertRaises(ShapeError): project_quota(wire(n))
    def test_invalid_shapes(self):
        for obj in (None, [], {}, {"rateLimits": 3}, {"rateLimitsByLimitId": []}):
            with self.assertRaises(ShapeError): project_quota(obj)
    def test_all_buckets(self):
        v = wire(); v["rateLimitsByLimitId"] = {"codex": v["rateLimits"], "spark": {"limitId": "spark"}}
        self.assertEqual(set(snap(v)["buckets"]), {"codex", "spark"})
    def test_legacy_no_duplicate(self):
        v = wire(); v["rateLimits"]["limitId"] = None
        v["rateLimitsByLimitId"] = {"codex": copy.deepcopy(v["rateLimits"])}
        self.assertEqual(list(snap(v)["buckets"]), ["codex"])
    def test_legacy_distinct_retained(self):
        v = wire(); v["rateLimits"]["limitId"] = None
        v["rateLimitsByLimitId"] = {"spark": {"limitId": "spark"}}
        self.assertEqual(set(snap(v)["buckets"]), {"spark", "legacy_default"})
    def test_reset_count_authoritative(self):
        self.assertEqual(Tracker().observe(snap(), NOW)[0]["earned_reset_count"], 3)
    def test_secret_fields_removed(self):
        v = wire(); v.update(accessToken="SECRET", email="name@example.com", accountId="PRIVATE", rateLimitUpsell={"cookie": "SECRET"})
        v["rateLimits"].update(password="SECRET", limitName="name@example.com")
        v["rateLimitResetCredits"]["credits"] = [{"id": "PRIVATE", "description": "SECRET", "status": "available", "expiresAt": 200}]
        s = json.dumps(project_quota(v))
        for x in ("SECRET", "PRIVATE", "example.com", "password", "cookie", "accessToken"):
            self.assertNotIn(x, s)
    def test_sensitive_label_rejected(self):
        for s in ("Bearer token", "sk-secret", "name@example.com", "https://abc", "eyJxxxxxxxx", "=SUM(A1)"):
            self.assertIsNone(label(s))
    def test_decimal_balance(self):
        v = wire(); v["rateLimits"]["credits"] = {"balance": "123.456", "hasCredits": True}
        self.assertEqual(project_quota(v)["rateLimits"]["credits"]["balance"], "123.456")
    def test_decimal_balance_injection(self):
        self.assertIsNone(decimal_string("=CMD()"))
        self.assertIsNone(decimal_string(float("inf")))
    def test_identity_salted(self):
        a = {"account": {"email": "some@example.com", "type": "chatgpt"}}
        c1 = account_context(a, {}, b"1"*32); c2 = account_context(a, {}, b"2"*32)
        self.assertNotEqual(c1["account_fingerprint"], c2["account_fingerprint"])
        self.assertNotIn("example.com", json.dumps(c1))
    def test_empty_map(self):
        self.assertEqual(normalize(project_quota({"rateLimitsByLimitId": {}}), {})["buckets"], {})
    def test_unsafe_bucket_key(self):
        with self.assertRaises(ShapeError): project_quota({"rateLimitsByLimitId": {"person@example.com": {}}})
    def test_usage_projection(self):
        p = project_usage({"summary": {"lifetimeTokens": 345, "secret": "HIDE"}, "dailyUsageBuckets": [{"startDate": "2026-09-12", "tokens": 12}]})
        self.assertNotIn("HIDE", json.dumps(p)); self.assertEqual(p["dailyUsageBuckets"][0]["tokens"], 12)
    def test_usage_nulls(self):
        self.assertIsNone(project_usage({"summary": None, "dailyUsageBuckets": None})["dailyUsageBuckets"])
    def test_usage_bad_date(self):
        with self.assertRaises(ShapeError): project_usage({"summary": {}, "dailyUsageBuckets": [{"startDate": "SECRET", "tokens": 1}]})

class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.t = Tracker(); self.s = snap(); self.a = self.t.observe(self.s, NOW)[0]
    def row(self, s): return self.t.observe(s, NOW)[0]
    def test_deduplicate(self): self.assertEqual(self.t.observe(copy.deepcopy(self.s), NOW), [])
    def test_change(self): self.assertEqual(self.row(snap(wire(21)))["event_type"], "change")
    def test_heartbeat_even_unchanged(self):
        self.assertIn("heartbeat", self.t.observe(self.s, NOW, trigger="heartbeat", force=True)[0]["event_type"])
    def test_primary_rollover_keeps_weekly_id(self):
        s = copy.deepcopy(self.s); s["buckets"]["codex"]["primary"]["resetsAt"] += 300
        b = self.row(s)
        self.assertNotEqual(self.a["primary_interval_id"], b["primary_interval_id"])
        self.assertEqual(self.a["secondary_interval_id"], b["secondary_interval_id"])
    def test_duration_change(self):
        s = copy.deepcopy(self.s); s["buckets"]["codex"]["secondary"]["windowDurationMins"] = 9000
        self.assertIn("window_change", self.row(s)["event_type"])
    def test_unexpected_decrease(self):
        b = self.row(snap(wire(19)))
        self.assertIn("possible_reset_or_correction", b["event_type"])
        self.assertNotEqual(self.a["primary_interval_id"], b["primary_interval_id"])
        self.assertNotIn("delta", b)
    def test_plan_change(self):
        s = copy.deepcopy(self.s); s["buckets"]["codex"]["planType"] = "business"
        b = self.row(s)
        self.assertIn("plan_or_limit_change", b["event_type"])
        self.assertNotEqual(self.a["secondary_interval_id"], b["secondary_interval_id"])
    def test_account_change(self):
        s = copy.deepcopy(self.s); s["context"]["account_fingerprint"] = "def"
        self.assertIn("account_change", self.row(s)["event_type"])
    def test_bucket_removed(self):
        s = copy.deepcopy(self.s); s["buckets"] = {}
        b = self.row(s); self.assertFalse(b["bucket_present"])
        self.assertIn("bucket_disappeared", b["event_type"])
    def test_bucket_added(self):
        s = copy.deepcopy(self.s); s["buckets"]["spark"] = copy.deepcopy(s["buckets"]["codex"])
        rows = self.t.observe(s, NOW)
        self.assertIn("bucket_appeared", rows[1]["event_type"])
    def test_out_of_range_not_clamped(self):
        b = self.row(snap(wire(102)))
        self.assertEqual(b["primary_remaining_percent"], -2)
        self.assertIn("anomaly", b["event_type"])
    def test_no_permission_inference(self):
        s = snap(wire(100)); b = self.row(s)
        self.assertNotIn("limit_reached", b["event_type"])
        self.assertIsNone(b["ordinary_usage_allowed"])
    def test_explicit_block_and_recovery(self):
        s = copy.deepcopy(self.s); s["ordinary_usage_allowed"] = False
        self.assertIn("limit_reached", self.row(s)["event_type"])
        s["ordinary_usage_allowed"] = True
        self.assertIn("usage_allowed", self.row(s)["event_type"])
    def test_window_null_opens_interval(self):
        s = copy.deepcopy(self.s); s["buckets"]["codex"]["primary"] = None
        self.assertIsNone(self.row(s)["primary_interval_id"])
    def test_gap_opens_all_intervals(self):
        b = self.t.observe(self.s, NOW, gap=True)[0]
        self.assertIn("telemetry_recovered", b["event_type"])
        self.assertNotEqual(self.a["secondary_interval_id"], b["secondary_interval_id"])
    def test_timezone(self):
        self.assertTrue(self.a["timestamp_local"].endswith("+08:00"))
    def test_mutable_input_not_retained(self):
        self.s["buckets"]["codex"]["primary"]["usedPercent"] = 35
        self.assertTrue(self.t.observe(self.s, NOW))
    def test_reset_credit_change(self):
        s = copy.deepcopy(self.s); s["reset_summary"]["availableCount"] = 4
        self.assertIn("reset_credit_change", self.row(s)["event_type"])
