import csv
import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from codex_quota_logger.storage import *
from test_model import snap
from codex_quota_logger.model import Tracker

NOW = datetime(2026, 9, 12, 4, tzinfo=timezone.utc)

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name) / "data"
        self.s = Store(self.root, cap=300000); self.s.acquire()
    def tearDown(self): self.s.release(); self.tmp.cleanup()
    def put(self, name, size):
        p = self.s.raw / name; p.write_bytes(b"x"*size); return p
    def test_lock(self):
        self.assertTrue(is_locked(self.root))
        s2 = Store(self.root)
        with self.assertRaises(StorageError): s2.acquire()
    def test_lock_release(self):
        self.s.release(); self.assertFalse(is_locked(self.root))
    def test_lock_required(self):
        self.s.release()
        with self.assertRaises(StorageError): self.s.ensure(0)
    def test_directory_permissions(self): self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
    def test_file_permissions(self):
        self.s.operation("test", NOW)
        self.assertEqual((self.root/"operations.jsonl").stat().st_mode & 0o777, 0o600)
    def test_csv_roundtrip(self):
        rows = Tracker().observe(snap(), NOW)
        rows[0]["notes"] = 'quoted, "field"'
        self.s.write_rows(rows)
        with self.s.csv_path.open() as f:
            actual = list(csv.DictReader(f))
        self.assertEqual(actual[0]["notes"], rows[0]["notes"])
    def test_csv_no_duplicate_header(self):
        rows = Tracker().observe(snap(), NOW)
        self.s.write_rows(rows); self.s.write_rows(rows)
        self.assertEqual(self.s.csv_path.read_text().count("schema_version"), 1)
    def test_partial_tail_preserved(self):
        self.s.write_rows(Tracker().observe(snap(), NOW))
        with self.s.csv_path.open("ab") as f: f.write(b"partial")
        before = self.s.csv_path.read_bytes()
        with self.assertRaises(StorageError): self.s.write_rows(Tracker().observe(snap(), NOW))
        self.assertEqual(before, self.s.csv_path.read_bytes())
    def test_schema_mismatch(self):
        self.s.csv_path.write_text("bad,header\n")
        with self.assertRaises(StorageError): self.s.validate_csv()
    def test_salt_stable(self): self.assertEqual(self.s.salt(), self.s.salt())
    def test_raw_date_rotation(self):
        self.s.evidence({"quota": 1}, NOW)
        self.assertTrue((self.s.raw/"2026-09-12.jsonl").exists())
    def test_gzip_content(self):
        p = self.s.raw/"2026-09-11.jsonl"; p.write_bytes(b'{"synthetic":true}\n'*100)
        before = p.read_bytes(); self.s.housekeeping(NOW)
        self.assertEqual(gzip.decompress(p.with_suffix(".jsonl.gz").read_bytes()), before)
        self.assertFalse(p.exists())
    def test_no_gzip_current(self):
        p = self.put("2026-09-12.jsonl", 100)
        self.s.housekeeping(NOW); self.assertTrue(p.exists())
    def test_30_calendar_days(self):
        gone = self.put("2026-08-13.jsonl.gz", 10)
        keep = self.put("2026-08-14.jsonl.gz", 10)
        self.s.housekeeping(NOW)
        self.assertFalse(gone.exists()); self.assertTrue(keep.exists())
    def test_cap_oldest_first(self):
        a = self.put("2026-09-10.jsonl", 100000); b = self.put("2026-09-11.jsonl", 100000)
        self.s.ensure(100000)
        self.assertFalse(a.exists()); self.assertTrue(b.exists())
    def test_cap_preserves_csv(self):
        self.s.csv_path.write_bytes(b"x"*265000)
        original = self.s.csv_path.read_bytes()
        with self.assertRaises(StorageFull): self.s.ensure(1000)
        self.assertEqual(original, self.s.csv_path.read_bytes())
    def test_cap_preserves_unknown_files(self):
        p = self.root/"do-not-delete.bin"; p.write_bytes(b"x"*270000)
        with self.assertRaises(StorageFull): self.s.ensure(1000)
        self.assertTrue(p.exists())
    def test_every_append_stays_in_cap(self):
        for _ in range(20):
            self.s.evidence({"payload": "x"*20000}, NOW)
            self.assertLessEqual(self.s.size(), self.s.cap)
    def test_gzip_space_admission(self):
        p = self.put("2026-09-11.jsonl", 200000)
        self.s.csv_path.write_bytes(b"z"*60000)
        self.s.housekeeping(NOW)
        self.assertLessEqual(self.s.size(), self.s.cap)
        self.assertTrue(p.exists())
    def test_unknown_raw_file_not_deleted(self):
        p = self.s.raw/"notes.txt"; p.write_text("preserve")
        self.s.housekeeping(NOW); self.assertTrue(p.exists())
    def test_symlink_refused(self):
        target = Path(self.tmp.name)/"outside"; target.write_text("do not change")
        (self.s.raw/"2026-09-01.jsonl").symlink_to(target)
        with self.assertRaises(StorageError): self.s.housekeeping(NOW)
        self.assertEqual(target.read_text(), "do not change")
    def test_hardlink_refused(self):
        p = self.root/"outside"; p.write_text("data")
        os.link(p, self.s.raw/"2026-09-01.jsonl")
        with self.assertRaises(StorageError): self.s.size()
    def test_symlink_root_refused(self):
        alias = Path(self.tmp.name)/"alias"; alias.symlink_to(self.root)
        with self.assertRaises(StorageError): Store(alias)
    def test_atomic_state(self):
        self.s.atomic("health.json", {"state": "running"})
        self.assertEqual(json.loads((self.root/"health.json").read_text())["state"], "running")
        self.assertFalse((self.root/".health.json.tmp").exists())
    def test_operational_logs_rotate(self):
        for _ in range(400): self.s.operation("read_failed", NOW)
        self.assertTrue((self.root/"operations.1.jsonl").exists())
        self.assertLessEqual(self.s.size(), self.s.cap)
    def test_operational_error_no_arbitrary_content(self):
        self.s.operation("Bearer SECRET_TOKEN", NOW)
        self.assertNotIn("SECRET", (self.root/"operations.jsonl").read_text())
