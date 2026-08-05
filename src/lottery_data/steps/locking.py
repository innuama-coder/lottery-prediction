"""Crash-safe, non-blocking inter-process file locks."""

from __future__ import annotations

import os
from pathlib import Path

from lottery_data.artifacts import validate_stable_id


class LockUnavailable(RuntimeError):
    exit_code = 6


class OSFileLock:
    """Hold an OS advisory lock until close or process death.

    The lock file is intentionally persistent; ownership is the open descriptor,
    not file existence, so a killed writer cannot leave a stale lock.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: int | None = None

    def acquire(self) -> "OSFileLock":
        if self._descriptor is not None:
            raise RuntimeError("lock is already acquired")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise LockUnavailable(f"lock is held: {self.path}") from exc
            else:
                import fcntl

                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise LockUnavailable(f"lock is held: {self.path}") from exc
        except Exception:
            os.close(descriptor)
            raise
        self._descriptor = descriptor
        return self

    def release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def read_locked_bytes(self) -> bytes:
        """Read the lock-file payload through the descriptor that owns the lock."""
        descriptor = self._descriptor
        if descriptor is None:
            raise RuntimeError("lock is not acquired")
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 8192)
            if not chunk:
                break
            chunks.append(chunk)
        # Windows byte-range unlock acts at the current file position, so leave
        # the descriptor at byte zero for release().
        os.lseek(descriptor, 0, os.SEEK_SET)
        return b"".join(chunks)

    def __enter__(self) -> "OSFileLock":
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()


class RunLock(OSFileLock):
    """One process owns one mutable run; the persistent file is not ownership."""

    def __init__(self, artifacts_root: Path, run_id: str) -> None:
        validate_stable_id(run_id, "run-id")
        super().__init__(artifacts_root / f".run-lock-{run_id}.lock")
