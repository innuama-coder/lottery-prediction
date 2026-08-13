from __future__ import annotations

import base64
import ctypes
import errno
import glob
import io
import json
import os
import platform
try:
    import resource
except ImportError:  # pragma: no cover - exercised by Windows suites
    resource = None  # type: ignore[assignment]
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from .forecast import ForecastViolation, validate_label_free_snapshot
from .identity import content_id, validate_stable_id, verify_content_id
from .ledger import AppendOnlyLedger
from .lock import ForecastLockViolation, load_locked_forecast
from .serialization import load_json, sha256_file
from .storage import AdvisoryFileLock, resolve_inside, write_once_json
from .time_gate import validate_official_result_label_times


class LabelCapabilityViolation(ForecastViolation):
    pass


_TRAINER_MODE = False
_TRAINER_EXEC_MARKER = "phase4-trainer-clean-exec-v1"
_TRAINER_PAYLOAD_LIMIT = 1_048_576
_TRAINER_FIXED_PATH = "/usr/bin:/bin"
_TRAINER_FIXED_LOCALE = "C.UTF-8"
_TRAINER_FIXED_UMASK = 0o077
_TRAINER_PYTHON = "/usr/bin/python3"
_TRAINER_PRLIMIT = "/usr/bin/prlimit"
_TRAINER_FIXED_LIMITS = {} if resource is None else {
    resource.RLIMIT_AS: (2_147_483_648, 2_147_483_648),
    resource.RLIMIT_CORE: (0, 0),
    resource.RLIMIT_CPU: (300, 300),
    resource.RLIMIT_DATA: (1_073_741_824, 1_073_741_824),
    resource.RLIMIT_FSIZE: (16_777_216, 16_777_216),
    resource.RLIMIT_MEMLOCK: (65_536, 65_536),
    resource.RLIMIT_MSGQUEUE: (0, 0),
    resource.RLIMIT_NICE: (0, 0),
    resource.RLIMIT_NOFILE: (256, 256),
    resource.RLIMIT_NPROC: (64, 64),
    resource.RLIMIT_RSS: (1_073_741_824, 1_073_741_824),
    resource.RLIMIT_RTPRIO: (0, 0),
    resource.RLIMIT_RTTIME: (1_000_000, 1_000_000),
    resource.RLIMIT_SIGPENDING: (64, 64),
    resource.RLIMIT_STACK: (8_388_608, 8_388_608),
}
_TRAINER_LIMIT_NAMES = {} if resource is None else {
    resource.RLIMIT_AS: "RLIMIT_AS",
    resource.RLIMIT_CORE: "RLIMIT_CORE",
    resource.RLIMIT_CPU: "RLIMIT_CPU",
    resource.RLIMIT_DATA: "RLIMIT_DATA",
    resource.RLIMIT_FSIZE: "RLIMIT_FSIZE",
    resource.RLIMIT_MEMLOCK: "RLIMIT_MEMLOCK",
    resource.RLIMIT_MSGQUEUE: "RLIMIT_MSGQUEUE",
    resource.RLIMIT_NICE: "RLIMIT_NICE",
    resource.RLIMIT_NOFILE: "RLIMIT_NOFILE",
    resource.RLIMIT_NPROC: "RLIMIT_NPROC",
    resource.RLIMIT_RSS: "RLIMIT_RSS",
    resource.RLIMIT_RTPRIO: "RLIMIT_RTPRIO",
    resource.RLIMIT_RTTIME: "RLIMIT_RTTIME",
    resource.RLIMIT_SIGPENDING: "RLIMIT_SIGPENDING",
    resource.RLIMIT_STACK: "RLIMIT_STACK",
}


def _contains_forbidden_label(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key in {"numbers", "result_revision_id", "winning_numbers", "label", "capability", "unlock_receipt"}:
                return True
            if _contains_forbidden_label(nested):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden_label(item) for item in value)
    return False


def _active_nonessential_file_descriptors() -> list[int]:
    if os.name == "nt":
        raise LabelCapabilityViolation("trainer descriptor isolation requires POSIX /proc")
    try:
        candidates = sorted({int(name) for name in os.listdir("/proc/self/fd") if name.isdigit() and int(name) > 2})
    except (OSError, ValueError) as exc:
        raise LabelCapabilityViolation("cannot enumerate trainer file descriptors") from exc
    active: list[int] = []
    for descriptor in candidates:
        try:
            os.fstat(descriptor)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise LabelCapabilityViolation(f"cannot inspect trainer descriptor {descriptor}") from exc
        else:
            active.append(descriptor)
    return active


def _close_nonessential_file_descriptors() -> list[int]:
    """Close every descriptor except stdin/stdout/stderr before quarantine."""
    active = _active_nonessential_file_descriptors()
    machine = platform.machine().lower()
    close_range_number = {
        "x86_64": 436,
        "amd64": 436,
        "aarch64": 436,
        "arm64": 436,
    }.get(machine)
    if close_range_number is None:
        raise LabelCapabilityViolation(f"trainer clean-exec FD closure is unsupported on {machine}")
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(close_range_number, 3, ctypes.c_uint(-1).value, 0)
    if result != 0:
        observed_errno = ctypes.get_errno()
        if observed_errno != errno.ENOSYS:
            raise LabelCapabilityViolation(f"cannot close trainer file descriptors: errno={observed_errno}")
        for descriptor in active:
            try:
                os.close(descriptor)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise LabelCapabilityViolation(f"cannot close trainer descriptor {descriptor}") from exc
    for descriptor in active:
        try:
            os.fstat(descriptor)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise LabelCapabilityViolation(f"trainer descriptor {descriptor} has an unexpected terminal") from exc
        else:
            raise LabelCapabilityViolation(f"trainer descriptor {descriptor} remained open")
    return active


def _validate_trainer_stdio_allowlist() -> None:
    try:
        targets = {descriptor: os.readlink(f"/proc/self/fd/{descriptor}") for descriptor in (0, 1, 2)}
    except OSError as exc:
        raise LabelCapabilityViolation("trainer standard descriptor allowlist cannot be verified") from exc
    if targets[0] != "/dev/null" or not targets[1].startswith("pipe:[") or not targets[2].startswith("pipe:["):
        raise LabelCapabilityViolation("trainer standard descriptors do not match the explicit allowlist")


def _c_environment_entry_count() -> int:
    libc = ctypes.CDLL(None)
    environment = ctypes.POINTER(ctypes.c_char_p).in_dll(libc, "environ")
    count = 0
    while count <= 1024 and environment[count] is not None:
        count += 1
    if count > 1024:
        raise LabelCapabilityViolation("trainer C environment is unexpectedly large")
    return count


def _trainer_syscall_probe_report() -> dict[str, bool]:
    machine = platform.machine().lower()
    calls_by_architecture = {
        "x86_64": {
            "os.read": (0, (999_999, 0, 1)),
            "mmap_ACCESS_READ": (9, (0, 4096, 1, 2, 999_999, 0)),
            "os.sendfile_to_inherited_stdout": (40, (1, 999_999, 0, 1)),
            "os.splice_file_to_pipe_to_stdout": (275, (999_999, 0, 1, 0, 1, 0)),
            "os.copy_file_range": (326, (999_999, 0, 1, 0, 1, 0)),
            "os.readv": (19, (999_999, 0, 1)),
            "os.preadv": (295, (999_999, 0, 1, 0, 0)),
            "proc_self_fd_path_read": (257, (-100, ctypes.c_char_p(b"/proc/self/fd/3"), 0, 0)),
            "inherited_socket_recv": (45, (999_999, 0, 1, 0, 0, 0)),
            "inherited_directory_fd_direct_getdents64_syscall": (217, (999_999, 0, 1)),
        },
        "amd64": {
            "os.read": (0, (999_999, 0, 1)),
            "mmap_ACCESS_READ": (9, (0, 4096, 1, 2, 999_999, 0)),
            "os.sendfile_to_inherited_stdout": (40, (1, 999_999, 0, 1)),
            "os.splice_file_to_pipe_to_stdout": (275, (999_999, 0, 1, 0, 1, 0)),
            "os.copy_file_range": (326, (999_999, 0, 1, 0, 1, 0)),
            "os.readv": (19, (999_999, 0, 1)),
            "os.preadv": (295, (999_999, 0, 1, 0, 0)),
            "proc_self_fd_path_read": (257, (-100, ctypes.c_char_p(b"/proc/self/fd/3"), 0, 0)),
            "inherited_socket_recv": (45, (999_999, 0, 1, 0, 0, 0)),
            "inherited_directory_fd_direct_getdents64_syscall": (217, (999_999, 0, 1)),
        },
        "aarch64": {
            "os.read": (63, (999_999, 0, 1)),
            "mmap_ACCESS_READ": (222, (0, 4096, 1, 2, 999_999, 0)),
            "os.sendfile_to_inherited_stdout": (71, (1, 999_999, 0, 1)),
            "os.splice_file_to_pipe_to_stdout": (76, (999_999, 0, 1, 0, 1, 0)),
            "os.copy_file_range": (285, (999_999, 0, 1, 0, 1, 0)),
            "os.readv": (65, (999_999, 0, 1)),
            "os.preadv": (69, (999_999, 0, 1, 0, 0)),
            "proc_self_fd_path_read": (56, (-100, ctypes.c_char_p(b"/proc/self/fd/3"), 0, 0)),
            "inherited_socket_recv": (207, (999_999, 0, 1, 0, 0, 0)),
            "inherited_directory_fd_direct_getdents64_syscall": (61, (999_999, 0, 1)),
        },
        "arm64": {
            "os.read": (63, (999_999, 0, 1)),
            "mmap_ACCESS_READ": (222, (0, 4096, 1, 2, 999_999, 0)),
            "os.sendfile_to_inherited_stdout": (71, (1, 999_999, 0, 1)),
            "os.splice_file_to_pipe_to_stdout": (76, (999_999, 0, 1, 0, 1, 0)),
            "os.copy_file_range": (285, (999_999, 0, 1, 0, 1, 0)),
            "os.readv": (65, (999_999, 0, 1)),
            "os.preadv": (69, (999_999, 0, 1, 0, 0)),
            "proc_self_fd_path_read": (56, (-100, ctypes.c_char_p(b"/proc/self/fd/3"), 0, 0)),
            "inherited_socket_recv": (207, (999_999, 0, 1, 0, 0, 0)),
            "inherited_directory_fd_direct_getdents64_syscall": (61, (999_999, 0, 1)),
        },
    }
    calls = calls_by_architecture.get(machine)
    if calls is None:
        raise LabelCapabilityViolation(f"trainer syscall probes are unsupported on {machine}")
    libc = ctypes.CDLL(None, use_errno=True)
    report: dict[str, bool] = {}
    for route, (syscall_number, arguments) in calls.items():
        ctypes.set_errno(0)
        result = libc.syscall(syscall_number, *arguments)
        report[route] = result == -1 and ctypes.get_errno() == errno.EPERM
    return report


def _install_trainer_syscall_quarantine() -> None:
    """Permanently deny label-discovery, transfer, mapping, and child syscalls."""
    global _TRAINER_MODE
    _TRAINER_MODE = True
    denied_audit_events = {
        "open", "os.listdir", "os.scandir", "os.system", "os.posix_spawn", "os.fork",
        "os.forkpty", "os.spawn", "subprocess.Popen",
    }

    def deny_audited_surface(event: str, _arguments: tuple[Any, ...]) -> None:
        if event in denied_audit_events:
            raise LabelCapabilityViolation(f"trainer quarantine denied audited operation: {event}")

    sys.addaudithook(deny_audited_surface)
    machine = platform.machine().lower()
    blocked_by_architecture = {
        "x86_64": {
            0, 2, 4, 5, 6, 8, 16, 17, 19, 21, 40, 41, 42, 43, 45, 47,
            53, 56, 57, 58, 59, 78, 89, 217, 257, 262, 267, 269, 275, 288,
            295, 299, 307, 310, 319, 322, 326, 327, 332, 435, 437, 439,
        },
        "amd64": {
            0, 2, 4, 5, 6, 8, 16, 17, 19, 21, 40, 41, 42, 43, 45, 47,
            53, 56, 57, 58, 59, 78, 89, 217, 257, 262, 267, 269, 275, 288,
            295, 299, 307, 310, 319, 322, 326, 327, 332, 435, 437, 439,
        },
        "aarch64": {
            23, 25, 29, 48, 56, 61, 62, 63, 65, 66, 67, 69, 71, 76, 78,
            79, 198, 200, 202, 203, 207, 212, 220, 221, 243, 270, 276,
            279, 281, 285, 286, 291, 327, 435, 437, 439,
        },
        "arm64": {
            23, 25, 29, 48, 56, 61, 62, 63, 65, 66, 67, 69, 71, 76, 78,
            79, 198, 200, 202, 203, 207, 212, 220, 221, 243, 270, 276,
            279, 281, 285, 286, 291, 327, 435, 437, 439,
        },
    }
    blocked = blocked_by_architecture.get(machine)
    if blocked is None:
        raise LabelCapabilityViolation(f"trainer syscall quarantine is unsupported on {machine}")

    class SockFilter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]

    class SockFprog(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("filter", ctypes.POINTER(SockFilter))]

    mmap_syscall = 9 if machine in {"x86_64", "amd64"} else 222
    # File-backed mmap is denied, while fd=-1 anonymous allocations remain
    # available to the isolated numerical process.
    instructions = [
        SockFilter(0x20, 0, 0, 0),
        SockFilter(0x15, 0, 3, mmap_syscall),
        SockFilter(0x20, 0, 0, 48),
        SockFilter(0x15, 1, 0, 0xFFFFFFFF),
        SockFilter(0x06, 0, 0, 0x00050000 | errno.EPERM),
        SockFilter(0x20, 0, 0, 0),
    ]
    for syscall_number in sorted(blocked):
        instructions.extend((SockFilter(0x15, 0, 1, syscall_number), SockFilter(0x06, 0, 0, 0x00050000 | errno.EPERM)))
    instructions.append(SockFilter(0x06, 0, 0, 0x7FFF0000))
    filters = (SockFilter * len(instructions))(*instructions)
    program = SockFprog(len(instructions), filters)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise LabelCapabilityViolation(f"cannot set trainer no-new-privileges: errno={ctypes.get_errno()}")
    if libc.prctl(22, 2, ctypes.byref(program)) != 0:
        raise LabelCapabilityViolation(f"cannot install trainer syscall quarantine: errno={ctypes.get_errno()}")


def install_trainer_quarantine(_label_free_payload: Mapping[str, Any]) -> None:
    """Reject the obsolete inherited-process transition; trainers must clean-exec."""
    raise LabelCapabilityViolation("in-process trainer transition forbidden; use launch_trainer_clean_exec")


def _validate_trainer_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LabelCapabilityViolation("trainer payload has the wrong shape")
    try:
        return validate_label_free_snapshot(value)
    except ForecastViolation as exc:
        raise LabelCapabilityViolation("trainer payload is not a valid label-free feature snapshot") from exc


def _trainer_python_surface_probe_report() -> dict[str, bool]:
    actions = {
        "builtins.open": lambda: open("/tmp/phase4-trainer-forbidden", "rb"),
        "os.open": lambda: os.open("/tmp/phase4-trainer-forbidden", os.O_RDONLY),
        "io.open": lambda: io.open("/tmp/phase4-trainer-forbidden", "rb"),
        "pathlib.Path.open": lambda: Path("/tmp/phase4-trainer-forbidden").open("rb"),
        "pathlib.Path.read_bytes": lambda: Path("/tmp/phase4-trainer-forbidden").read_bytes(),
        "pathlib.Path.stat": lambda: Path("/tmp").stat(),
        "pathlib.Path.iterdir": lambda: list(Path("/tmp").iterdir()),
        "pathlib.Path.glob": lambda: list(Path("/tmp").glob("*")),
        "os.listdir": lambda: os.listdir("/tmp"),
        "os.scandir": lambda: list(os.scandir("/tmp")),
        "os.stat": lambda: os.stat("/tmp"),
        "glob.glob": lambda: glob.glob("/tmp/*"),
        "subprocess.run": lambda: subprocess.run(["true"], check=True),
        "os.system": lambda: os.system("true"),
        "os.posix_spawn": lambda: os.posix_spawn("/usr/bin/true", ["true"], os.environ),
        "os.spawnv": lambda: os.spawnv(os.P_WAIT, "/usr/bin/true", ["true"]),
        "os.fork": lambda: os.fork(),
        "LabelStore": lambda: LabelStore(Path("/tmp/phase4-trainer-runtime")),
    }
    report: dict[str, bool] = {}
    for route, action in actions.items():
        try:
            action()
        except (Exception, OSError):
            report[route] = True
        else:
            report[route] = False
    return report


def _trainer_clean_exec_worker(encoded_payload: str) -> int:
    source_root = Path(__file__).resolve().parents[2]
    if sys.argv != ["-c", "--trainer-clean-exec", encoded_payload]:
        raise LabelCapabilityViolation("trainer worker argv does not match the fixed bootstrap shape")
    expected_environment = {
        "PATH": _TRAINER_FIXED_PATH,
        "LANG": _TRAINER_FIXED_LOCALE,
        "LC_ALL": _TRAINER_FIXED_LOCALE,
        "P4_TRAINER_EXEC_MARKER": _TRAINER_EXEC_MARKER,
    }
    if dict(os.environ) != expected_environment:
        raise LabelCapabilityViolation("trainer worker environment does not match the fixed bootstrap environment")
    if Path.cwd().resolve() != source_root:
        raise LabelCapabilityViolation("trainer worker cwd does not match the fixed source root")
    if not (
        sys.flags.isolated == 1
        and sys.flags.ignore_environment == 1
        and sys.flags.no_user_site == 1
        and sys.flags.no_site == 1
        and sys.flags.safe_path
        and sys.path
        and Path(sys.path[0]).resolve() == source_root
        and Path(sys.executable).resolve() == Path(_TRAINER_PYTHON).resolve()
    ):
        raise LabelCapabilityViolation("trainer worker interpreter isolation flags or import root mismatch")
    observed_limits: dict[str, list[int]] = {}
    for limit, expected in _TRAINER_FIXED_LIMITS.items():
        actual = resource.getrlimit(limit)
        if actual != expected:
            raise LabelCapabilityViolation(f"trainer worker {_TRAINER_LIMIT_NAMES[limit]} mismatch")
        observed_limits[_TRAINER_LIMIT_NAMES[limit]] = [actual[0], actual[1]]
    previous_umask = os.umask(_TRAINER_FIXED_UMASK)
    if previous_umask != _TRAINER_FIXED_UMASK:
        raise LabelCapabilityViolation("trainer worker umask mismatch")
    if os.getsid(0) != os.getpid() or os.getpgrp() != os.getpid():
        raise LabelCapabilityViolation("trainer worker session isolation mismatch")
    try:
        payload_bytes = base64.b64decode(encoded_payload.encode("ascii"), validate=True)
    except (UnicodeError, ValueError, OverflowError) as exc:
        raise LabelCapabilityViolation("trainer payload encoding is invalid") from exc
    if not payload_bytes or len(payload_bytes) > _TRAINER_PAYLOAD_LIMIT:
        raise LabelCapabilityViolation("trainer payload size is invalid")
    inherited_memory_absent = "P4_TRAINER_INHERITED_MEMORY_SENTINEL" not in globals()
    _validate_trainer_stdio_allowlist()
    inherited_descriptors = _close_nonessential_file_descriptors()
    sys.argv[:] = ["phase4-trainer"]
    os.environ.clear()
    if hasattr(os, "environb"):
        os.environb.clear()
    environment_cleared = len(os.environ) == 0 and (not hasattr(os, "environb") or len(os.environb) == 0)
    c_environment_entry_count = _c_environment_entry_count()
    if not environment_cleared or c_environment_entry_count != 0:
        raise LabelCapabilityViolation("trainer worker environment could not be cleared")
    _install_trainer_syscall_quarantine()
    syscall_probes = _trainer_syscall_probe_report()
    if not all(syscall_probes.values()):
        raise LabelCapabilityViolation("trainer syscall quarantine probe failed")
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise LabelCapabilityViolation("trainer payload is not valid JSON") from exc
    payload = _validate_trainer_payload(payload)
    python_surface_probes = _trainer_python_surface_probe_report()
    if not all(python_surface_probes.values()):
        raise LabelCapabilityViolation("trainer Python surface quarantine probe failed")
    report = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_trainer_clean_exec_result",
        "status": "PASS",
        "clean_exec": True,
        "inherited_memory_absent": inherited_memory_absent,
        "nonessential_file_descriptors_closed": True,
        "inherited_nonessential_file_descriptor_count": len(inherited_descriptors),
        "standard_file_descriptor_allowlist_valid": True,
        "audited_inherited_file_descriptors_closed": True,
        "fixed_environment_validated_then_cleared": environment_cleared,
        "environment_entry_count_after_transition": len(os.environ),
        "environment_bytes_entry_count_after_transition": len(os.environb) if hasattr(os, "environb") else 0,
        "c_environment_entry_count_after_transition": c_environment_entry_count,
        "argv_after_transition": list(sys.argv),
        "fixed_cwd": str(source_root),
        "fixed_umask_octal": "0077",
        "fixed_resource_limits": observed_limits,
        "isolated_interpreter": True,
        "new_session": True,
        "quarantine_installed": True,
        "syscall_probe_count": len(syscall_probes),
        "syscall_probes": syscall_probes,
        "python_surface_probe_count": len(python_surface_probes),
        "python_surface_probes": python_surface_probes,
        "payload": payload,
    }
    os.write(1, json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return 0


def _trainer_clean_exec_command(encoded_payload: str) -> tuple[list[str], dict[str, str], str]:
    source_root = str(Path(__file__).resolve().parents[2])
    environment = {
        "PATH": _TRAINER_FIXED_PATH,
        "LANG": _TRAINER_FIXED_LOCALE,
        "LC_ALL": _TRAINER_FIXED_LOCALE,
        "P4_TRAINER_EXEC_MARKER": _TRAINER_EXEC_MARKER,
    }
    bootstrap = (
        "import sys;"
        f"sys.path.insert(0,{source_root!r});"
        "from lottery_system.phase4.label_capability import _main;"
        "raise SystemExit(_main(sys.argv[1:]))"
    )
    command = [
        _TRAINER_PRLIMIT,
        "--as=2147483648:2147483648",
        "--core=0:0",
        "--cpu=300:300",
        "--data=1073741824:1073741824",
        "--fsize=16777216:16777216",
        "--memlock=65536:65536",
        "--msgqueue=0:0",
        "--nice=0:0",
        "--nofile=256:256",
        "--nproc=64:64",
        "--rss=1073741824:1073741824",
        "--rtprio=0:0",
        "--rttime=1000000:1000000",
        "--sigpending=64:64",
        "--stack=8388608:8388608",
        "--",
        _TRAINER_PYTHON,
        "-I",
        "-S",
        "-c",
        bootstrap,
        "--trainer-clean-exec",
        encoded_payload,
    ]
    return command, environment, source_root


def launch_trainer_clean_exec(label_free_payload: Mapping[str, Any]) -> dict[str, Any]:
    """Start the trainer only by clean exec with a sanitized environment and FD set."""
    validated_payload = _validate_trainer_payload(label_free_payload)
    encoded_payload = base64.b64encode(
        json.dumps(validated_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    if len(encoded_payload) > 2 * _TRAINER_PAYLOAD_LIMIT:
        raise LabelCapabilityViolation("trainer payload size is invalid")
    command, environment, source_root = _trainer_clean_exec_command(encoded_payload)
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        cwd=source_root,
        env=environment,
        umask=_TRAINER_FIXED_UMASK,
        start_new_session=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[-400:]
        raise LabelCapabilityViolation(f"trainer clean-exec failed with exit {completed.returncode}: {detail}")
    try:
        result = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise LabelCapabilityViolation("trainer clean-exec returned invalid evidence") from exc
    if result.get("status") != "PASS" or not result.get("clean_exec") or not result.get("inherited_memory_absent"):
        raise LabelCapabilityViolation("trainer clean-exec evidence is incomplete")
    return result


def _revision_path(runtime_root: Path, revision_id: str) -> Path:
    validate_stable_id(revision_id, "result revision identity")
    return resolve_inside(runtime_root, f"result-revisions/{revision_id}.json")


def _load_revision(runtime_root: Path, revision_id: str) -> dict[str, Any]:
    path = _revision_path(runtime_root, revision_id)
    if not path.is_file():
        raise LabelCapabilityViolation("verified result revision file is missing")
    row = load_json(path, reject_floats=True)
    required = {
        "schema_version", "artifact_type", "result_revision_id", "game", "issue_id", "draw_business_date",
        "numbers", "primary_observation_id", "corroborating_observation_id", "verified_at_utc",
        "supersedes_revision_id",
    }
    if set(row) != required or row["schema_version"] != "1.0.0" or row["artifact_type"] != "phase4_result_revision":
        raise LabelCapabilityViolation("verified result revision shape mismatch")
    try:
        verify_content_id(row["result_revision_id"], "result-revision", row, excluded_fields=("result_revision_id",))
    except ValueError as exc:
        raise LabelCapabilityViolation("verified result revision identity mismatch") from exc
    if row["result_revision_id"] != revision_id:
        raise LabelCapabilityViolation("verified result revision file identity mismatch")
    return row


def _require_latest_revision(runtime_root: Path, selected: Mapping[str, Any]) -> None:
    root = resolve_inside(runtime_root, "result-revisions")
    if not root.is_dir():
        raise LabelCapabilityViolation("verified result revision root is missing")
    revisions = [load_json(path, reject_floats=True) for path in root.iterdir() if path.is_file() and path.suffix == ".json"]
    matching = [row for row in revisions if row.get("game") == selected["game"] and row.get("issue_id") == selected["issue_id"]]
    if any(row.get("supersedes_revision_id") == selected["result_revision_id"] for row in matching):
        raise LabelCapabilityViolation("requested result revision is superseded")
    if len([row for row in matching if not any(other.get("supersedes_revision_id") == row.get("result_revision_id") for other in matching)]) != 1:
        raise LabelCapabilityViolation("result revision chain does not have exactly one current head")


def _validate_result_ledger(runtime_root: Path, revision_id: str) -> str:
    ledger = AppendOnlyLedger(runtime_root, "result-revisions")
    validation = ledger.validate()
    if validation["event_count"] <= 0:
        raise LabelCapabilityViolation("verified result ledger is empty")
    view = load_json(ledger.current_view_path, reject_floats=True)
    item = view.get("objects", {}).get(revision_id)
    if item is None or item.get("event_type") != "result_revision_verified":
        raise LabelCapabilityViolation("result revision is not verified in the ledger")
    return validation["head_sha256"]


def unlock_result_label(
    runtime_root: Path,
    *,
    forecast_id: str,
    result_revision_id: str,
    label_unlocked_at: str,
    contract_id: str,
    producer_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    if contract_id != "phase4-time-contract-v1":
        raise LabelCapabilityViolation("label unlock time contract identity mismatch")
    with AdvisoryFileLock(resolve_inside(runtime_root, ".label-store.lock")):
        locked = load_locked_forecast(runtime_root, forecast_id)
        receipt = locked["lock_receipt"]
        revision = _load_revision(runtime_root, result_revision_id)
        result_head = _validate_result_ledger(runtime_root, result_revision_id)
        _require_latest_revision(runtime_root, revision)
        if revision["game"] != receipt["game"] or revision["issue_id"] != receipt["target_issue"]:
            raise LabelCapabilityViolation("result revision game/issue does not match the forecast lock")
        validate_official_result_label_times(
            prediction_locked_at=receipt["prediction_locked_at"],
            result_verified_at=revision["verified_at_utc"],
            label_unlocked_at=label_unlocked_at,
        )
        eligibility: dict[str, Any] = {
            "schema_version": "1.0.0",
            "artifact_type": "phase4_label_unlock_eligibility",
            "forecast_id": forecast_id,
            "result_revision_id": result_revision_id,
            "game": receipt["game"],
            "target_issue": receipt["target_issue"],
            "model_id": receipt["model_id"],
            "model_release_id": receipt["model_release_id"],
            "data_release_id": receipt["data_release_id"],
            "calendar_release_id": receipt["calendar_release_id"],
            "schedule_release_id": receipt["schedule_release_id"],
            "metric_contract_id": receipt["metric_contract_id"],
            "forecast_lock_receipt_id": receipt["lock_receipt_id"],
            "forecast_lock_receipt_sha256": sha256_file(resolve_inside(runtime_root, f"forecasts/{forecast_id}/lock-receipt.json")),
            "result_revision_sha256": sha256_file(_revision_path(runtime_root, result_revision_id)),
            "forecast_ledger_head_sha256": locked["ledger_head_sha256"],
            "result_ledger_head_sha256": result_head,
            "prediction_locked_at": receipt["prediction_locked_at"],
            "result_verified_at": revision["verified_at_utc"],
            "label_unlocked_at": label_unlocked_at,
            "capability_persisted": False,
            "numbers_persisted": False,
            "producer_provenance": dict(producer_provenance),
        }
        eligibility["unlock_eligibility_id"] = content_id("label-unlock", eligibility)
        if _contains_forbidden_label({key: value for key, value in eligibility.items() if key != "result_revision_id"}):
            raise LabelCapabilityViolation("unlock eligibility unexpectedly contains label data")
        path = resolve_inside(runtime_root, f"label-unlocks/{forecast_id}/{result_revision_id}.json")
        ledger = AppendOnlyLedger(runtime_root, "label-unlocks")
        validation = ledger.validate()
        if path.exists():
            if load_json(path, reject_floats=True) != eligibility:
                raise LabelCapabilityViolation("label unlock identity reuse mismatch")
            view = load_json(ledger.current_view_path, reject_floats=True)
            if eligibility["unlock_eligibility_id"] not in view.get("objects", {}):
                raise LabelCapabilityViolation("unlock eligibility file lacks a ledger event")
            return {"eligibility": eligibility, "ledger_head_sha256": validation["head_sha256"], "idempotent_resume": True}
        write_once_json(path, eligibility)
        event = ledger.append_event(
            object_id=eligibility["unlock_eligibility_id"],
            event_type="label_unlock_eligible",
            event_at_utc=label_unlocked_at,
            payload={
                "unlock_eligibility_id": eligibility["unlock_eligibility_id"],
                "forecast_id": forecast_id,
                "result_revision_id": result_revision_id,
                "eligibility_sha256": sha256_file(path),
            },
            producer_provenance=producer_provenance,
            expected_head_sha256=validation["head_sha256"],
        )
    return {"eligibility": eligibility, "ledger_head_sha256": event["head"]["event_sha256"], "idempotent_resume": False}


class _ScoringCapability:
    __slots__ = ("_token",)

    def __init__(self, token: object) -> None:
        self._token = token

    def __repr__(self) -> str:
        return "<opaque phase4 scorer capability>"

    def __reduce__(self) -> Any:
        raise TypeError("Phase 4 scorer capabilities are nonserializable")

    def __copy__(self) -> Any:
        raise TypeError("Phase 4 scorer capabilities cannot be copied")

    def __deepcopy__(self, _memo: Mapping[int, Any]) -> Any:
        raise TypeError("Phase 4 scorer capabilities cannot be deep-copied")


class LabelStore:
    def __init__(self, runtime_root: Path) -> None:
        if _TRAINER_MODE:
            raise LabelCapabilityViolation("trainer process cannot construct a label store")
        self._runtime_root = runtime_root.resolve()
        self._entries: dict[object, tuple[int, dict[str, list[int]]]] = {}
        os.register_at_fork(after_in_child=self._entries.clear)

    def acquire_for_scoring(
        self,
        *,
        forecast_id: str,
        result_revision_id: str,
        metric_contract_id: str,
        clock: str,
        expected_identity: Mapping[str, str],
    ) -> _ScoringCapability:
        if _TRAINER_MODE:
            raise LabelCapabilityViolation("trainer process cannot acquire a scorer capability")
        with AdvisoryFileLock(resolve_inside(self._runtime_root, ".label-store.lock")):
            locked = load_locked_forecast(self._runtime_root, forecast_id)
            receipt = locked["lock_receipt"]
            revision = _load_revision(self._runtime_root, result_revision_id)
            _require_latest_revision(self._runtime_root, revision)
            result_head = _validate_result_ledger(self._runtime_root, result_revision_id)
            eligibility_path = resolve_inside(self._runtime_root, f"label-unlocks/{forecast_id}/{result_revision_id}.json")
            if not eligibility_path.is_file():
                raise LabelCapabilityViolation("persisted unlock eligibility is missing")
            eligibility = load_json(eligibility_path, reject_floats=True)
            unlock_ledger = AppendOnlyLedger(self._runtime_root, "label-unlocks")
            unlock_validation = unlock_ledger.validate()
            unlock_view = load_json(unlock_ledger.current_view_path, reject_floats=True)
            item = unlock_view.get("objects", {}).get(eligibility.get("unlock_eligibility_id"))
            if item is None or item.get("event_type") != "label_unlock_eligible":
                raise LabelCapabilityViolation("unlock eligibility ledger binding is missing")
            unlock_payload = load_json(unlock_ledger.payloads_root / f"{item['payload_sha256']}.json", reject_floats=True)
            if unlock_payload.get("eligibility_sha256") != sha256_file(eligibility_path):
                raise LabelCapabilityViolation("unlock eligibility ledger/file hash mismatch")
            identity_fields = {
                "game", "target_issue", "model_id", "model_release_id", "data_release_id",
                "calendar_release_id", "schedule_release_id", "metric_contract_id",
            }
            if set(expected_identity) != identity_fields:
                raise LabelCapabilityViolation("scorer expected identity set is incomplete")
            if any(receipt.get(key) != expected_identity[key] or eligibility.get(key) != expected_identity[key] for key in identity_fields):
                raise LabelCapabilityViolation("scorer identity does not match lock/unlock identities")
            if metric_contract_id != receipt["metric_contract_id"] or revision["game"] != receipt["game"] or revision["issue_id"] != receipt["target_issue"]:
                raise LabelCapabilityViolation("scorer forecast/result/metric identity mismatch")
            if eligibility.get("forecast_ledger_head_sha256") != locked["ledger_head_sha256"]:
                raise LabelCapabilityViolation("forecast ledger head changed since unlock eligibility")
            if eligibility.get("result_ledger_head_sha256") != result_head:
                raise LabelCapabilityViolation("result ledger head changed since unlock eligibility")
            validate_official_result_label_times(
                prediction_locked_at=receipt["prediction_locked_at"],
                result_verified_at=revision["verified_at_utc"],
                label_unlocked_at=eligibility["label_unlocked_at"],
            )
            from .time_gate import parse_utc

            if parse_utc(clock, "scorer clock") < parse_utc(eligibility["label_unlocked_at"], "label unlock time"):
                raise LabelCapabilityViolation("scorer clock precedes label unlock")
            del unlock_validation
            token = object()
            self._entries[token] = (
                os.getpid(),
                {"front": list(revision["numbers"]["front"]), "back": list(revision["numbers"]["back"])},
            )
            return _ScoringCapability(token)

    def read_once(self, capability: object) -> dict[str, list[int]]:
        if type(capability) is not _ScoringCapability:
            raise LabelCapabilityViolation("label read requires an opaque scorer capability")
        entry = self._entries.get(capability._token)
        if entry is None or entry[0] != os.getpid():
            raise LabelCapabilityViolation("scorer capability is from another store or process")
        _, numbers = self._entries.pop(capability._token)
        return {"front": list(numbers["front"]), "back": list(numbers["back"])}


def _main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "--trainer-clean-exec":
        try:
            return _trainer_clean_exec_worker(argv[1])
        except LabelCapabilityViolation as exc:
            os.write(2, str(exc).encode("utf-8", errors="replace"))
            return 20
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
