"""Scheduling and read-only sampling. Polling and persistence are independent."""
from __future__ import annotations

import copy
import json
import os
import random
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .model import Tracker, ShapeError, account_context, normalize, project_quota, project_usage, label
from .protocol import ReadOnlyClient, RpcError, ProtocolError
from .storage import Store, StorageError, StorageFull, regular


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    codex: str = "codex"
    timezone: str = "Asia/Shanghai"
    poll_seconds: int = 300
    heartbeat_seconds: int = 3600
    cap_bytes: int = 250_000_000
    retention_days: int = 30
    usage: bool = False
    request_timeout: float = 30

    def validate(self):
        ZoneInfo(self.timezone)
        if self.poll_seconds < 30 or self.heartbeat_seconds < self.poll_seconds:
            raise ValueError("invalid_poll_or_heartbeat_interval")
        if self.cap_bytes < 65536 or not 1 <= self.retention_days <= 365:
            raise ValueError("invalid_storage_policy")
        if not 1 <= self.request_timeout <= 120:
            raise ValueError("invalid_request_timeout")
        return self


def resolve_codex(value):
    path = shutil.which(value)
    if not path:
        raise ProtocolError("codex_executable_not_found")
    # Keep the executable path, not its resolved script target (npm shims).
    return str(Path(path).absolute())


def child_env():
    keep = ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "CODEX_HOME",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "all_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["RUST_LOG"] = "off"
    return env


def codex_version(codex):
    try:
        r = subprocess.run([codex, "--version"], capture_output=True, timeout=10, env=child_env(), cwd=tempfile.gettempdir())
        text = r.stdout[:512].decode("utf-8", "replace").strip()
        match = re.search(r"(?:codex(?:-cli)?\s+)?(\d+\.\d+\.\d+(?:[-+.][a-zA-Z0-9.-]+)?)", text)
        if r.returncode or not match:
            raise ProtocolError("version_unavailable")
        return match.group(1)
    except (OSError, subprocess.TimeoutExpired):
        raise ProtocolError("version_command_failed") from None


def inspect_schema(codex):
    """Read local generated method contracts; no login or inference.

    Generation is a one-shot diagnostic, not part of the background polling loop.
    Only method names and counts leave the temporary directory.
    """
    with tempfile.TemporaryDirectory(prefix="codex-quota-schema-") as td:
        r = subprocess.run([codex, "app-server", "generate-json-schema", "--out", td],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45, env=child_env(), cwd=td)
        if r.returncode:
            return {"status": "unavailable", "methods": []}
        names, size, files = set(), 0, 0
        def visit(x):
            if isinstance(x, dict):
                v = x.get("const")
                if isinstance(v, str) and (v.startswith("account/") or v in ("initialize", "initialized")):
                    names.add(v)
                for v in x.get("enum", []):
                    if isinstance(v, str) and (v.startswith("account/") or v in ("initialize", "initialized")):
                        names.add(v)
                for v in x.values():
                    if isinstance(v, (dict, list)):
                        visit(v)
            elif isinstance(x, list):
                for v in x:
                    visit(v)
        for p in Path(td).rglob("*.json"):
            size += p.stat().st_size
            files += 1
            if size > 32 * 1024 * 1024 or files > 20000:
                return {"status": "inspection_limit", "methods": []}
            try:
                visit(json.loads(p.read_text()))
            except (ValueError, RecursionError):
                return {"status": "unreadable", "methods": []}
        return {"status": "inspected", "methods": sorted(names), "files": files, "bytes": size}


def read_snapshot(client, salt):
    # account/read is metadata only, with forced refresh explicitly disabled.
    account = client.request("account/read", {"refreshToken": False})
    result = client.request("account/rateLimits/read")
    projected = project_quota(result)
    context = account_context(account, result, salt)
    snapshot = normalize(projected, context)
    return snapshot, projected


def one_shot(settings, inspect=False):
    """One actual quota RPC; default does not create data directory/history."""
    settings.validate()
    codex = resolve_codex(settings.codex)
    version = codex_version(codex)
    schema = inspect_schema(codex) if inspect else None
    if schema and schema["status"] == "inspected" and "account/rateLimits/read" not in schema["methods"]:
        raise ProtocolError("installed_schema_missing_quota_read")
    with tempfile.TemporaryDirectory(prefix="codex-quota-read-") as td:
        with ReadOnlyClient([codex, "app-server"], Path(td), settings.request_timeout, env=child_env()) as client:
            snapshot, projected = read_snapshot(client, secrets.token_bytes(32))
            # Avoid unstable/random identity fingerprints in displayed dry-runs.
            snapshot["context"]["account_fingerprint"] = None
            rows = Tracker().observe(snapshot, datetime.now(timezone.utc), settings.timezone,
                                     trigger="dry_run", force=True, codex_version=version)
            calls = dict(client.sent)
    return {"result": "READ_ONLY_SNAPSHOT_PASS", "codex_version": version,
            "schema": schema, "outgoing_methods": calls, "model_turn_methods_sent": 0,
            "history_written": False, "launchagent_installed": False, "rows": rows}


class Schedule:
    def __init__(self, poll=300, heartbeat=3600):
        self.poll = poll
        self.heartbeat = heartbeat
        self.last_read = None
        self.last_heartbeat = None

    def due(self, now, dirty=False):
        if self.last_read is None:
            return "startup"
        if now < self.last_read:
            return "clock_change"
        if now - self.last_read > max(self.poll * 2, 600):
            return "after_gap"
        if self.last_heartbeat is None or now - self.last_heartbeat >= self.heartbeat:
            return "heartbeat"
        if dirty and now - self.last_read >= 2:
            return "notification"
        if now - self.last_read >= self.poll:
            return "poll"
        return None

    def success(self, now, trigger):
        self.last_read = now
        if self.last_heartbeat is None or trigger in ("startup", "heartbeat", "after_gap", "clock_change"):
            self.last_heartbeat = now


def backoff_seconds(attempt, jitter=True):
    base = min(900, 15 * (2 ** min(attempt, 6)))
    return base * random.uniform(.9, 1.1) if jitter else base


class Daemon:
    def __init__(self, settings):
        self.settings = settings.validate()
        self.stop_requested = False
        self.last_rows = []
        self.last_success = None
        self.last_persisted = None
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _stop(self, *_):
        self.stop_requested = True

    def _health(self, store, state, code=None, codex_version=None):
        now = datetime.now(timezone.utc)
        payload = {"pid": os.getpid(), "started_at": self.started_at,
                   "checked_at": now.isoformat(timespec="seconds"), "state": state,
                   "code": code, "last_successful_read": self.last_success,
                   "last_persisted_at": self.last_persisted, "last_rows": self.last_rows,
                   "codex_version": codex_version, "cap_bytes": self.settings.cap_bytes,
                   "poll_seconds": self.settings.poll_seconds, "heartbeat_seconds": self.settings.heartbeat_seconds,
                   "raw_retention_days": self.settings.retention_days, "raw_files_evicted": store.raw_dropped}
        try:
            store.atomic("health.json", payload)
        except StorageFull:
            # No history truncation and no expansion beyond the managed cap.
            pass

    def run(self):
        previous_handlers = {s: signal.signal(s, self._stop) for s in (signal.SIGINT, signal.SIGTERM)}
        try:
            return self._run()
        finally:
            for s, handler in previous_handlers.items():
                signal.signal(s, handler)

    def _run(self):
        cfg = self.settings
        with Store(cfg.data_dir, cfg.cap_bytes, cfg.retention_days) as store:
            store.validate_csv()
            salt = store.salt()
            attempt, last_housekeeping_day = 0, None
            while not self.stop_requested:
                try:
                    now = datetime.now(timezone.utc)
                    store.housekeeping(now)
                    last_housekeeping_day = now.date()
                    codex = resolve_codex(cfg.codex)
                    version = codex_version(codex)
                    # Temporary cwd avoids inheriting a project AGENTS/config tree.
                    with tempfile.TemporaryDirectory(prefix="codex-quota-idle-") as td:
                        with ReadOnlyClient([codex, "app-server"], Path(td), cfg.request_timeout, env=child_env()) as client:
                            tracker, schedule = Tracker(), Schedule(cfg.poll_seconds, cfg.heartbeat_seconds)
                            usage_enabled, last_usage_day = cfg.usage, None
                            self._health(store, "connected", codex_version=version)
                            while not self.stop_requested:
                                now = datetime.now(timezone.utc)
                                if now.date() != last_housekeeping_day:
                                    store.housekeeping(now)
                                    last_housekeeping_day = now.date()
                                trigger = schedule.due(time.time(), client.quota_dirty or client.auth_dirty)
                                if trigger:
                                    auth_dirty = client.auth_dirty
                                    client.quota_dirty = client.auth_dirty = False
                                    snapshot, raw = read_snapshot(client, salt)
                                    now = datetime.now(timezone.utc)  # Local receipt, not request start.
                                    candidate = copy.deepcopy(tracker)
                                    rows = candidate.observe(snapshot, now, cfg.timezone, trigger=trigger,
                                        force=trigger in ("startup", "heartbeat", "after_gap", "clock_change"),
                                        codex_version=version, gap=auth_dirty or trigger in ("after_gap", "clock_change"))
                                    if rows:
                                        store.write_rows(rows)  # Protect normalized history FIRST.
                                        tracker = candidate
                                        self.last_rows = rows
                                        self.last_persisted = now.isoformat(timespec="seconds")
                                        try:
                                            store.evidence({"timestamp_utc": now.isoformat(timespec="seconds"),
                                                "timestamp_local": rows[0]["timestamp_local"], "kind": "quota_snapshot",
                                                "sample_id": rows[0]["sample_id"], "trigger": trigger,
                                                "request_id": client.next_id, "notification_count": client.notification_count,
                                                "payload": raw}, now)
                                        except StorageFull:
                                            store.operation("raw_evidence_skipped_for_cap", now)
                                    self.last_success = now.isoformat(timespec="seconds")
                                    schedule.success(time.time(), trigger)
                                    attempt = 0
                                    self._health(store, "running", codex_version=version)
                                    if usage_enabled and now.date() != last_usage_day:
                                        try:
                                            u = project_usage(client.request("account/usage/read"))
                                            store.evidence({"timestamp_utc": now.isoformat(timespec="seconds"),
                                                "kind": "account_usage_supplement", "bucket_timezone": "backend_unspecified",
                                                "payload": u}, now)
                                        except (RpcError, ShapeError):
                                            usage_enabled = False  # Optional endpoint: no repeated probe storm.
                                            store.operation("optional_usage_disabled", now)
                                        except StorageFull:
                                            store.operation("optional_usage_skipped_for_cap", now)
                                        last_usage_day = now.date()
                                client.pump(timeout=1)
                except StorageFull:
                    self._health(store, "storage_full", "storage_full_history_preserved")
                    self._sleep(300)
                except (ProtocolError, ShapeError, OSError, subprocess.TimeoutExpired) as e:
                    code = str(e) if isinstance(e, (ProtocolError, ShapeError)) else "io_or_process_error"
                    code = re.sub(r"[^a-z0-9_]", "_", code.lower())[:80]
                    store.operation(code, datetime.now(timezone.utc))
                    self._health(store, "disconnected", code)
                    self._sleep(backoff_seconds(attempt))
                    attempt += 1
            self._health(store, "stopped")
        return 0

    def _sleep(self, seconds):
        until = time.monotonic() + seconds
        while not self.stop_requested and time.monotonic() < until:
            time.sleep(min(1, max(0, until - time.monotonic())))
