"""Small CLI. No dependency installation, credential prompts or model calls."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfoNotFoundError

from . import __version__
from .runtime import Settings, Daemon, one_shot
from .storage import Store, StorageError, is_locked, regular
from .protocol import ProtocolError
from .model import ShapeError
from . import launchd


def default_dir():
    return Path.home() / "Library" / "Application Support" / "CodexQuotaLogger"


def parser():
    p = argparse.ArgumentParser(description="Read-only account quota logger; no model turns.")
    p.add_argument("command", choices=["doctor", "snapshot", "run", "status", "housekeeping", "plist", "install", "uninstall", "start", "stop", "restart"])
    p.add_argument("--codex", default="codex", help="Codex executable, never an arbitrary shell command")
    p.add_argument("--data-dir", type=Path, default=default_dir())
    p.add_argument("--timezone", default="Asia/Shanghai")
    p.add_argument("--poll-seconds", type=int, default=300)
    p.add_argument("--heartbeat-seconds", type=int, default=3600)
    p.add_argument("--cap-bytes", type=int, default=250_000_000)
    p.add_argument("--retention-days", type=int, default=30)
    p.add_argument("--request-timeout", type=float, default=30)
    p.add_argument("--usage", action="store_true", help="Optional once-daily account/usage/read (separate evidence)")
    p.add_argument("--approve-install", action="store_true", help="Explicitly permit writing a user LaunchAgent; does not load it")
    p.add_argument("--version", action="version", version=__version__)
    return p


def status(settings):
    root = settings.data_dir.expanduser().absolute()
    result = {"data_dir": str(root), "installed": launchd.plist_path().is_file(),
              "launchagent_loaded": launchd.loaded(), "writer_lock_held": False,
              "logical_bytes": 0, "allocated_bytes": 0, "health": None}
    if not root.exists():
        return result
    if root.is_symlink():
        raise StorageError("symlink_directory_refused")
    result["writer_lock_held"] = is_locked(root)
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
        for name in files:
            p = Path(base) / name
            if not p.is_symlink() and p.is_file():
                st = p.stat()
                result["logical_bytes"] += st.st_size
                result["allocated_bytes"] += getattr(st, "st_blocks", 0) * 512
    h = root / "health.json"
    if regular(h) and h.stat().st_size < 524288:
        try:
            result["health"] = json.loads(h.read_text())
            stamp = result["health"].get("last_successful_read")
            if stamp:
                result["last_success_age_seconds"] = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds())
        except (ValueError, AttributeError):
            result["health"] = {"state": "unreadable_health"}
    return result


def main(argv=None):
    os.umask(0o077)
    a = parser().parse_args(argv)
    cfg = Settings(a.data_dir, a.codex, a.timezone, a.poll_seconds, a.heartbeat_seconds,
                   a.cap_bytes, a.retention_days, a.usage, a.request_timeout)
    try:
        cfg.validate()
        if a.command in ("doctor", "snapshot"):
            out = one_shot(cfg, inspect=a.command == "doctor")
            if a.command == "doctor":
                out["result"] = "CODEX_QUOTA_LOGGER_DRY_RUN_PASS"
                out["compatibility"] = "live_account_read_verified; schema status reported separately"
        elif a.command == "run":
            return Daemon(cfg).run()
        elif a.command == "status":
            out = status(cfg)  # Reads saved files only. Never starts Codex.
        elif a.command == "housekeeping":
            with Store(cfg.data_dir, cfg.cap_bytes, cfg.retention_days) as s:
                out = s.housekeeping(datetime.now(timezone.utc))
        elif a.command == "plist":
            import plistlib
            sys.stdout.buffer.write(plistlib.dumps(launchd.make_plist(cfg, Path(__file__).parent.parent / "quota_logger.py")))
            return 0
        elif a.command == "install":
            out = launchd.install(cfg, Path(__file__).parent.parent / "quota_logger.py", a.approve_install)
        else:
            out = launchd.action(a.command)
        print(json.dumps(out, indent=2, ensure_ascii=True, allow_nan=False))
        return 0
    except (ProtocolError, ShapeError, StorageError) as e:
        # These exception messages are application-defined codes, not payloads.
        print(json.dumps({"result": "BLOCKED", "code": str(e), "model_turn_methods_sent_by_design": 0}), file=sys.stderr)
        return 2
    except (OSError, ValueError, ZoneInfoNotFoundError, subprocess.TimeoutExpired):
        print(json.dumps({"result": "BLOCKED", "code": "local_configuration_or_io_error"}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
