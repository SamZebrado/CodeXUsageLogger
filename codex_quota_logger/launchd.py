"""macOS user LaunchAgent controls. Never invoked implicitly by the logger."""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .runtime import resolve_codex
from .storage import StorageError, regular, open_private, write_all

LABEL = "local.codex-quota-logger"


def plist_path(home=None):
    return Path(home or Path.home()) / "Library" / "LaunchAgents" / (LABEL + ".plist")


def make_plist(settings, launcher, python=None):
    exe = resolve_codex(settings.codex)
    py = str(Path(python or sys.executable).absolute())
    entry = [str(Path(launcher).absolute())] if Path(launcher).is_file() else ["-m", "codex_quota_logger"]
    args = [py, "-B", *entry, "run", "--codex", exe,
            "--data-dir", str(settings.data_dir.expanduser().absolute()),
            "--timezone", settings.timezone, "--poll-seconds", str(settings.poll_seconds),
            "--heartbeat-seconds", str(settings.heartbeat_seconds), "--cap-bytes", str(settings.cap_bytes),
            "--retention-days", str(settings.retention_days), "--request-timeout", str(settings.request_timeout)]
    if settings.usage:
        args.append("--usage")
    env = {"PATH": ":".join(dict.fromkeys([str(Path(exe).parent), str(Path(py).parent),
                    "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"])),
           "HOME": str(Path.home()), "PYTHONDONTWRITEBYTECODE": "1"}
    if "CODEX_HOME" in os.environ:
        env["CODEX_HOME"] = str(Path(os.environ["CODEX_HOME"]).expanduser().absolute())
    return {"Label": LABEL, "ProgramArguments": args, "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 300,
            "ProcessType": "Background", "Umask": 63, "EnvironmentVariables": env,
            "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null"}


def check_mac():
    if sys.platform != "darwin":
        raise StorageError("launchagent_requires_macos")


def install(settings, launcher, approved=False):
    check_mac()
    if not approved:
        raise StorageError("explicit_install_approval_required")
    target = plist_path()
    if target.is_symlink():
        raise StorageError("symlink_launchagent_refused")
    if target.exists():
        raise StorageError("launchagent_exists_uninstall_first")
    target.parent.mkdir(parents=True, exist_ok=True)
    data = plistlib.dumps(make_plist(settings, launcher), sort_keys=True)
    fd = open_private(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    return {"installed": str(target), "loaded": False, "next_command": "python3 quota_logger.py start"}


def _ctl(args):
    check_mac()
    return subprocess.run(["/bin/launchctl", *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)


def loaded():
    if sys.platform != "darwin":
        return None
    return _ctl(["print", f"gui/{os.getuid()}/{LABEL}"]).returncode == 0


def action(name):
    check_mac()
    target = plist_path()
    domain, service = f"gui/{os.getuid()}", f"gui/{os.getuid()}/{LABEL}"
    present = regular(target)
    if name in ("start", "restart") and not present:
        raise StorageError("launchagent_not_installed")
    if name in ("stop", "restart", "uninstall") and loaded():
        if _ctl(["bootout", service]).returncode:
            raise StorageError("launchagent_bootout_failed")
    if name in ("start", "restart"):
        if loaded():
            return {"state": "already_loaded"}
        if _ctl(["bootstrap", domain, str(target)]).returncode:
            raise StorageError("launchagent_bootstrap_failed")
    if name == "uninstall" and present:
        target.unlink()
    return {"action": name, "loaded": loaded(), "history_deleted": False}
