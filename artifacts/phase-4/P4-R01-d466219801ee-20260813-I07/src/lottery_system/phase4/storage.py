from __future__ import annotations

import errno
import hashlib
import os
import secrets
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .identity import validate_stable_id
from .serialization import canonical_json_bytes


class IdentityReuseError(FileExistsError):
    exit_code = 4


class SecurityBoundaryError(ValueError):
    exit_code = 6


class LockUnavailable(RuntimeError):
    exit_code = 30


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    return os.open(path, flags)


def fsync_directory(path: Path) -> None:
    # Windows does not permit opening a directory with ``os.open``.  NTFS
    # publishes both hard links and renames atomically; file contents are
    # flushed before publication below, but Python exposes no portable way to
    # flush the containing directory handle.
    if os.name == "nt":
        return
    descriptor = _open_directory(path)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def ensure_directory(path: Path, *, mode: int = 0o700) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    if not current.is_dir():
        raise NotADirectoryError(current)
    for directory in reversed(missing):
        directory.mkdir(mode=mode)
        fsync_directory(directory.parent)


def safe_relative_path(value: str) -> str:
    pure = PurePosixPath(value)
    if (
        not value
        or pure.is_absolute()
        or "\\" in value
        or "latest" in value.lower()
        or "*" in value
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise SecurityBoundaryError(f"unsafe relative path: {value!r}")
    return pure.as_posix()


def resolve_inside(root: Path, relative: str) -> Path:
    safe = safe_relative_path(relative)
    resolved_root = root.resolve()
    if os.name == "nt":
        # Content identities deliberately contain ``:``.  Keep those logical
        # identities in manifests and JSON while using a reversible physical
        # spelling on filesystems where the character is reserved.
        physical_parts = []
        for part in PurePosixPath(safe).parts:
            if ":" in part and len(part) > 64:
                prefix = part.split(":", 1)[0][:24]
                suffix = PurePosixPath(part).suffix
                part = f"{prefix}~{hashlib.sha256(part.encode('utf-8')).hexdigest()[:40]}{suffix}"
            else:
                part = part.replace("%", "%25").replace(":", "%3A")
            physical_parts.append(part)
        candidate = resolved_root.joinpath(*physical_parts).resolve(strict=False)
        if not candidate.exists():
            # The imported Linux snapshot was prepared before the reversible
            # mapper and used underscores for colon-bearing artifact names.
            legacy = resolved_root.joinpath(*(part.replace(":", "_") for part in PurePosixPath(safe).parts)).resolve(strict=False)
            if legacy.exists():
                candidate = legacy
    else:
        candidate = (resolved_root / safe).resolve(strict=False)
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise SecurityBoundaryError("path escapes the authorized root") from exc
    return candidate


def validate_runtime_root(project_root: Path, runtime_root: Path) -> Path:
    project = project_root.resolve()
    expected_parent = (project / "artifacts/phase-4-runtime").resolve(strict=False)
    candidate = runtime_root.resolve(strict=False)
    try:
        relative = candidate.relative_to(expected_parent)
    except ValueError as exc:
        configured = os.environ.get("P4_RUNTIME_ROOT", "").strip()
        external = Path(configured).resolve(strict=False) if configured else None
        if (
            external != candidate
            or candidate.parent.name != "phase-4-runtime"
            or candidate.parent.parent.name != "artifacts"
        ):
            raise SecurityBoundaryError("runtime root is outside artifacts/phase-4-runtime") from exc
        relative = Path(candidate.name)
    if len(relative.parts) != 1:
        raise SecurityBoundaryError("runtime root must name exactly one immutable runtime identity")
    validate_stable_id(relative.parts[0], "runtime identity")
    for protected in (
        "artifacts/phase-0", "artifacts/phase-0-multisource", "artifacts/phase-1",
        "artifacts/phase-2", "artifacts/phase-2.1", "artifacts/phase-3",
    ):
        protected_path = (project / protected).resolve()
        if candidate == protected_path or protected_path in candidate.parents:
            raise SecurityBoundaryError("runtime root overlaps a protected Phase 0-3 root")
    return candidate


def _temporary(path: Path) -> Path:
    # Keep the temporary basename bounded.  Content-addressed destination names
    # are already long enough to approach MAX_PATH in Windows test/runtime
    # roots, and the temporary file does not need to repeat that identity.
    token = hashlib.sha256(os.fsencode(path.name)).hexdigest()[:12]
    return path.with_name(f".tmp-{token}-{os.getpid()}-{secrets.token_hex(4)}")


def _write_exclusive(path: Path, payload: bytes, mode: int) -> None:
    if os.name == "nt":
        # Binary streams avoid Windows CRT text/device semantics while ``xb``
        # retains the required exclusive-create guarantee.
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_once_bytes(
    path: Path,
    payload: bytes,
    *,
    mode: int = 0o600,
    fault: Callable[[str], None] | None = None,
) -> None:
    ensure_directory(path.parent)
    temporary = _temporary(path)
    callback = fault or (lambda _stage: None)
    try:
        _write_exclusive(temporary, payload, mode)
        callback("after_file_fsync")
        try:
            if os.name == "nt":
                # Windows rename is an atomic create when the destination does
                # not exist and fails rather than replacing an existing file.
                os.rename(temporary, path)
            else:
                os.link(temporary, path)
        except FileExistsError as exc:
            raise IdentityReuseError(path) from exc
        callback("after_publish")
        temporary.unlink(missing_ok=True)
        fsync_directory(path.parent)
        callback("after_directory_fsync")
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def write_once_json(path: Path, value: Any, *, fault: Callable[[str], None] | None = None) -> None:
    write_once_bytes(path, canonical_json_bytes(value), fault=fault)


def atomic_replace_bytes(
    path: Path,
    payload: bytes,
    *,
    mode: int = 0o600,
    fault: Callable[[str], None] | None = None,
) -> None:
    ensure_directory(path.parent)
    temporary = _temporary(path)
    callback = fault or (lambda _stage: None)
    try:
        _write_exclusive(temporary, payload, mode)
        callback("after_file_fsync")
        os.replace(temporary, path)
        callback("after_rename")
        fsync_directory(path.parent)
        callback("after_directory_fsync")
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def atomic_replace_json(path: Path, value: Any, *, fault: Callable[[str], None] | None = None) -> None:
    atomic_replace_bytes(path, canonical_json_bytes(value), fault=fault)


def remove_durable(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    fsync_directory(path.parent)


def publish_directory_once(staging: Path, destination: Path) -> None:
    if destination.exists():
        raise IdentityReuseError(destination)
    ensure_directory(destination.parent)
    try:
        os.rename(staging, destination)
    except OSError as exc:
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            raise IdentityReuseError(destination) from exc
        raise
    fsync_directory(destination.parent)


class AdvisoryFileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: int | None = None

    def acquire(self, *, blocking: bool = True) -> "AdvisoryFileLock":
        if self._descriptor is not None:
            raise RuntimeError("lock is already acquired")
        ensure_directory(self.path.parent)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                operation = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                try:
                    msvcrt.locking(descriptor, operation, 1)
                except OSError as exc:
                    raise LockUnavailable(f"lock is held: {self.path}") from exc
            else:
                import fcntl

                operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
                try:
                    fcntl.flock(descriptor, operation)
                except BlockingIOError as exc:
                    raise LockUnavailable(f"lock is held: {self.path}") from exc
            self._descriptor = descriptor
            return self
        except Exception:
            os.close(descriptor)
            raise

    def release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> "AdvisoryFileLock":
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()
