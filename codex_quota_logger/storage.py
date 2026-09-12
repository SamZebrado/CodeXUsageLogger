"""Private, single-writer storage with admission checks and protected CSV history."""
from __future__ import annotations

import csv
import fcntl
import gzip
import io
import json
import os
import re
import secrets
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .model import COLUMNS

RAW_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl(?:\.gz)?$")


class StorageError(RuntimeError):
    pass


class StorageFull(StorageError):
    pass


def secure_directory(path: Path):
    path = path.expanduser().absolute()
    if path.is_symlink():
        raise StorageError("symlink_directory_refused")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or path.stat().st_uid != os.getuid():
        raise StorageError("directory_ownership_refused")
    path.chmod(0o700)
    return path


def regular(path: Path):
    try:
        s = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_uid != os.getuid():
        raise StorageError("unsafe_file_refused")
    return True


def open_private(path: Path, flags):
    regular(path)
    fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    s = os.fstat(fd)
    if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_uid != os.getuid():
        os.close(fd)
        raise StorageError("unsafe_open_file")
    os.fchmod(fd, 0o600)
    return fd


def write_all(fd, data):
    while data:
        n = os.write(fd, data)
        if n <= 0:
            raise StorageError("short_write")
        data = data[n:]


class Store:
    def __init__(self, root: Path, cap=250_000_000, retention_days=30):
        if cap < 65536 or not 1 <= retention_days <= 365:
            raise ValueError("invalid_storage_policy")
        self.root = secure_directory(root)
        self.raw = secure_directory(self.root / "raw")
        self.cap = cap
        self.retention_days = retention_days
        self.reserve = min(1_048_576, cap // 8)
        self.lock_fd = None
        self.csv_path = self.root / "quota_history.csv"
        self.raw_dropped = 0

    def files(self):
        files = []
        for base, dirs, names in os.walk(self.root, followlinks=False):
            for d in dirs:
                if (Path(base) / d).is_symlink():
                    raise StorageError("symlink_in_data_directory")
            for name in names:
                p = Path(base) / name
                if regular(p):
                    files.append(p)
        return files

    def size(self):
        return sum(p.stat().st_size for p in self.files())

    def acquire(self):
        fd = open_private(self.root / ".lock", os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise StorageError("logger_already_running") from None
        self.lock_fd = fd
        return self

    def release(self):
        if self.lock_fd is not None:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)
            self.lock_fd = None

    def require_lock(self):
        if self.lock_fd is None:
            raise StorageError("writer_lock_required")

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()

    def _evictable(self):
        return sorted((p for p in self.raw.iterdir() if RAW_NAME.fullmatch(p.name) and regular(p)),
                      key=lambda p: (p.name[:10], p.stat().st_mtime, p.name))

    def ensure(self, extra: int, *, overhead=False, exclude=()):
        """Logical byte cap, including existing/temporary files in this directory.

        Only known raw-evidence files and rotated operational logs are eligible.
        Unknown files, configuration, health, salt and CSV are protected.
        """
        self.require_lock()
        limit = self.cap if overhead else self.cap - self.reserve
        total = self.size()
        excluded = set(exclude)
        for path in self._evictable():
            if total + extra <= limit:
                return
            if path in excluded:
                continue
            total -= path.stat().st_size
            path.unlink()
            self.raw_dropped += 1
        for name in ("operations.2.jsonl", "operations.1.jsonl"):
            p = self.root / name
            if total + extra <= limit:
                return
            if p not in excluded and regular(p):
                total -= p.stat().st_size
                p.unlink()
        if total + extra > limit:
            raise StorageFull("storage_full_history_preserved")

    def atomic(self, name, payload):
        self.require_lock()
        if name not in ("health.json", "settings.json"):
            raise StorageError("unknown_atomic_target")
        data = (json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n").encode()
        if len(data) > 524288:
            raise StorageError("state_too_large")
        target, temp = self.root / name, self.root / ("." + name + ".tmp")
        if regular(temp):
            temp.unlink()  # Owned fixed temporary file from an interrupted write.
        self.ensure(len(data), overhead=True)
        fd = open_private(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, target)
        self._sync_dir()

    def _sync_dir(self):
        fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def salt(self):
        self.require_lock()
        p = self.root / "identity.salt"
        if regular(p):
            data = p.read_bytes()
            if len(data) != 32:
                raise StorageError("invalid_identity_salt")
            return data
        data = secrets.token_bytes(32)
        self.ensure(len(data), overhead=True)
        fd = open_private(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        return data

    def append(self, path: Path, data: bytes, *, overhead=False):
        self.require_lock()
        if path.parent not in (self.root, self.raw):
            raise StorageError("invalid_append_path")
        self.ensure(len(data), overhead=overhead)
        fd = open_private(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        start = os.fstat(fd).st_size
        try:
            write_all(fd, data)
            os.fsync(fd)
        except BaseException:
            # Handles normal interrupted/failed writes. Power-loss recovery is
            # deliberately fail-closed; never silently truncate old CSV data.
            os.ftruncate(fd, start)
            raise
        finally:
            os.close(fd)

    def validate_csv(self):
        if not regular(self.csv_path) or self.csv_path.stat().st_size == 0:
            return
        with self.csv_path.open("rb") as f:
            first = f.readline(8192).decode("utf-8")
            if next(csv.reader([first])) != COLUMNS:
                raise StorageError("csv_schema_mismatch")
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                raise StorageError("csv_incomplete_tail_preserved")

    def write_rows(self, rows):
        if not rows:
            return
        self.validate_csv()
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, COLUMNS, lineterminator="\n", extrasaction="raise")
        if not regular(self.csv_path) or self.csv_path.stat().st_size == 0:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: ("true" if v is True else "false" if v is False else v) for k, v in row.items()})
        self.append(self.csv_path, output.getvalue().encode())

    def evidence(self, payload, now):
        # Caller supplies ONLY projected quota/usage; never arbitrary RPC data.
        data = (json.dumps(payload, allow_nan=False, separators=(",", ":")) + "\n").encode()
        if len(data) > 2 * 1024 * 1024:
            raise StorageError("evidence_record_too_large")
        path = self.raw / (now.astimezone(timezone.utc).date().isoformat() + ".jsonl")
        self.append(path, data)

    def operation(self, code, now, count=None):
        if not re.fullmatch(r"[a-z0-9_]{1,80}", code):
            code = "unspecified_error"
        payload = {"timestamp_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds"), "code": code}
        if isinstance(count, int):
            payload["count"] = count
        p = self.root / "operations.jsonl"
        rotate_at = min(131072, self.cap // 32)
        if regular(p) and p.stat().st_size >= rotate_at:
            older, newest = self.root / "operations.2.jsonl", self.root / "operations.1.jsonl"
            if regular(older):
                older.unlink()
            if regular(newest):
                os.replace(newest, older)
            os.replace(p, newest)
        try:
            self.append(p, (json.dumps(payload) + "\n").encode(), overhead=True)
        except StorageFull:
            pass  # Hard cap wins even over diagnostics.

    def housekeeping(self, now):
        self.require_lock()
        today = now.astimezone(timezone.utc).date()
        cutoff = today - timedelta(days=self.retention_days - 1)
        deleted, compressed = 0, 0
        for p in self._evictable():
            try:
                day = datetime.strptime(p.name[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if day < cutoff:
                p.unlink()
                deleted += 1
        for p in list(self._evictable()):
            if p.suffix != ".jsonl" or p.name[:10] >= today.isoformat():
                continue
            target, temp = p.with_suffix(".jsonl.gz"), p.with_suffix(".jsonl.gz.tmp")
            if regular(target):
                # Never overwrite conflicting historical evidence automatically.
                continue
            if regular(temp):
                temp.unlink()
            size = p.stat().st_size
            # Conservative gzip expansion bound + streaming working-file space.
            bound = size + size // 100 + 65536
            try:
                self.ensure(bound, exclude=(p,))
            except StorageFull:
                continue  # Compression is optional; cap/CSV preservation win.
            fd = open_private(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            try:
                with os.fdopen(fd, "wb", closefd=True) as out:
                    with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0) as z:
                        with p.open("rb") as src:
                            while chunk := src.read(65536):
                                z.write(chunk)
                                if out.tell() > bound:
                                    raise StorageError("compression_bound_exceeded")
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temp, target)
                p.unlink()
                compressed += 1
            except BaseException:
                if regular(temp):
                    temp.unlink()
                raise
        self.ensure(0, overhead=True)
        if deleted:
            self.operation("expired_raw_deleted", now, deleted)
        if compressed:
            self.operation("raw_days_compressed", now, compressed)
        return {"expired_files_deleted": deleted, "files_compressed": compressed, "logical_bytes": self.size()}


def is_locked(root: Path):
    p = root / ".lock"
    if not regular(p):
        return False
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True
    finally:
        os.close(fd)
