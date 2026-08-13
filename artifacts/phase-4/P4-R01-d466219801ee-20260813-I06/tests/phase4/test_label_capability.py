from __future__ import annotations

import base64
import json
import copy
import gc
import glob
import inspect
import io
import os
import pathlib
import pickle
try:
    import resource
except ImportError:
    resource = None
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from lottery_system.phase4.commands.forecast import _snapshot_from_ticks
from lottery_system.phase4.forecast import generate_forecast
from lottery_system.phase4.label_capability import (
    LabelCapabilityViolation,
    LabelStore,
    _trainer_clean_exec_command,
    install_trainer_quarantine,
    launch_trainer_clean_exec,
    unlock_result_label,
)
import lottery_system.phase4.label_capability as label_capability_module
from lottery_system.phase4.ledger import AppendOnlyLedger
from lottery_system.phase4.lock import lock_forecast
from lottery_system.phase4.rules import game_rule
from lottery_system.phase4.serialization import canonical_json_bytes
from lottery_system.phase4.storage import write_once_json
from lottery_system.phase4.verification import normalized_fact, verify_result_revision


PROVENANCE = {
    "producer_actor_id": "p4-implementation-author-i01", "task_id": "T05",
    "session_id": "/root/implementation_author", "source_commit": "f8a7a6abb46a55f8fa17e5ae3280c5c5432c363b",
    "path": "tests/phase4/test_label_capability.py", "role": "implementation_author",
}


def make_revision(numbers: dict[str, list[int]], *, verified: str, supersedes: str | None = None) -> dict:
    facts = [normalized_fact(
        source_id=source, game="ssq", observation_id=f"obs-{source}-{numbers['back'][0]}-{verified[11:13]}",
        issue_id="2099999", draw_business_date="2026-01-02",
        front_numbers=numbers["front"], back_numbers=numbers["back"],
    ) for source in ("swlc", "ydniu")]
    return verify_result_revision(facts[0], facts[1], verified_at_utc=verified, supersedes_revision_id=supersedes)


def setup_runtime(base: Path) -> tuple[dict, dict]:
    rule = game_rule("ssq")
    generated = generate_forecast(_snapshot_from_ticks({"rule_id": rule.rule_id}, "ssq", "M0", [0] * rule.front_n, [0] * rule.back_n))
    forecast = generated["forecast"]
    write_once_json(base / f"data-releases/{forecast['data_release_id']}/data-release.json", {"data_release_id": forecast["data_release_id"]})
    write_once_json(base / f"calendar-releases/{forecast['calendar_release_id']}/calendar.json", {"calendar_release_id": forecast["calendar_release_id"]})
    lock_forecast(
        base, generated, prediction_locked_at="2026-01-02T09:00:00Z",
        hard_deadline_at="2026-01-02T10:00:00Z", producer_provenance=PROVENANCE,
    )
    revision = make_revision({"front": [1, 2, 3, 4, 5, 6], "back": [1]}, verified="2026-01-02T14:31:00Z")
    path = base / f"result-revisions/{revision['result_revision_id']}.json"
    write_once_json(path, revision)
    ledger = AppendOnlyLedger(base, "result-revisions")
    ledger.append_event(
        object_id=revision["result_revision_id"], event_type="result_revision_verified",
        event_at_utc=revision["verified_at_utc"],
        payload={"result_revision_id": revision["result_revision_id"], "sha256": __import__("hashlib").sha256(path.read_bytes()).hexdigest()},
        producer_provenance=PROVENANCE, expected_head_sha256=None,
    )
    unlock_result_label(
        base, forecast_id=forecast["forecast_id"], result_revision_id=revision["result_revision_id"],
        label_unlocked_at="2026-01-02T14:32:00Z", contract_id="phase4-time-contract-v1",
        producer_provenance=PROVENANCE,
    )
    return generated, revision


def expected_identity(generated: dict) -> dict[str, str]:
    forecast = generated["forecast"]
    return {key: forecast[key] for key in (
        "game", "target_issue", "model_id", "model_release_id", "data_release_id",
        "calendar_release_id", "schedule_release_id", "metric_contract_id",
    )}


@unittest.skipUnless(resource is not None and os.name == "posix", "POSIX trainer isolation boundary")
class LabelCapabilityTests(unittest.TestCase):
    def test_persisted_unlock_is_number_free_and_capability_is_opaque_one_shot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            generated, revision = setup_runtime(runtime)
            receipt_path = runtime / f"label-unlocks/{generated['forecast']['forecast_id']}/{revision['result_revision_id']}.json"
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
            def keys(value: object) -> set[str]:
                if isinstance(value, dict):
                    return set(value) | set().union(*(keys(item) for item in value.values()))
                if isinstance(value, list):
                    return set().union(*(keys(item) for item in value))
                return set()
            self.assertNotIn("numbers", keys(payload))
            self.assertFalse(payload["capability_persisted"])
            store = LabelStore(runtime)
            capability = store.acquire_for_scoring(
                forecast_id=generated["forecast"]["forecast_id"], result_revision_id=revision["result_revision_id"],
                metric_contract_id="phase4-metric-v1", clock="2026-01-02T14:33:00Z",
                expected_identity=expected_identity(generated),
            )
            with self.assertRaises(TypeError):
                pickle.dumps(capability)
            with self.assertRaises(TypeError):
                copy.copy(capability)
            with self.assertRaises(TypeError):
                copy.deepcopy(capability)
            with self.assertRaises(TypeError):
                vars(capability)
            self.assertNotIn("_numbers", dir(capability))
            with self.assertRaises(AttributeError):
                getattr(capability, "_numbers")
            with self.assertRaises(AttributeError):
                object.__getattribute__(capability, "_numbers")
            self.assertNotIn(revision["numbers"], [value for _, value in inspect.getmembers(capability)])
            self.assertNotIn(revision["numbers"], gc.get_referents(capability))
            self.assertEqual(store.read_once(capability), revision["numbers"])
            with self.assertRaises(LabelCapabilityViolation):
                store.read_once(capability)

    def test_cross_pid_reuse_fails_and_fresh_pid_reacquires(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            generated, revision = setup_runtime(runtime)
            store = LabelStore(runtime)
            capability = store.acquire_for_scoring(
                forecast_id=generated["forecast"]["forecast_id"], result_revision_id=revision["result_revision_id"],
                metric_contract_id="phase4-metric-v1", clock="2026-01-02T14:33:00Z",
                expected_identity=expected_identity(generated),
            )
            pid = os.fork()
            if pid == 0:
                try:
                    leaked = any(value == revision["numbers"] for value in gc.get_referents(capability))
                    if leaked or hasattr(capability, "_numbers") or store._entries:
                        os._exit(4)
                    store.read_once(capability)
                except LabelCapabilityViolation:
                    os._exit(0)
                os._exit(1)
            self.assertEqual(os.waitpid(pid, 0)[1], 0)

            probe = {
                "runtime": str(runtime), "forecast_id": generated["forecast"]["forecast_id"],
                "result_revision_id": revision["result_revision_id"],
                "expected_identity": expected_identity(generated), "expected_numbers": revision["numbers"],
            }
            script = (
                "import json,sys; from pathlib import Path; "
                "from lottery_system.phase4.label_capability import LabelStore; "
                "p=json.loads(sys.argv[1]); s=LabelStore(Path(p['runtime'])); "
                "c=s.acquire_for_scoring(forecast_id=p['forecast_id'],result_revision_id=p['result_revision_id'],"
                "metric_contract_id='phase4-metric-v1',clock='2026-01-02T14:33:00Z',expected_identity=p['expected_identity']); "
                "raise SystemExit(0 if s.read_once(c)==p['expected_numbers'] else 3)"
            )
            environment = dict(os.environ)
            environment["PYTHONPATH"] = "src"
            completed = subprocess.run(
                [sys.executable, "-c", script, json.dumps(probe, sort_keys=True)],
                cwd=Path(__file__).resolve().parents[2], env=environment, check=False,
            )
            self.assertEqual(completed.returncode, 0)

            pid = os.fork()
            if pid == 0:
                try:
                    fresh = LabelStore(runtime)
                    fresh_capability = fresh.acquire_for_scoring(
                        forecast_id=generated["forecast"]["forecast_id"], result_revision_id=revision["result_revision_id"],
                        metric_contract_id="phase4-metric-v1", clock="2026-01-02T14:33:00Z",
                        expected_identity=expected_identity(generated),
                    )
                    observed = fresh.read_once(fresh_capability)
                    os._exit(0 if observed == revision["numbers"] else 2)
                except Exception:
                    os._exit(3)
            self.assertEqual(os.waitpid(pid, 0)[1], 0)

    def test_wrong_identity_tamper_and_superseded_revision_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            generated, revision = setup_runtime(runtime)
            store = LabelStore(runtime)
            wrong = expected_identity(generated)
            wrong["game"] = "dlt"
            with self.assertRaises(LabelCapabilityViolation):
                store.acquire_for_scoring(
                    forecast_id=generated["forecast"]["forecast_id"], result_revision_id=revision["result_revision_id"],
                    metric_contract_id="phase4-metric-v1", clock="2026-01-02T14:33:00Z", expected_identity=wrong,
                )
            eligibility = runtime / f"label-unlocks/{generated['forecast']['forecast_id']}/{revision['result_revision_id']}.json"
            original = eligibility.read_bytes()
            eligibility.write_bytes(canonical_json_bytes({"tampered": True}))
            with self.assertRaises(LabelCapabilityViolation):
                store.acquire_for_scoring(
                    forecast_id=generated["forecast"]["forecast_id"], result_revision_id=revision["result_revision_id"],
                    metric_contract_id="phase4-metric-v1", clock="2026-01-02T14:33:00Z",
                    expected_identity=expected_identity(generated),
                )
            eligibility.write_bytes(original)
            successor = make_revision(
                {"front": [1, 2, 3, 4, 5, 7], "back": [1]}, verified="2026-01-02T14:34:00Z",
                supersedes=revision["result_revision_id"],
            )
            path = runtime / f"result-revisions/{successor['result_revision_id']}.json"
            write_once_json(path, successor)
            ledger = AppendOnlyLedger(runtime, "result-revisions")
            head = ledger.read_head()
            ledger.append_event(
                object_id=successor["result_revision_id"], event_type="result_revision_verified",
                event_at_utc=successor["verified_at_utc"], payload={"result_revision_id": successor["result_revision_id"]},
                producer_provenance=PROVENANCE, expected_head_sha256=head.event_sha256,
            )
            with self.assertRaises(LabelCapabilityViolation):
                store.acquire_for_scoring(
                    forecast_id=generated["forecast"]["forecast_id"], result_revision_id=revision["result_revision_id"],
                    metric_contract_id="phase4-metric-v1", clock="2026-01-02T14:35:00Z",
                    expected_identity=expected_identity(generated),
                )

    def test_trainer_requires_clean_exec_and_denies_all_audited_routes(self) -> None:
        rule = game_rule("ssq")
        snapshot = _snapshot_from_ticks(
            {"rule_id": rule.rule_id}, "ssq", "M0", [0] * rule.front_n, [0] * rule.back_n,
        )
        with self.assertRaises(LabelCapabilityViolation):
            install_trainer_quarantine(snapshot)
        with self.assertRaises(LabelCapabilityViolation):
            launch_trainer_clean_exec({"numbers": {"front": [1], "back": [1]}})
        label_capability_module.P4_TRAINER_INHERITED_MEMORY_SENTINEL = "LABEL-SECRET-123"
        try:
            result = launch_trainer_clean_exec(snapshot)
        finally:
            del label_capability_module.P4_TRAINER_INHERITED_MEMORY_SENTINEL
        self.assertTrue(result["clean_exec"])
        self.assertTrue(result["inherited_memory_absent"])
        self.assertTrue(result["nonessential_file_descriptors_closed"])
        self.assertTrue(result["standard_file_descriptor_allowlist_valid"])
        self.assertEqual(result["syscall_probe_count"], 10)
        self.assertEqual(sum(result["syscall_probes"].values()), 10)
        self.assertEqual(result["python_surface_probe_count"], 18)
        self.assertEqual(sum(result["python_surface_probes"].values()), 18)
        self.assertEqual(result["payload"]["feature_snapshot_id"], snapshot["feature_snapshot_id"])

    def test_clean_exec_closes_explicitly_inherited_file_directory_and_socket_fds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            label_path = root / "label-secret.txt"
            label_path.write_bytes(b"LABEL-SECRET-123")
            file_descriptor = os.open(label_path, os.O_RDONLY)
            directory_descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            first_socket, second_socket = socket.socketpair()
            descriptors = (file_descriptor, directory_descriptor, first_socket.fileno(), second_socket.fileno())
            rule = game_rule("ssq")
            snapshot = _snapshot_from_ticks(
                {"rule_id": rule.rule_id}, "ssq", "M0", [0] * rule.front_n, [0] * rule.back_n,
            )
            payload = base64.b64encode(json.dumps(
                snapshot, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).decode("ascii")
            command, environment, source_root = _trainer_clean_exec_command(payload)
            try:
                completed = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    close_fds=True, pass_fds=descriptors, cwd=source_root, env=environment,
                    umask=0o077, start_new_session=True, check=False,
                )
            finally:
                os.close(file_descriptor)
                os.close(directory_descriptor)
                first_socket.close()
                second_socket.close()
            self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
            result = json.loads(completed.stdout.decode("utf-8"))
            self.assertTrue(result["audited_inherited_file_descriptors_closed"])
            self.assertEqual(result["inherited_nonessential_file_descriptor_count"], 4)
            self.assertEqual(sum(result["syscall_probes"].values()), 10)

    def test_clean_exec_environment_argv_cwd_umask_and_rlimits_are_fixed(self) -> None:
        rule = game_rule("ssq")
        snapshot = _snapshot_from_ticks(
            {"rule_id": rule.rule_id}, "ssq", "M0", [0] * rule.front_n, [0] * rule.back_n,
        )
        parent_mutations = {
            "PATH": "LABEL-SECRET-PARENT-PATH",
            "HOME": "LABEL-SECRET-HOME",
            "TMPDIR": "LABEL-SECRET-TMPDIR",
            "XDG_CONFIG_HOME": "LABEL-SECRET-XDG",
            "PIP_CONFIG_FILE": "LABEL-SECRET-PIP",
            "PYTHONUSERBASE": "LABEL-SECRET-USERBASE",
            "PYTHONNOUSERSITE": "LABEL-SECRET-NOUSERSITE",
            "PYTHONINSPECT": "LABEL-SECRET-INSPECT",
            "PYTHONSTARTUP": "LABEL-SECRET-STARTUP",
            "PYTHONWARNINGS": "LABEL-SECRET-WARNINGS",
            "PYTHONMALLOC": "LABEL-SECRET-MALLOC",
            "PYTHONBREAKPOINT": "LABEL-SECRET-BREAKPOINT",
            "LANG": "LABEL-SECRET-LANG",
            "LC_ALL": "LABEL-SECRET-LC",
            "P4_SECRET": "LABEL-SECRET-P4",
            "AWS_CONFIG_FILE": "LABEL-SECRET-AWS",
        }
        old_umask = os.umask(0o022)
        old_nofile = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, old_nofile[1]))
        try:
            with mock.patch.dict(os.environ, parent_mutations, clear=False):
                result = launch_trainer_clean_exec(snapshot)
        finally:
            resource.setrlimit(resource.RLIMIT_NOFILE, old_nofile)
            os.umask(old_umask)
        self.assertTrue(result["fixed_environment_validated_then_cleared"])
        self.assertEqual(result["environment_entry_count_after_transition"], 0)
        self.assertEqual(result["environment_bytes_entry_count_after_transition"], 0)
        self.assertEqual(result["c_environment_entry_count_after_transition"], 0)
        self.assertEqual(result["argv_after_transition"], ["phase4-trainer"])
        self.assertEqual(result["fixed_umask_octal"], "0077")
        self.assertEqual(result["fixed_resource_limits"]["RLIMIT_NOFILE"], [256, 256])
        self.assertTrue(result["isolated_interpreter"])
        self.assertTrue(result["new_session"])
        self.assertEqual(result["inherited_nonessential_file_descriptor_count"], 0)

        encoded = base64.b64encode(json.dumps(
            snapshot, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).decode("ascii")
        command, fixed_environment, fixed_cwd = _trainer_clean_exec_command(encoded)

        def run_worker(*, environment: dict[str, str] | None = None, command_value: list[str] | None = None,
                       cwd: str | None = None, umask: int = 0o077) -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                command_value or command,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                close_fds=True, cwd=cwd or fixed_cwd, env=environment or fixed_environment,
                umask=umask, start_new_session=True, check=False,
            )

        environment_mutations = [
            ("PATH", "LABEL-SECRET-PATH"),
            ("PYTHONPATH", "LABEL-SECRET-PYTHONPATH"),
            ("LC_CTYPE", "LABEL-SECRET-LC"),
            ("PYTHONHASHSEED", "123456"),
            ("HOME", "LABEL-SECRET-HOME"),
            ("TMPDIR", "LABEL-SECRET-TMPDIR"),
            ("XDG_CONFIG_HOME", "LABEL-SECRET-XDG"),
            ("PIP_CONFIG_FILE", "LABEL-SECRET-PIP"),
            ("PYTHONUSERBASE", "LABEL-SECRET-USERBASE"),
            ("PYTHONNOUSERSITE", "LABEL-SECRET-NOUSERSITE"),
            ("PYTHONINSPECT", "LABEL-SECRET-INSPECT"),
            ("PYTHONSTARTUP", "LABEL-SECRET-STARTUP"),
            ("PYTHONWARNINGS", "LABEL-SECRET-WARNINGS"),
            ("PYTHONMALLOC", "LABEL-SECRET-MALLOC"),
            ("PYTHONBREAKPOINT", "LABEL-SECRET-BREAKPOINT"),
            ("LANG", "LABEL-SECRET-LANG"),
            ("LC_ALL", "LABEL-SECRET-LC-ALL"),
            ("P4_SECRET", "LABEL-SECRET-P4"),
            ("AWS_CONFIG_FILE", "LABEL-SECRET-AWS"),
            ("P4_TRAINER_AUDIT_FDS", "999999,999999"),
            ("P4_TRAINER_AUDIT_FDS", "-1"),
            ("P4_TRAINER_AUDIT_FDS", "x"),
            ("P4_TRAINER_AUDIT_FDS", "999999999999999999999"),
        ]
        for key, value in environment_mutations:
            mutated = dict(fixed_environment)
            mutated[key] = value
            with self.subTest(environment_key=key, environment_value=value):
                self.assertEqual(run_worker(environment=mutated).returncode, 20)

        extra_argv = list(command) + ["LABEL-SECRET-EXTRA-ARGV"]
        self.assertEqual(run_worker(command_value=extra_argv).returncode, 2)
        invalid_command, _, _ = _trainer_clean_exec_command("!!!")
        self.assertEqual(run_worker(command_value=invalid_command).returncode, 20)
        forbidden = base64.b64encode(b'{"numbers":{"front":[1],"back":[1]}}').decode("ascii")
        forbidden_command, _, _ = _trainer_clean_exec_command(forbidden)
        self.assertEqual(run_worker(command_value=forbidden_command).returncode, 20)
        with tempfile.TemporaryDirectory(prefix="LABEL-SECRET-CWD-") as arbitrary_cwd:
            self.assertEqual(run_worker(cwd=arbitrary_cwd).returncode, 20)
        self.assertEqual(run_worker(umask=0o022).returncode, 20)
        wrong_limits = ["--nofile=64:64" if item.startswith("--nofile=") else item for item in command]
        self.assertEqual(run_worker(command_value=wrong_limits).returncode, 20)


if __name__ == "__main__":
    unittest.main()
