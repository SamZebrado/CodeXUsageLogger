"""Bounded newline-JSON client. No model, task, reset or account-write APIs."""
from __future__ import annotations

import json
import hashlib
import os
import selectors
import signal
import subprocess
import time
from collections import Counter
from pathlib import Path


ALLOWED_REQUESTS = frozenset({"initialize", "account/read", "account/rateLimits/read", "account/usage/read"})
ALLOWED_NOTIFICATIONS = frozenset({"initialized"})
QUOTA_NOTICE = "account/rateLimits/updated"
AUTH_NOTICE = "account/updated"


class ProtocolError(RuntimeError):
    pass


class RpcError(ProtocolError):
    def __init__(self, code):
        self.code = code if isinstance(code, int) else None
        # Intentionally discard arbitrary upstream message/data strings.
        super().__init__(f"rpc_error_{self.code}")


class ReadOnlyClient:
    def __init__(self, command, cwd: Path, timeout=30.0, max_message=2 * 1024 * 1024, env=None):
        self.command = list(command)
        self.cwd = cwd
        self.timeout = timeout
        self.max_message = max_message
        self.env = env
        self.proc = None
        self.sel = None
        self.buffer = bytearray()
        self.next_id = 0
        self.quota_dirty = False
        self.auth_dirty = False
        self.sent = Counter()
        self.notification_count = 0
        self._last_notice_digest = None
        self._reply = None
        self._waiting_id = None

    def start(self):
        # Independent child: never connect to or terminate the desktop process.
        self.proc = subprocess.Popen(self.command, cwd=self.cwd, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
            start_new_session=True, env=self.env)
        os.set_blocking(self.proc.stdout.fileno(), False)
        os.set_blocking(self.proc.stdin.fileno(), False)
        self.sel = selectors.DefaultSelector()
        self.sel.register(self.proc.stdout, selectors.EVENT_READ)
        self.request("initialize", {"clientInfo": {"name": "codex_quota_logger", "title": "Codex Quota Logger", "version": "0.1.0"}})
        self.notify("initialized", {})
        return self

    def _write(self, message):
        if self.proc is None or self.proc.poll() is not None:
            raise ProtocolError("server_not_running")
        data = (json.dumps(message, separators=(",", ":"), allow_nan=False) + "\n").encode()
        if len(data) > 8192:
            raise ProtocolError("outgoing_message_too_large")
        end = time.monotonic() + self.timeout
        while data:
            try:
                n = os.write(self.proc.stdin.fileno(), data)
                if n <= 0:
                    raise ProtocolError("short_pipe_write")
                data = data[n:]
            except BlockingIOError:
                if time.monotonic() >= end:
                    raise ProtocolError("write_timeout") from None
                time.sleep(.01)
            except (BrokenPipeError, OSError):
                raise ProtocolError("write_failed") from None

    def request(self, method, params=None):
        if method not in ALLOWED_REQUESTS:
            raise ProtocolError("forbidden_request")
        if method == "account/read" and params != {"refreshToken": False}:
            raise ProtocolError("auth_refresh_forbidden")
        if self._waiting_id is not None:
            raise ProtocolError("only_one_inflight_request")
        self.next_id += 1
        rid = self.next_id
        msg = {"id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._waiting_id, self._reply = rid, None
        try:
            self._write(msg)
            self.sent[method] += 1
            deadline = time.monotonic() + self.timeout
            while self._reply is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProtocolError("request_timeout")
                self.pump(min(remaining, .5))
            reply = self._reply
            if "error" in reply:
                e = reply["error"]
                raise RpcError(e.get("code") if isinstance(e, dict) else None)
            if "result" not in reply:
                raise ProtocolError("missing_result")
            return reply["result"]
        finally:
            self._waiting_id, self._reply = None, None

    def notify(self, method, params=None):
        if method not in ALLOWED_NOTIFICATIONS:
            raise ProtocolError("forbidden_notification")
        message = {"method": method}
        if params is not None:
            message["params"] = params
        self._write(message)
        self.sent[method] += 1

    def _dispatch(self, line):
        try:
            m = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError, RecursionError):
            raise ProtocolError("invalid_json") from None
        if not isinstance(m, dict):
            raise ProtocolError("invalid_message")
        if "method" in m and "id" in m:
            # No approvals, login helpers, tools or external credential service.
            raise ProtocolError("unexpected_server_request")
        if "id" in m:
            if type(m["id"]) is int and m["id"] == self._waiting_id:
                self._reply = m
            return  # Ignore unrelated/late responses; do not log arbitrary content.
        if m.get("method") == QUOTA_NOTICE:
            # Sparse notices are hints, never authoritative replacements. A
            # repeated notification emitted by a read must not create a loop.
            from .model import project_quota, ShapeError
            try:
                projected = project_quota(m.get("params", {}))
            except ShapeError:
                projected = {"unrecognized_sparse_hint": True}
            digest = hashlib.sha256(json.dumps(projected, sort_keys=True).encode()).hexdigest()
            if digest != self._last_notice_digest:
                self.quota_dirty = True
                self._last_notice_digest = digest
            self.notification_count += 1
        elif m.get("method") == AUTH_NOTICE:
            self.auth_dirty = True
        # All other notifications discarded without persistence.

    def pump(self, timeout=0):
        if self.sel is None:
            raise ProtocolError("server_not_running")
        # Bound each iteration so a notification flood cannot monopolize the loop.
        for _ in range(16):
            ready = self.sel.select(timeout if _ == 0 else 0)
            if not ready:
                return
            try:
                chunk = os.read(self.proc.stdout.fileno(), 65536)
            except BlockingIOError:
                return
            if not chunk:
                raise ProtocolError("server_eof")
            self.buffer.extend(chunk)
            while b"\n" in self.buffer:
                line, _, rest = self.buffer.partition(b"\n")
                self.buffer = bytearray(rest)
                if len(line) > self.max_message:
                    raise ProtocolError("message_too_large")
                if line.strip():
                    self._dispatch(line)
            if len(self.buffer) > self.max_message:
                raise ProtocolError("message_too_large")
            if self._reply is not None:
                return

    def close(self):
        if self.sel:
            self.sel.close()
            self.sel = None
        p = self.proc
        if p is None:
            return
        try:
            if p.stdin:
                p.stdin.close()
            if p.poll() is None:
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGTERM)
                    try:
                        p.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        os.killpg(p.pid, signal.SIGKILL)
                        p.wait(timeout=2)
        except ProcessLookupError:
            pass
        finally:
            if p.stdout:
                p.stdout.close()
            self.proc = None

    def __enter__(self):
        try:
            return self.start()
        except BaseException:
            self.close()
            raise

    def __exit__(self, *_):
        self.close()
