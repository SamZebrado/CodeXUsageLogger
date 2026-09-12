import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from codex_quota_logger.protocol import *
from codex_quota_logger.runtime import read_snapshot

FAKE = Path(__file__).with_name("fake_codex.py").resolve()

class ProtocolTests(unittest.TestCase):
    def setUp(self): self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def client(self, mode="normal", **kwargs):
        env = dict(os.environ, CQL_FAKE_MODE=mode, CQL_FAKE_LOG=str(self.root/"rpc.jsonl"))
        return ReadOnlyClient([sys.executable, "-B", str(FAKE), "app-server"], self.root, env=env, **kwargs)
    def transcript(self): return [json.loads(x) for x in (self.root/"rpc.jsonl").read_text().splitlines()]
    def test_handshake_and_one_quota_call(self):
        with self.client() as c:
            s, p = read_snapshot(c, b"x"*32)
            self.assertEqual(s["buckets"]["codex"]["secondary"]["usedPercent"], 25)
        self.assertEqual([x["method"] for x in self.transcript()], ["initialize", "initialized", "account/read", "account/rateLimits/read"])
        self.assertEqual(self.transcript()[2]["params"], {"refreshToken": False})
    def test_forbidden_methods(self):
        with self.client() as c:
            for m in ("thread/start", "turn/start", "thread/resume", "review/start", "account/rateLimitResetCredit/consume", "account/logout", "account/login/start"):
                with self.assertRaises(ProtocolError): c.request(m)
        self.assertEqual(len(self.transcript()), 2)
    def test_forbidden_notification(self):
        with self.client() as c:
            with self.assertRaises(ProtocolError): c.notify("turn/start")
    def test_forced_refresh_refused(self):
        with self.client() as c:
            with self.assertRaises(ProtocolError): c.request("account/read", {"refreshToken": True})
    def test_interleaving_and_wrong_id(self):
        with self.client("notices") as c:
            s, _ = read_snapshot(c, b"x"*32)
            self.assertEqual(s["buckets"]["codex"]["primary"]["usedPercent"], 10)
            self.assertTrue(c.quota_dirty)
    def test_sparse_hint_does_not_erase_weekly(self):
        with self.client("notices") as c:
            s, _ = read_snapshot(c, b"x"*32)
            c.quota_dirty = False
            s2, _ = read_snapshot(c, b"x"*32)
            self.assertEqual(s, s2)
            self.assertFalse(c.quota_dirty)  # same hint cannot cause a feedback storm
    def test_partial_message(self):
        with self.client("partial") as c:
            s, _ = read_snapshot(c, b"x"*32)
            self.assertIn("codex", s["buckets"])
    def test_malformed_json(self):
        with self.client("bad_json") as c:
            with self.assertRaisesRegex(ProtocolError, "invalid_json"): c.request("account/rateLimits/read")
    def test_oversized_line(self):
        with self.client("oversize", max_message=2048) as c:
            with self.assertRaisesRegex(ProtocolError, "message_too_large"): c.request("account/rateLimits/read")
    def test_eof(self):
        with self.client("exit") as c:
            with self.assertRaisesRegex(ProtocolError, "server_eof"): c.request("account/rateLimits/read")
    def test_request_timeout(self):
        with self.client("timeout", timeout=5) as c:
            c.timeout = .2
            with self.assertRaisesRegex(ProtocolError, "request_timeout"): c.request("account/rateLimits/read")
    def test_no_server_approvals(self):
        with self.client("request") as c:
            with self.assertRaisesRegex(ProtocolError, "unexpected_server_request"): c.request("account/rateLimits/read")
        self.assertFalse(any("result" in m for m in self.transcript()))
    def test_errors_discard_payload(self):
        with self.client("error") as c:
            with self.assertRaises(RpcError) as error: c.request("account/rateLimits/read")
            self.assertNotIn("SECRET", str(error.exception))
            self.assertEqual(error.exception.code, -32000)
    def test_optional_usage_unsupported(self):
        with self.client("unsupported_usage") as c:
            with self.assertRaises(RpcError) as error: c.request("account/usage/read")
            self.assertEqual(error.exception.code, -32601)
            self.assertIn("rateLimits", c.request("account/rateLimits/read"))
    def test_child_terminated(self):
        with self.client() as c: p = c.proc
        self.assertIsNotNone(p.poll())
    def test_no_unrelated_notifications_persisted(self):
        with self.client("notices") as c:
            s, p = read_snapshot(c, b"x"*32)
            self.assertNotIn("SECRET", json.dumps([s, p]))
