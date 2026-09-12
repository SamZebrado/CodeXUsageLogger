import json
import os
import plistlib
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from codex_quota_logger.runtime import *
from codex_quota_logger.cli import main, status
from codex_quota_logger import launchd
from codex_quota_logger.storage import StorageError
from test_model import wire

class ScheduleTests(unittest.TestCase):
    def test_startup(self): self.assertEqual(Schedule().due(0), "startup")
    def test_unchanged_poll_every_five_minutes(self):
        s = Schedule(); s.success(0, "startup")
        self.assertIsNone(s.due(299)); self.assertEqual(s.due(300), "poll")
    def test_hourly_heartbeat_not_delayed_by_changes(self):
        s = Schedule(); s.success(0, "startup")
        for n in range(300, 3600, 300): s.success(n, "poll")
        self.assertEqual(s.due(3600), "heartbeat")
    def test_notification_debounce(self):
        s = Schedule(); s.success(0, "startup")
        self.assertIsNone(s.due(1, True)); self.assertEqual(s.due(2, True), "notification")
    def test_sleep_wake(self):
        s = Schedule(); s.success(0, "startup"); self.assertEqual(s.due(700), "after_gap")
    def test_clock_backwards(self):
        s = Schedule(); s.success(30, "startup"); self.assertEqual(s.due(29), "clock_change")
    def test_backoff_bounded(self):
        self.assertEqual(backoff_seconds(0, False), 15)
        self.assertEqual(backoff_seconds(10000, False), 900)
    def test_invalid_settings(self):
        for kw in ({"poll_seconds": 0}, {"cap_bytes": 1}, {"retention_days": 0}, {"request_timeout": .5}):
            with self.assertRaises(ValueError): Settings(Path("/tmp/test"), **kw).validate()

class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.fake = self.root/"fake codex"; shutil.copy(Path(__file__).with_name("fake_codex.py"), self.fake); self.fake.chmod(0o700)
        self.cfg = Settings(self.root/"data", codex=str(self.fake))
    def tearDown(self): self.tmp.cleanup()
    def test_live_synthetic_doctor_exact_one_read(self):
        log = self.root/"transcript.jsonl"
        with patch("codex_quota_logger.runtime.child_env", return_value=dict(os.environ, CQL_FAKE_LOG=str(log))):
            r = one_shot(self.cfg, inspect=True)
        self.assertEqual(r["schema"]["status"], "inspected")
        self.assertEqual(r["outgoing_methods"]["account/rateLimits/read"], 1)
        self.assertEqual(r["model_turn_methods_sent"], 0)
        self.assertFalse((self.root/"data").exists())
        self.assertNotIn("SECRET", json.dumps(r)); self.assertNotIn("example.invalid", json.dumps(r))
    def test_status_does_not_find_or_start_codex(self):
        with patch("codex_quota_logger.runtime.resolve_codex", side_effect=AssertionError("must not start")):
            r = status(self.cfg)
        self.assertFalse(r["writer_lock_held"])
    def test_plist_no_shell_and_space_safe(self):
        launcher = self.root/"source with spaces"/"quota_logger.py"; launcher.parent.mkdir(); launcher.write_text("# test")
        p = launchd.make_plist(self.cfg, launcher)
        args = p["ProgramArguments"]
        self.assertIn(str(launcher), args); self.assertNotIn("sh", args)
        self.assertEqual(p["StandardErrorPath"], "/dev/null")
        self.assertEqual(plistlib.loads(plistlib.dumps(p)), p)
    def test_plist_uses_installed_module_without_launcher(self):
        p = launchd.make_plist(self.cfg, self.root/"absent.py")
        self.assertIn("-m", p["ProgramArguments"])
    def test_plist_no_credentials(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "SECRET", "CODEX_ACCESS_TOKEN": "SECRET"}):
            p = launchd.make_plist(self.cfg, self.root/"absent.py")
        self.assertNotIn("SECRET", repr(p))
    def test_no_install_without_approval(self):
        with patch("codex_quota_logger.launchd.check_mac"):
            with self.assertRaisesRegex(StorageError, "approval_required"):
                launchd.install(self.cfg, self.root/"x", approved=False)
    def test_install_only_writes_plist(self):
        with patch("codex_quota_logger.launchd.check_mac"), patch("codex_quota_logger.launchd.plist_path", return_value=self.root/"agent.plist"), patch("codex_quota_logger.launchd._ctl", side_effect=AssertionError("no launchctl")):
            r = launchd.install(self.cfg, self.root/"x", approved=True)
        self.assertFalse(r["loaded"])
        self.assertTrue((self.root/"agent.plist").exists())
    def test_child_env_excludes_credentials(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "SECRET", "CODEX_ACCESS_TOKEN": "SECRET"}):
            self.assertNotIn("SECRET", repr(child_env()))

class DaemonTests(unittest.TestCase):
    def test_failed_server_restarts_and_records(self):
        with tempfile.TemporaryDirectory() as td:
            d = Daemon(Settings(Path(td)/"data"))
            starts = []
            class Fake:
                def __init__(self, *a, **k):
                    self.quota_dirty = self.auth_dirty = False
                    self.next_id = 4; self.notification_count = 0
                def __enter__(self):
                    starts.append(1)
                    if len(starts) == 1: raise ProtocolError("synthetic_first_crash")
                    return self
                def __exit__(self, *a): pass
                def pump(self, **kw): d.stop_requested = True
                def request(self, method, params=None):
                    if method == "account/read": return {"account": {"type": "chatgpt", "planType": "pro"}}
                    return wire()
            with patch("codex_quota_logger.runtime.ReadOnlyClient", Fake), patch("codex_quota_logger.runtime.resolve_codex", return_value="fake"), patch("codex_quota_logger.runtime.codex_version", return_value="synthetic"), patch.object(d, "_sleep"):
                self.assertEqual(d._run(), 0)
            self.assertEqual(len(starts), 2)
            self.assertTrue((Path(td)/"data"/"quota_history.csv").exists())
            h = json.loads((Path(td)/"data"/"health.json").read_text())
            self.assertEqual(h["state"], "stopped")
            self.assertIsNotNone(h["last_successful_read"])

class EndToEndPersistenceTests(unittest.TestCase):
    def exercise(self, optional=False):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = root/"fake codex"; shutil.copy(Path(__file__).with_name("fake_codex.py"), fake); fake.chmod(0o700)
            d = Daemon(Settings(root/"data", codex=str(fake), usage=optional))
            actual = ReadOnlyClient
            class OneCycle(actual):
                def pump(self, timeout=0):
                    if self._waiting_id is None:
                        d.stop_requested = True
                        return
                    return super().pump(timeout)
            env = dict(os.environ, CQL_FAKE_LOG=str(root/"transcript.jsonl"), CQL_FAKE_MODE="unsupported_usage" if optional else "normal")
            with patch("codex_quota_logger.runtime.ReadOnlyClient", OneCycle), patch("codex_quota_logger.runtime.child_env", return_value=env):
                self.assertEqual(d._run(), 0)
            data = b"\n".join(p.read_bytes() for p in (root/"data").rglob("*") if p.is_file() and p.name != "identity.salt")
            for secret in (b"SECRET_TOKEN", b"synthetic@example.invalid", b"synthetic-account-id", b"accessToken"):
                self.assertNotIn(secret, data)
            self.assertIn(b"quota_snapshot", data)
            self.assertIn(b"primary_remaining_percent", data)
            methods = [json.loads(line)["method"] for line in (root/"transcript.jsonl").read_text().splitlines()]
            self.assertEqual(methods.count("account/rateLimits/read"), 1)
            self.assertEqual(methods.count("account/usage/read"), int(optional))
            self.assertNotIn("turn/start", methods)
            return data
    def test_real_fake_server_persistence_no_identity_or_credentials(self): self.exercise()
    def test_optional_failure_does_not_block_core_logger(self):
        self.assertIn(b"optional_usage_disabled", self.exercise(True))
