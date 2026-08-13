from __future__ import annotations

import bisect
import copy
import gzip
import hashlib
import itertools
from functools import lru_cache
from collections.abc import Mapping
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_EVEN, localcontext
from pathlib import Path
from typing import Any

from ..identity import content_id, validate_stable_id
from ..ledger import AppendOnlyLedger
from ..serialization import canonical_json_bytes, canonical_sha256, decimal_string, load_json, sha256_bytes, sha256_file
from ..storage import AdvisoryFileLock, IdentityReuseError, atomic_replace_json, resolve_inside, write_once_bytes, write_once_json
from .alpha import AlphaViolation, make_spend_event, reduce_alpha_events
from .proposal import build_decision, build_experiment, zero_experiment_decision
from .registry import ResearchRegistryViolation, apply_registered_diff, build_candidate
from .sequential import SequentialViolation, reduce_e_process


class ResearchControllerViolation(ValueError):
    exit_code = 5


_DEVELOPMENT_GAMES = ("dlt", "ssq")
_DEVELOPMENT_WORLDS = ("uniform", "static_bias", "slow_drift", "useful_feature")
_DEVELOPMENT_Q = (1536, 1792, 2048)
_QUALIFICATION_DOMAINS = ("development", "power-confirmation", "formal-qualification")
_TWO_256 = 1 << 256


@lru_cache(maxsize=1)
def _scientific_controller_identity_cached() -> dict[str, Any]:
    package_root = Path(__file__).resolve().parents[1]
    worker_path = package_root / "research/worker.py"
    if not worker_path.is_file():
        raise ResearchControllerViolation("scientific controller worker is not installed")
    body: dict[str, Any] = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_scientific_controller_identity",
        "protocol": "phase4_scientific_single_sequence_json_v1",
        "argv": ["python3", "-m", "lottery_system.phase4.research.worker"],
        "controller_source_path": "src/lottery_system/phase4/research/controller.py",
        "controller_source_sha256": sha256_file(Path(__file__)),
        "worker_source_path": "src/lottery_system/phase4/research/worker.py",
        "worker_source_sha256": sha256_file(worker_path),
        "cycles_per_sequence": 150,
        "input_modes": ["seed", "raw_draws"],
        "network_allowed": False,
        "independent_module_import_allowed": False,
    }
    body["controller_identity_id"] = content_id("scientific-controller", body)
    return body


def scientific_controller_identity() -> dict[str, Any]:
    return copy.deepcopy(_scientific_controller_identity_cached())


def qualification_design(q: int) -> dict[str, Any]:
    if isinstance(q, bool) or q not in _DEVELOPMENT_Q:
        raise ResearchControllerViolation("qualification effect is outside the frozen menu")
    body: dict[str, Any] = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_qualification_design",
        "probability_family": "P4E1",
        "scale": 1024,
        "small_space": {"N": 10, "k": 3, "space_size": 120},
        "cycles_per_sequence": 150,
        "q": q,
        "static_bias": {"effect_ticks": q},
        "slow_drift": {"effect_ticks": q, "ramp_cycles": 100, "rounding": "ROUND_HALF_EVEN"},
        "useful_feature": {"effect_ticks": q, "context": "strict_alternation_75_75_context_fixed_before_draw"},
        "alpha_first": "0.003",
        "minimum_look": 30,
        "maximum_look": 150,
        "controller_identity": scientific_controller_identity(),
    }
    body["design_id"] = content_id("qualification-design", body)
    return body


def _validated_scientific_design(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or isinstance(value.get("q"), bool):
        raise ResearchControllerViolation("scientific controller design is invalid")
    base = qualification_design(value["q"])
    if value == base:
        return base
    selected_keys = set(base) | {"development_manifest_id", "selection_receipt_id", "non_formal"}
    if set(value) != selected_keys or any(value.get(key) != expected for key, expected in base.items()):
        raise ResearchControllerViolation("scientific controller design is not content-derived from installed code")
    validate_stable_id(value["development_manifest_id"], "development manifest identity")
    validate_stable_id(value["selection_receipt_id"], "development selection receipt identity")
    if value["non_formal"] is not True:
        raise ResearchControllerViolation("selected scientific design must remain explicitly non-formal")
    return base


def derive_qualification_seed(design_id: str, domain: str, game: str, world: str, sequence_ordinal: int) -> int:
    validate_stable_id(design_id, "qualification design identity")
    if domain not in _QUALIFICATION_DOMAINS:
        raise ResearchControllerViolation("qualification seed domain is not registered")
    if game not in _DEVELOPMENT_GAMES or world not in _DEVELOPMENT_WORLDS:
        raise ResearchControllerViolation("qualification seed cell is not registered")
    if isinstance(sequence_ordinal, bool) or not isinstance(sequence_ordinal, int) or sequence_ordinal < 1:
        raise ResearchControllerViolation("sequence ordinal must be a positive integer")
    material = f"P4-SEED-v2|{design_id}|{domain}|{game}|{world}|{sequence_ordinal}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest(), "big")


def _fixture_seed(fixture_id: str, design_id: str, game: str, world: str, sequence_ordinal: int) -> int:
    validate_stable_id(fixture_id, "registered fixture identity")
    material = f"P4-FIXTURE-SEED-v1|{fixture_id}|{design_id}|{game}|{world}|{sequence_ordinal}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest(), "big")


def _normalized_effect_ticks(q: int, *, sign: int = 1) -> tuple[int, ...]:
    raw = tuple(sign * q * value for value in (1, 1, 1, 0, 0, 0, 0, -1, -1, -1))
    ticks = tuple(value - raw[0] for value in raw)
    if ticks[0] != 0 or min(ticks) < -4096 or max(ticks) > 4096:
        raise ResearchControllerViolation("qualification ticks exceed the frozen normalized bounds")
    return ticks


def _rounded_ramp(q: int, look: int) -> int:
    with localcontext() as context:
        context.prec = 80
        return int(
            (Decimal(q) * Decimal(min(look, 100)) / Decimal(100)).quantize(
                Decimal(1), rounding=ROUND_HALF_EVEN
            )
        )


@lru_cache(maxsize=256)
def _distribution(ticks: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[Decimal, ...], tuple[Decimal, ...]]:
    combinations = tuple(itertools.combinations(range(10), 3))
    with localcontext() as context:
        context.prec = 80
        weights = tuple((Decimal(sum(ticks[index] for index in ticket)) / Decimal(1024)).exp() for ticket in combinations)
        partition = sum(weights, Decimal(0))
        probabilities = tuple(+(weight / partition) for weight in weights)
        ratios = tuple(+(probability * Decimal(120)) for probability in probabilities)
        cumulative = Decimal(0)
        cutoffs: list[int] = []
        for probability in probabilities[:-1]:
            cumulative += probability
            cutoffs.append(int((cumulative * Decimal(_TWO_256)).to_integral_value(rounding=ROUND_FLOOR)))
        cutoffs.append(_TWO_256)
        return tuple(cutoffs), ratios, tuple(+ratio.ln() for ratio in ratios)


def _observation_word(seed: int, look: int) -> int:
    return int.from_bytes(hashlib.sha256(seed.to_bytes(32, "big") + look.to_bytes(4, "big")).digest(), "big")


def _distribution_for(world: str, q: int, look: int) -> tuple[tuple[int, ...], tuple[Decimal, ...], tuple[Decimal, ...]]:
    if world == "uniform":
        with localcontext() as context:
            context.prec = 80
            probability = Decimal(1) / Decimal(120)
            cutoffs = tuple(((index + 1) * _TWO_256) // 120 for index in range(119)) + (_TWO_256,)
            ratios = (Decimal(1),) * 120
            logs = (Decimal(0),) * 120
            return cutoffs, ratios, logs
    if world == "static_bias":
        ticks = _normalized_effect_ticks(q)
    elif world == "slow_drift":
        ticks = _normalized_effect_ticks(_rounded_ramp(q, look))
    elif world == "useful_feature":
        ticks = _normalized_effect_ticks(q, sign=1 if look % 2 else -1)
    else:
        raise ResearchControllerViolation("qualification world is not registered")
    return _distribution(ticks)


def _family_world(family: str) -> str:
    try:
        return {
            "static_parameter": "static_bias",
            "slow_drift_parameter": "slow_drift",
            "context_feature": "useful_feature",
        }[family]
    except KeyError as exc:
        raise ResearchControllerViolation("qualification family is not registered") from exc


def _reduce_development_sequence(
    *, design: Mapping[str, Any], game: str, world: str, sequence_ordinal: int, seed: int | None,
    seed_domain: str | None, raw_draws: list[int] | None = None,
) -> dict[str, Any]:
    q = design["q"]
    families = (
        ("static_parameter", "slow_drift_parameter", "context_feature")
        if world == "uniform" else
        ({"static_bias": "static_parameter", "slow_drift": "slow_drift_parameter", "useful_feature": "context_feature"}[world],)
    )
    with localcontext() as context:
        context.prec = 80
        threshold = Decimal(1) / Decimal("0.003")
        log_threshold = threshold.ln()
        log_values = {family: Decimal(0) for family in families}
        crossing: tuple[int, str] | None = None
        draws: list[int] = []
        look_rows: list[dict[str, Any]] = []
        if raw_draws is not None and (
            len(raw_draws) != 150
            or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 or value >= 120 for value in raw_draws)
        ):
            raise ResearchControllerViolation("raw scientific draws must be exactly 150 registered outcome indices")
        if raw_draws is None and (isinstance(seed, bool) or not isinstance(seed, int) or seed < 0 or seed >= _TWO_256):
            raise ResearchControllerViolation("scientific sequence seed must be an unsigned 256-bit integer")
        controller_identity_id = design.get("controller_identity", {}).get("controller_identity_id")
        if controller_identity_id != scientific_controller_identity()["controller_identity_id"]:
            raise ResearchControllerViolation("qualification design is not bound to the installed scientific controller")
        for look in range(1, 151):
            if raw_draws is None:
                draw_cutoffs, _, _ = _distribution_for(world, q, look)
                assert seed is not None
                outcome = bisect.bisect_right(draw_cutoffs, _observation_word(seed, look))
                if outcome >= 120:
                    outcome = 119
            else:
                outcome = raw_draws[look - 1]
            draws.append(outcome)
            if crossing is not None:
                continue
            family_rows = []
            for family in families:
                _, ratios, logs = _distribution_for(_family_world(family), q, look)
                log_values[family] += logs[outcome]
                crossed = look >= 30 and log_values[family] >= log_threshold
                family_rows.append({
                    "family": family,
                    "lr_increment": decimal_string(ratios[outcome]),
                    "log_e_value": decimal_string(log_values[family]),
                    "crossed": crossed,
                })
                if crossed and crossing is None:
                    crossing = (look, family)
            look_rows.append({"look": look, "outcome_index": outcome, "families": family_rows})
        recovered = crossing is not None
        expected_family = None if world == "uniform" else families[0]
        correct_recovery = recovered and crossing[1] == expected_family
        event = recovered if world == "uniform" else correct_recovery
        terminal_name = "false_shadow_proposal" if world == "uniform" and event else (
            "shadow_candidate" if event else "rejected"
        )
        shadow_body = {
            "game": game, "design_id": design["design_id"], "q": q,
            "family": None if crossing is None else crossing[1], "direction": "registered_positive",
        }
        terminal: dict[str, Any] = {
            "schema_version": "1.0.0",
            "artifact_type": "phase4_development_sequence_terminal",
            "sequence_id": f"{design['design_id']}:{game}:{world}:{sequence_ordinal}",
            "sequence_ordinal": sequence_ordinal,
            "game": game,
            "world": world,
            "design_id": design["design_id"],
            "controller_identity_id": controller_identity_id,
            "q": q,
            "input_mode": "raw_draws" if raw_draws is not None else "seed",
            "seed_domain": seed_domain,
            "seed_uint256": None if seed is None else str(seed),
            "draw_observation_count": 150,
            "outcome_indices": draws,
            "draws_sha256": canonical_sha256(draws),
            "context_bits": [1 if look % 2 else 0 for look in range(1, 151)] if world == "useful_feature" else None,
            "look_count": len(look_rows),
            "first_crossing_look": None if crossing is None else crossing[0],
            "crossing_family": None if crossing is None else crossing[1],
            "terminal": terminal_name,
            "sequence_event": event,
            "non_statistical_guards_pass": True,
            "next_shadow_sha256": None if crossing is None else canonical_sha256(shadow_body),
            "looks_sha256": canonical_sha256(look_rows),
            "final_log_e_values": {family: decimal_string(log_values[family]) for family in families},
        }
        terminal["terminal_sha256"] = canonical_sha256(terminal)
        return terminal


def execute_scientific_sequence_request(request: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "artifact_type", "request_id", "expected_controller_identity_id",
        "design", "game", "world", "sequence_ordinal", "seed_domain", "input_mode",
        "seed_uint256", "seed_commitment_sha256", "raw_draws",
    }
    if (
        set(request) != required
        or request.get("schema_version") != "1.0.0"
        or request.get("artifact_type") != "phase4_scientific_controller_request"
    ):
        raise ResearchControllerViolation("scientific controller request shape or identity is invalid")
    request_id = validate_stable_id(request["request_id"], "scientific controller request identity")
    identity = scientific_controller_identity()
    if request["expected_controller_identity_id"] != identity["controller_identity_id"]:
        raise ResearchControllerViolation("scientific controller identity differs from the caller's frozen command")
    supplied_design = request["design"]
    design = _validated_scientific_design(supplied_design)
    game, world = request["game"], request["world"]
    ordinal, seed_domain = request["sequence_ordinal"], request["seed_domain"]
    if game not in _DEVELOPMENT_GAMES or world not in _DEVELOPMENT_WORLDS:
        raise ResearchControllerViolation("scientific controller cell is not registered")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
        raise ResearchControllerViolation("scientific controller sequence ordinal is invalid")
    if seed_domain not in _QUALIFICATION_DOMAINS:
        raise ResearchControllerViolation("scientific controller seed domain is not registered")
    commitment = request["seed_commitment_sha256"]
    if not isinstance(commitment, str) or len(commitment) != 64 or any(character not in "0123456789abcdef" for character in commitment):
        raise ResearchControllerViolation("scientific controller seed commitment is invalid")
    if request["input_mode"] == "seed":
        if request["raw_draws"] is not None or not isinstance(request["seed_uint256"], str):
            raise ResearchControllerViolation("seed mode must contain only the canonical explicit seed")
        seed_text = request["seed_uint256"]
        if not seed_text.isdigit() or (len(seed_text) > 1 and seed_text.startswith("0")):
            raise ResearchControllerViolation("scientific controller seed is not canonical uint256 text")
        seed = int(seed_text)
        if seed != derive_qualification_seed(design["design_id"], seed_domain, game, world, ordinal):
            raise ResearchControllerViolation("explicit scientific controller seed does not match P4-SEED-v2")
        if commitment != sha256_bytes(seed_text.encode("ascii")):
            raise ResearchControllerViolation("explicit scientific controller seed commitment mismatch")
        raw_draws = None
    elif request["input_mode"] == "raw_draws":
        if request["seed_uint256"] is not None or not isinstance(request["raw_draws"], list):
            raise ResearchControllerViolation("raw-draw mode must not disclose or synthesize a seed")
        seed = None
        raw_draws = request["raw_draws"]
    else:
        raise ResearchControllerViolation("scientific controller input mode is not registered")
    terminal = _reduce_development_sequence(
        design=design, game=game, world=world, sequence_ordinal=ordinal,
        seed=seed, seed_domain=seed_domain, raw_draws=raw_draws,
    )
    response: dict[str, Any] = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_scientific_controller_response",
        "status": "PASS",
        "terminal": "SCIENTIFIC_SEQUENCE_REDUCED",
        "request_id": request_id,
        "request_sha256": canonical_sha256(dict(request)),
        "controller_identity": identity,
        "design_id": design["design_id"],
        "game": game,
        "world": world,
        "sequence_ordinal": ordinal,
        "seed_domain": seed_domain,
        "seed_commitment_sha256": commitment,
        "sequence_terminal": terminal,
        "guard_code": "ALL_REGISTERED_GUARDS_PASS",
        "guards": [
            "controller_identity_bound", "design_identity_bound", "registered_game_world",
            "registered_seed_domain", "seed_or_raw_draws_exclusive", "exact_150_draws",
            "first_crossing_only", "next_shadow_content_bound", "champion_mutation_zero",
        ],
        "champion_mutation_count": 0,
    }
    response["response_sha256"] = canonical_sha256(response)
    return response


def execute_registered_scientific_controller_fixture(request: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "artifact_type", "fixture_id", "non_scientific",
        "qualification_seed_domain", "expected_controller_identity_id", "design",
        "game", "world", "sequence_ordinal", "raw_draws",
    }
    if (
        set(request) != required
        or request.get("schema_version") != "1.0.0"
        or request.get("artifact_type") != "phase4_registered_scientific_controller_test_request"
        or request.get("non_scientific") is not True
        or request.get("qualification_seed_domain") is not None
    ):
        raise ResearchControllerViolation("scientific controller test fixture is not registered and seed-isolated")
    fixture_id = validate_stable_id(request["fixture_id"], "scientific controller fixture identity")
    identity = scientific_controller_identity()
    if request["expected_controller_identity_id"] != identity["controller_identity_id"]:
        raise ResearchControllerViolation("scientific controller test fixture identity mismatch")
    design = _validated_scientific_design(request["design"])
    game, world, ordinal = request["game"], request["world"], request["sequence_ordinal"]
    if game not in _DEVELOPMENT_GAMES or world not in _DEVELOPMENT_WORLDS:
        raise ResearchControllerViolation("scientific controller test cell is not registered")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
        raise ResearchControllerViolation("scientific controller test ordinal is invalid")
    terminal = _reduce_development_sequence(
        design=design, game=game, world=world, sequence_ordinal=ordinal,
        seed=None, seed_domain=None, raw_draws=request["raw_draws"],
    )
    response: dict[str, Any] = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_non_scientific_controller_fixture_response",
        "status": "PASS",
        "terminal": "NON_SCIENTIFIC_CONTROLLER_FIXTURE_REDUCED",
        "fixture_id": fixture_id,
        "non_scientific": True,
        "qualification_seed_domain": None,
        "request_sha256": canonical_sha256(dict(request)),
        "controller_identity": identity,
        "sequence_terminal": terminal,
        "guard_code": "ALL_REGISTERED_GUARDS_PASS",
        "champion_mutation_count": 0,
    }
    response["response_sha256"] = canonical_sha256(response)
    return response


def _validate_development_preregistration(value: Mapping[str, Any], *, sequences_per_cell: int) -> None:
    expected = {
        "artifact_type": "phase4_qualification_preregistration",
        "formal_run_authorized": False,
        "cycles_per_sequence": 150,
        "worlds": list(_DEVELOPMENT_WORLDS),
        "effect_vector": [1, 1, 1, 0, 0, 0, 0, -1, -1, -1],
        "effect_ticks": list(_DEVELOPMENT_Q),
        "slow_drift_ramp_cycles": 100,
        "feature_context": "strict_alternation_75_75_context_fixed_before_draw",
        "development_sequences_per_cell_design": sequences_per_cell,
        "champion_change_maximum": 0,
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise ResearchControllerViolation("development preregistration differs from the frozen contract")
    if value.get("seed_domains") != list(_QUALIFICATION_DOMAINS):
        raise ResearchControllerViolation("qualification seed domains differ from the frozen contract")
    selection = value.get("selection")
    if selection != {
        "analytic_uniform_aggregate_lower_min": "0.99",
        "analytic_positive_aggregate_lower_min": "0.99",
        "analytic_positive_sequence_lower_min": "0.93",
        "implementation_match_rate": "1",
        "order": "q_ascending_then_canonical_config_bytes",
        "empirical_rate_in_predicate": False,
    }:
        raise ResearchControllerViolation("development selection rule differs from the frozen contract")


def _analytic_candidates(certificate: Mapping[str, Any]) -> list[int]:
    if (
        certificate.get("artifact_type") != "phase4_independent_analytic_feasibility_certificate"
        or certificate.get("status") != "PASS"
        or certificate.get("result_blind") is not True
        or certificate.get("decimal_precision") != 80
        or certificate.get("uniform", {}).get("formal_1000_gate_pass_probability_lower_bound") is None
    ):
        raise ResearchControllerViolation("analytic feasibility certificate identity or status is invalid")
    with localcontext() as context:
        context.prec = 80
        uniform_lower = Decimal(certificate["uniform"]["formal_1000_gate_pass_probability_lower_bound"])
        if not uniform_lower.is_finite() or uniform_lower < Decimal("0.99"):
            return []
        rows = certificate.get("candidates")
        if not isinstance(rows, list) or [row.get("q") for row in rows] != list(_DEVELOPMENT_Q):
            raise ResearchControllerViolation("analytic feasibility candidate menu is incomplete or reordered")
        eligible = []
        for candidate in rows:
            worlds = candidate.get("worlds")
            if not isinstance(worlds, list) or [row.get("world") for row in worlds] != list(_DEVELOPMENT_WORLDS[1:]):
                raise ResearchControllerViolation("analytic positive-world menu is incomplete or reordered")
            if all(
                row.get("aggregate_selection_gate_pass") is True
                and row.get("sequence_selection_gate_pass") is True
                and Decimal(row["formal_1000_gate_pass_probability_lower_bound"]) >= Decimal("0.99")
                and Decimal(row["sequence_recovery_lower_bound"]) >= Decimal("0.93")
                for row in worlds
            ):
                eligible.append(candidate["q"])
        return eligible


def _write_or_validate_bytes(path: Path, payload: bytes) -> bool:
    if path.is_file():
        if path.read_bytes() != payload:
            raise IdentityReuseError(f"immutable development artifact differs: {path}")
        return True
    write_once_bytes(path, payload)
    return False


def _development_shard(
    *, design: Mapping[str, Any], game: str, world: str, start: int, stop: int,
    seed_function: Any, seed_domain: str | None,
) -> dict[str, Any]:
    terminals = [
        _reduce_development_sequence(
            design=design, game=game, world=world, sequence_ordinal=ordinal,
            seed=seed_function(design["design_id"], game, world, ordinal), seed_domain=seed_domain,
        )
        for ordinal in range(start, stop + 1)
    ]
    value: dict[str, Any] = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_development_sequence_shard",
        "design_id": design["design_id"],
        "game": game,
        "world": world,
        "first_sequence_ordinal": start,
        "last_sequence_ordinal": stop,
        "sequence_count": len(terminals),
        "draw_observation_count": len(terminals) * 150,
        "terminals": terminals,
    }
    value["shard_content_sha256"] = canonical_sha256(value)
    return value


def _validate_completed_development_manifest(output_root: Path, manifest: Mapping[str, Any], control_sha256: str) -> None:
    required = {
        "schema_version", "artifact_type", "control_sha256", "files", "design_count", "cell_count",
        "sequence_count", "draw_observation_count", "batch_count", "implementation_match_count",
        "implementation_match_rate", "lossless_shard_count", "event_counts", "descriptive_non_selection",
        "empirical_rate_in_selection_predicate", "seed_domain", "registered_fixture_id", "non_formal",
        "seed_set_sha256", "seed_count", "manifest_id",
    }
    if (
        set(manifest) != required
        or manifest.get("schema_version") != "1.0.0"
        or manifest.get("artifact_type") != "phase4_development_manifest"
        or manifest.get("control_sha256") != control_sha256
        or manifest.get("implementation_match_rate") != "1"
        or manifest.get("descriptive_non_selection") is not True
        or manifest.get("empirical_rate_in_selection_predicate") is not False
        or manifest.get("non_formal") is not True
    ):
        raise ResearchControllerViolation("completed development manifest shape or identity mismatch")
    expected_id = content_id("development-manifest", dict(manifest), excluded_fields=("manifest_id",))
    if manifest.get("manifest_id") != expected_id:
        raise ResearchControllerViolation("completed development manifest content identity mismatch")
    rows = manifest.get("files")
    if not isinstance(rows, list) or len(rows) != manifest.get("lossless_shard_count"):
        raise ResearchControllerViolation("completed development manifest file inventory is incomplete")
    observed_sequences = observed_draws = 0
    seeds: list[list[str]] = []
    for row in rows:
        if set(row) != {
            "path", "sha256", "bytes", "uncompressed_sha256", "uncompressed_bytes",
            "sequence_count", "draw_observation_count",
        }:
            raise ResearchControllerViolation("development manifest row shape is invalid")
        path = resolve_inside(output_root, row["path"])
        if not path.is_file() or sha256_file(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ResearchControllerViolation("development manifest references a missing or changed shard")
        try:
            raw = gzip.decompress(path.read_bytes())
        except (OSError, EOFError) as exc:
            raise ResearchControllerViolation("development shard is not valid lossless gzip") from exc
        if sha256_bytes(raw) != row["uncompressed_sha256"] or len(raw) != row["uncompressed_bytes"]:
            raise ResearchControllerViolation("development shard lossless identity mismatch")
        import json
        try:
            shard = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResearchControllerViolation("development shard payload is not canonical JSON") from exc
        if canonical_json_bytes(shard) != raw or shard.get("shard_content_sha256") != canonical_sha256(
            {key: value for key, value in shard.items() if key != "shard_content_sha256"}
        ):
            raise ResearchControllerViolation("development shard payload is noncanonical or content-tampered")
        if shard.get("sequence_count") != row["sequence_count"] or shard.get("draw_observation_count") != row["draw_observation_count"]:
            raise ResearchControllerViolation("development shard counts differ from the manifest")
        observed_sequences += row["sequence_count"]
        observed_draws += row["draw_observation_count"]
        for terminal in shard.get("terminals", []):
            seeds.append([terminal["sequence_id"], terminal["seed_uint256"]])
    seeds.sort()
    if (
        observed_sequences != manifest.get("sequence_count")
        or observed_draws != manifest.get("draw_observation_count")
        or len(seeds) != manifest.get("seed_count")
        or canonical_sha256(seeds) != manifest.get("seed_set_sha256")
    ):
        raise ResearchControllerViolation("completed development manifest aggregate or seed-set identity mismatch")


def _run_development_batches(
    *, output_root: Path, preregistration_sha256: str, feasibility_sha256: str,
    sequences_per_cell: int, seed_domain: str | None, clock: str,
    provenance: Mapping[str, Any], fixture_id: str | None = None, stop_after_batches: int | None = None,
) -> dict[str, Any]:
    if isinstance(sequences_per_cell, bool) or not isinstance(sequences_per_cell, int) or sequences_per_cell < 1:
        raise ResearchControllerViolation("development sequence count is invalid")
    if fixture_id is None and (seed_domain != "development" or sequences_per_cell != 2000):
        raise ResearchControllerViolation("formal development runner requires the frozen domain and sequence count")
    if fixture_id is not None and (seed_domain is not None or sequences_per_cell > 20):
        raise ResearchControllerViolation("registered non-scientific fixture runner is not qualification-capable")
    designs = [qualification_design(q) for q in _DEVELOPMENT_Q]
    control = {
        "schema_version": "1.0.0", "artifact_type": "phase4_development_control",
        "mode": "development-design-selection", "preregistration_sha256": preregistration_sha256,
        "feasibility_sha256": feasibility_sha256, "sequences_per_cell": sequences_per_cell,
        "cycles_per_sequence": 150, "games": list(_DEVELOPMENT_GAMES), "worlds": list(_DEVELOPMENT_WORLDS),
        "design_ids": [row["design_id"] for row in designs], "q_menu": list(_DEVELOPMENT_Q),
        "checkpoint_every_sequences": 10, "lossless_compression": "gzip-level-9-mtime-0",
        "seed_domain": seed_domain, "registered_fixture_id": fixture_id,
        "non_scientific_fixture": fixture_id is not None, "non_formal": True,
        "formal_run_authorized": False, "clock": clock, "producer_provenance": dict(provenance),
        "controller_source_path": "src/lottery_system/phase4/research/controller.py",
        "controller_source_sha256": sha256_file(Path(__file__)),
    }
    _write_idempotent(output_root / "control.json", control)
    completed_manifest_path = output_root / "manifest.json"
    if completed_manifest_path.is_file():
        completed_manifest = load_json(completed_manifest_path, reject_floats=True)
        _validate_completed_development_manifest(output_root, completed_manifest, sha256_file(output_root / "control.json"))
        return completed_manifest
    seed_function = (
        (lambda design_id, game, world, ordinal: derive_qualification_seed(design_id, "development", game, world, ordinal))
        if fixture_id is None else
        (lambda design_id, game, world, ordinal: _fixture_seed(fixture_id, design_id, game, world, ordinal))
    )
    manifest_rows: list[dict[str, Any]] = []
    batch_count = 0
    implementation_matches = 0
    sequence_count = 0
    seed_rows: list[list[str]] = []
    event_counts: dict[str, int] = {}
    for design in designs:
        for game in _DEVELOPMENT_GAMES:
            for world in _DEVELOPMENT_WORLDS:
                cell_key = f"{design['design_id']}|{game}|{world}"
                event_counts[cell_key] = 0
                for start in range(1, sequences_per_cell + 1, 10):
                    stop = min(start + 9, sequences_per_cell)
                    shard = _development_shard(
                        design=design, game=game, world=world, start=start, stop=stop,
                        seed_function=seed_function, seed_domain=seed_domain,
                    )
                    raw = canonical_json_bytes(shard)
                    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
                    relative = f"shards/{design['design_id']}/{game}/{world}/sequences-{start:04d}-{stop:04d}.json.gz"
                    shard_path = resolve_inside(output_root, relative)
                    _write_or_validate_bytes(shard_path, compressed)
                    if gzip.decompress(shard_path.read_bytes()) != raw:
                        raise ResearchControllerViolation("development shard is not lossless")
                    for terminal in shard["terminals"]:
                        replayed = _reduce_development_sequence(
                            design=design, game=game, world=world,
                            sequence_ordinal=terminal["sequence_ordinal"], seed=int(terminal["seed_uint256"]),
                            seed_domain=terminal["seed_domain"],
                        )
                        if replayed != terminal:
                            raise ResearchControllerViolation("product terminal does not replay from its immutable draw seed")
                        implementation_matches += 1
                        event_counts[cell_key] += int(terminal["sequence_event"])
                        seed_rows.append([terminal["sequence_id"], terminal["seed_uint256"]])
                    sequence_count += shard["sequence_count"]
                    manifest_rows.append({
                        "path": relative, "sha256": sha256_file(shard_path), "bytes": shard_path.stat().st_size,
                        "uncompressed_sha256": sha256_bytes(raw), "uncompressed_bytes": len(raw),
                        "sequence_count": shard["sequence_count"], "draw_observation_count": shard["draw_observation_count"],
                    })
                    checkpoint = {
                        "schema_version": "1.0.0", "artifact_type": "phase4_development_checkpoint",
                        "design_id": design["design_id"], "game": game, "world": world,
                        "completed_through_sequence_ordinal": stop, "next_sequence_ordinal": stop + 1,
                        "checkpoint_every_sequences": 10, "last_shard_path": relative,
                        "last_shard_sha256": sha256_file(shard_path),
                    }
                    _write_idempotent(
                        resolve_inside(output_root, f"checkpoints/{design['design_id']}/{game}/{world}/through-{stop:04d}.json"),
                        checkpoint,
                    )
                    batch_count += 1
                    if stop_after_batches is not None and batch_count >= stop_after_batches:
                        return {"status": "HOLD", "terminal": "DEVELOPMENT_CHECKPOINTED", "completed_batch_count": batch_count}
    seed_rows.sort()
    manifest = {
        "schema_version": "1.0.0", "artifact_type": "phase4_development_manifest",
        "control_sha256": sha256_file(output_root / "control.json"), "files": manifest_rows,
        "design_count": 3, "cell_count": 24, "sequence_count": sequence_count,
        "draw_observation_count": sequence_count * 150, "batch_count": batch_count,
        "implementation_match_count": implementation_matches,
        "implementation_match_rate": "1" if implementation_matches == sequence_count else "0",
        "lossless_shard_count": len(manifest_rows), "event_counts": event_counts,
        "descriptive_non_selection": True, "empirical_rate_in_selection_predicate": False,
        "seed_domain": seed_domain, "registered_fixture_id": fixture_id, "non_formal": True,
        "seed_set_sha256": canonical_sha256(seed_rows), "seed_count": len(seed_rows),
    }
    manifest["manifest_id"] = content_id("development-manifest", manifest)
    _write_idempotent(output_root / "manifest.json", manifest)
    return manifest


def select_development_design(
    *, manifest: Mapping[str, Any], certificate: Mapping[str, Any], designs: list[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if manifest.get("implementation_match_rate") != "1" or manifest.get("design_count") != 3 or manifest.get("cell_count") != 24:
        raise ResearchControllerViolation("complete full-menu implementation consistency is required before selection")
    if manifest.get("descriptive_non_selection") is not True or manifest.get("empirical_rate_in_selection_predicate") is not False:
        raise ResearchControllerViolation("empirical development rates entered the selection predicate")
    if [design.get("q") for design in designs] != list(_DEVELOPMENT_Q):
        raise ResearchControllerViolation("development design menu is incomplete or reordered")
    eligible_q = _analytic_candidates(certificate)
    eligible = [dict(design) for design in designs if design["q"] in eligible_q]
    eligible.sort(key=lambda row: (row["q"], canonical_json_bytes(row)))
    if not eligible:
        raise ResearchControllerViolation("no analytically feasible qualification design")
    selected = eligible[0]
    selection = {
        "schema_version": "1.0.0", "artifact_type": "phase4_development_selection_receipt",
        "status": "PASS", "terminal": "DEVELOPMENT_DESIGN_SELECTED",
        "manifest_id": manifest["manifest_id"], "analytic_certificate_sha256": canonical_sha256(dict(certificate)),
        "evaluated_design_ids": [design["design_id"] for design in designs],
        "eligible_design_ids": [design["design_id"] for design in eligible],
        "selected_design_id": selected["design_id"], "selected_q": selected["q"],
        "analytic_uniform_aggregate_lower_min": "0.99",
        "analytic_positive_aggregate_lower_min": "0.99",
        "analytic_positive_sequence_lower_min": "0.93",
        "implementation_match_rate": "1", "selection_order": "q_ascending_then_canonical_config_bytes",
        "analytic_only_effect_strength_selection": True,
        "empirical_rate_in_predicate": False, "non_formal": True,
        "controller_source_sha256": sha256_file(Path(__file__)),
    }
    selection["selection_receipt_id"] = content_id("development-selection", selection)
    return selected, selection


def execute_development_design_selection(
    *, output_root: Path, preregistration: Mapping[str, Any], preregistration_sha256: str,
    certificate: Mapping[str, Any], feasibility_sha256: str, sequences_per_cell: int,
    seed_domain: str, clock: str, provenance: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_development_preregistration(preregistration, sequences_per_cell=sequences_per_cell)
    _analytic_candidates(certificate)
    manifest = _run_development_batches(
        output_root=output_root, preregistration_sha256=preregistration_sha256,
        feasibility_sha256=feasibility_sha256, sequences_per_cell=sequences_per_cell,
        seed_domain=seed_domain, clock=clock, provenance=provenance,
    )
    designs = [qualification_design(q) for q in _DEVELOPMENT_Q]
    selected, selection = select_development_design(manifest=manifest, certificate=certificate, designs=designs)
    selected_descriptor = {
        **selected, "development_manifest_id": manifest["manifest_id"],
        "selection_receipt_id": selection["selection_receipt_id"], "non_formal": True,
    }
    _write_idempotent(output_root / "selected-design.json", selected_descriptor)
    _write_idempotent(output_root / "selection-receipt.json", selection)
    return {
        "status": "PASS", "terminal": "DEVELOPMENT_DESIGN_SELECTED", "exit_code": 0,
        "selected_design_id": selected["design_id"], "selected_q": selected["q"],
        "manifest_id": manifest["manifest_id"], "sequence_count": manifest["sequence_count"],
        "draw_observation_count": manifest["draw_observation_count"],
    }


def execute_registered_development_fixture(
    output_root: Path, fixture: Mapping[str, Any], *, stop_after_batches: int | None = None,
) -> dict[str, Any]:
    required = {
        "schema_version", "artifact_type", "fixture_id", "non_scientific", "qualification_seed_domain",
        "sequences_per_cell", "preregistration_sha256", "feasibility_sha256", "clock",
    }
    if (
        set(fixture) != required
        or fixture.get("schema_version") != "1.0.0"
        or fixture.get("artifact_type") != "phase4_registered_development_test_fixture"
        or fixture.get("non_scientific") is not True
        or fixture.get("qualification_seed_domain") is not None
    ):
        raise ResearchControllerViolation("development test fixture is not registered and seed-isolated")
    provenance = {
        "producer_actor_id": "registered-fixture", "task_id": "T07", "session_id": "registered-fixture",
        "source_commit": "fixture", "path": "fixture", "role": "non_scientific_fixture",
    }
    return _run_development_batches(
        output_root=output_root, preregistration_sha256=fixture["preregistration_sha256"],
        feasibility_sha256=fixture["feasibility_sha256"], sequences_per_cell=fixture["sequences_per_cell"],
        seed_domain=None, clock=fixture["clock"], provenance=provenance,
        fixture_id=fixture["fixture_id"], stop_after_batches=stop_after_batches,
    )


def _ledger_payloads(runtime_root: Path, ledger_id: str) -> list[dict[str, Any]]:
    ledger = AppendOnlyLedger(runtime_root, ledger_id)
    ledger.validate()
    if not ledger.current_view_path.is_file():
        return []
    view = load_json(ledger.current_view_path, reject_floats=True)
    rows: list[tuple[int, dict[str, Any]]] = []
    for detail in view["objects"].values():
        payload_path = ledger.payloads_root / f"{detail['payload_sha256']}.json"
        rows.append((detail["ordinal"], load_json(payload_path, reject_floats=True)))
    return [row for _, row in sorted(rows)]


def _append_idempotent(
    runtime_root: Path,
    ledger_id: str,
    *,
    object_id: str,
    event_type: str,
    event_at_utc: str,
    payload: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> bool:
    ledger = AppendOnlyLedger(runtime_root, ledger_id)
    state = ledger.validate()
    if ledger.current_view_path.is_file():
        view = load_json(ledger.current_view_path, reject_floats=True)
        existing = view["objects"].get(object_id)
        if existing is not None:
            if existing["payload_sha256"] != canonical_sha256(dict(payload)):
                raise IdentityReuseError(f"{ledger_id} object identity reused with different payload")
            return True
    ledger.append_event(
        object_id=object_id, event_type=event_type, event_at_utc=event_at_utc, payload=payload,
        producer_provenance=provenance, expected_head_sha256=state["head_sha256"],
    )
    return False


def _write_idempotent(path: Path, value: Mapping[str, Any]) -> bool:
    if path.is_file():
        if load_json(path, reject_floats=True) != dict(value):
            raise IdentityReuseError(path)
        return True
    write_once_json(path, dict(value))
    return False


def _replace_current(path: Path, value: Mapping[str, Any]) -> None:
    if path.is_file() and load_json(path, reject_floats=True) == dict(value):
        return
    atomic_replace_json(path, dict(value))


def _validate_decision_contract(value: Mapping[str, Any]) -> None:
    if value.get("artifact_type") != "phase4_decision_contract" or value.get("decision_contract_id") != "phase4-decision-v1":
        raise ResearchControllerViolation("decision contract identity mismatch")
    if value.get("maximum_experiments_per_cycle") != 1 or value.get("direct_champion_change_allowed") is not False:
        raise ResearchControllerViolation("decision contract governance mismatch")


def _validate_alpha_contract(value: Mapping[str, Any]) -> None:
    exact = {
        "initial_wealth_per_game_family": "0.006", "spending_formula": "W0/(t*(t+1))",
        "first_spend": "0.003", "reward": "0", "maximum_total_spend_per_game": "0.018",
        "minimum_look": 30, "maximum_look": 150, "threshold": "E_n>=1/alpha_t",
        "e_process": "product_t(p1_t(Y_t)/p0_t(Y_t))", "precision": 80,
        "revision_refund_allowed": False, "negative_wealth_allowed": False,
    }
    if value.get("artifact_type") != "phase4_alpha_contract" or any(value.get(key) != expected for key, expected in exact.items()):
        raise ResearchControllerViolation("alpha contract differs from frozen T01 semantics")


def _alpha_events(runtime_root: Path, game: str, family: str) -> list[dict[str, Any]]:
    rows = _ledger_payloads(runtime_root, "alpha-events")
    if any(row.get("artifact_type") != "phase4_alpha_event" for row in rows):
        raise AlphaViolation("alpha ledger contains an unregistered event payload")
    return [row for row in rows if row.get("game") == game and row.get("hypothesis_family") == family]


def execute_decision(
    runtime_root: Path,
    fixture: Mapping[str, Any],
    *,
    clock: str,
    provenance: Mapping[str, Any],
    model_registry: Mapping[str, Any],
    feature_registry: Mapping[str, Any],
    decision_contract: Mapping[str, Any],
    alpha_contract: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_decision_contract(decision_contract)
    _validate_alpha_contract(alpha_contract)
    allowed = {
        "schema_version", "artifact_type", "mode", "decision_id", "preregistration_path", "preregistration_sha256",
        "preregistration_id", "game", "target_issue", "result_revision_id", "trigger", "cycle_action",
        "zero_experiment_reason", "parent_model_id", "parent_config_id", "parent_config", "canonical_diff",
        "hypothesis_family", "code_identity", "data_release_id", "feature_snapshot_id", "qualification_id",
        "registered_p0_id", "registered_p1_id", "alpha_ordinal", "looks",
        "model_registry_path", "model_registry_sha256", "feature_registry_path", "feature_registry_sha256",
        "decision_contract_path", "decision_contract_sha256", "alpha_contract_path", "alpha_contract_sha256",
    }
    if set(fixture) != allowed or fixture.get("schema_version") != "1.0.0" or fixture.get("artifact_type") != "phase4_research_decision_fixture":
        raise ResearchControllerViolation("research fixture shape is invalid")
    decision_id = validate_stable_id(fixture["decision_id"], "decision identity")
    game = fixture["game"]
    if game not in {"ssq", "dlt"}:
        raise ResearchControllerViolation("game is invalid")
    action = fixture["cycle_action"]
    lock = AdvisoryFileLock(resolve_inside(runtime_root, f"research/locks/{game}-{fixture['target_issue']}.lock"))
    with lock:
        receipt_path = resolve_inside(runtime_root, f"research/decisions/{decision_id}/receipt.json")
        if receipt_path.is_file():
            prior_receipt = load_json(receipt_path, reject_floats=True)
            if prior_receipt.get("fixture_sha256") != canonical_sha256(dict(fixture)):
                raise IdentityReuseError("decision identity reused with a different frozen fixture")
            return {**prior_receipt, "idempotent_resume": True}
        champion_root = runtime_root / "champions"
        champion_before = _tree_identity(champion_root)
        if action == "zero_experiment":
            decision = zero_experiment_decision(fixture)
            experiment = candidate = alpha_event = sequential = shadow = None
        elif action == "experiment":
            if fixture["zero_experiment_reason"] is not None:
                raise ResearchControllerViolation("experiment cycle cannot carry a zero-experiment reason")
            family = fixture["hypothesis_family"]
            existing_alpha = _alpha_events(runtime_root, game, family)
            current_wealth_path = resolve_inside(runtime_root, f"research/alpha/current/{game}-{family}.json")
            if existing_alpha:
                expected_wealth = reduce_alpha_events(game, family, existing_alpha)
                if not current_wealth_path.is_file() or load_json(current_wealth_path, reject_floats=True) != expected_wealth:
                    raise AlphaViolation("alpha current wealth is missing or not reducible from immutable events")
            elif current_wealth_path.exists():
                raise AlphaViolation("alpha current wealth exists without immutable events")
            expected_ordinal = len(existing_alpha) + 1
            if fixture["alpha_ordinal"] != expected_ordinal:
                raise AlphaViolation("alpha ordinal is stale, split, or reused")
            sequential = reduce_e_process(fixture["looks"], alpha_ordinal=expected_ordinal)
            terminal = sequential["terminal"]
            if terminal == "in_progress":
                terminal = "rejected"
            candidate_status = "shadow_candidate" if terminal == "shadow_candidate" else "rejected"
            candidate = build_candidate(
                game=game, parent_model_id=fixture["parent_model_id"], parent_config_id=fixture["parent_config_id"],
                patches=fixture["canonical_diff"], hypothesis_family=family, code_identity=fixture["code_identity"],
                data_release_id=fixture["data_release_id"], feature_snapshot_id=fixture["feature_snapshot_id"],
                preregistration_id=fixture["preregistration_id"], qualification_id=fixture["qualification_id"],
                status=candidate_status, model_registry=model_registry, feature_registry=feature_registry,
            )
            shadow_config = apply_registered_diff(fixture["parent_config"], candidate["canonical_diff"])
            shadow = {
                "schema_version": "1.0.0", "artifact_type": "phase4_next_shadow_config", "game": game,
                "candidate_id": candidate["candidate_id"], "parent_config_id": fixture["parent_config_id"],
                "config": shadow_config,
            }
            shadow["shadow_config_id"] = content_id("shadow-config", shadow)
            experiment = build_experiment(
                game=game, decision_id=decision_id, hypothesis_family=family, alpha_ordinal=expected_ordinal,
                alpha_spent=sequential["alpha_spent"], parent_config_id=fixture["parent_config_id"],
                canonical_diff=candidate["canonical_diff"], registered_p0_id=fixture["registered_p0_id"],
                registered_p1_id=fixture["registered_p1_id"], terminal=terminal,
            )
            alpha_event = make_spend_event(game=game, hypothesis_family=family, experiment_id=experiment["experiment_id"], ordinal=expected_ordinal, event_at_utc=clock)
            decision = build_decision(
                decision_id=decision_id, game=game, target_issue=fixture["target_issue"],
                result_revision_id=fixture["result_revision_id"], trigger=fixture["trigger"],
                experiment_ids=[experiment["experiment_id"]],
                terminal="shadow_candidate_proposal" if terminal == "shadow_candidate" else "rejected",
                zero_experiment_reason=None,
            )
        else:
            raise ResearchControllerViolation("cycle action is invalid")

        resumed = _append_idempotent(runtime_root, "research-decisions", object_id=decision_id, event_type="research_decision", event_at_utc=clock, payload=decision, provenance=provenance)
        _write_idempotent(resolve_inside(runtime_root, f"research/decisions/{decision_id}/decision.json"), decision)
        if experiment is not None and candidate is not None and alpha_event is not None and sequential is not None and shadow is not None:
            _append_idempotent(runtime_root, "experiments", object_id=experiment["experiment_id"], event_type="experiment_terminal", event_at_utc=clock, payload=experiment, provenance=provenance)
            _append_idempotent(runtime_root, "candidates", object_id=candidate["candidate_id"], event_type="candidate_terminal", event_at_utc=clock, payload=candidate, provenance=provenance)
            _append_idempotent(runtime_root, "alpha-events", object_id=alpha_event["alpha_event_id"], event_type="alpha_spend", event_at_utc=clock, payload=alpha_event, provenance=provenance)
            wealth = reduce_alpha_events(game, fixture["hypothesis_family"], _alpha_events(runtime_root, game, fixture["hypothesis_family"]))
            _write_idempotent(resolve_inside(runtime_root, f"research/experiments/{experiment['experiment_id']}/experiment.json"), experiment)
            _write_idempotent(resolve_inside(runtime_root, f"research/experiments/{experiment['experiment_id']}/looks.json"), {"schema_version":"1.0.0","artifact_type":"phase4_e_process_looks","experiment_id":experiment["experiment_id"],**sequential})
            _write_idempotent(resolve_inside(runtime_root, f"research/candidates/{candidate['candidate_id']}/candidate.json"), candidate)
            _replace_current(current_wealth_path, wealth)
            _replace_current(resolve_inside(runtime_root, f"research/next-shadow/{game}.json"), shadow)
        champion_after = _tree_identity(champion_root)
        if champion_after != champion_before:
            raise ResearchControllerViolation("research control plane mutated Champion state")
        receipt = {
            "schema_version": "1.0.0", "artifact_type": "phase4_research_decision_receipt",
            "decision_id": decision_id, "game": game, "terminal": decision["terminal"],
            "experiment_count": decision["experiment_count"], "experiment_ids": decision["experiment_ids"],
            "candidate_id": None if candidate is None else candidate["candidate_id"],
            "alpha_event_id": None if alpha_event is None else alpha_event["alpha_event_id"],
            "champion_state_sha256": champion_after, "idempotent_resume": resumed,
            "fixture_sha256": canonical_sha256(dict(fixture)),
        }
        _write_idempotent(receipt_path, receipt)
        return receipt


def _tree_identity(root: Path) -> str:
    rows = []
    if root.exists():
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            rows.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return canonical_sha256(rows)


def remediate_correction(runtime_root: Path, impact_path: Path, *, clock: str, provenance: Mapping[str, Any], decision_id: str) -> dict[str, Any]:
    impact = load_json(impact_path, reject_floats=True)
    required = {"schema_version", "artifact_type", "correction_key", "old_result_revision_id", "new_result_revision_id", "corrected_score_ids", "corrected_aggregate_ids", "pending_research_object_ids", "alpha_event_ids_before", "score_side_complete"}
    if set(impact) != required or impact.get("artifact_type") != "phase4_score_correction_impact" or impact.get("score_side_complete") is not True:
        raise ResearchControllerViolation("score correction impact is invalid or incomplete")
    validate_stable_id(decision_id, "remediation decision identity")
    alpha_ledger = AppendOnlyLedger(runtime_root, "alpha-events")
    alpha_state = alpha_ledger.validate()
    alpha_view = load_json(alpha_ledger.current_view_path, reject_floats=True) if alpha_ledger.current_view_path.is_file() else {"objects": {}}
    if sorted(alpha_view["objects"]) != sorted(impact["alpha_event_ids_before"]):
        raise ResearchControllerViolation("correction impact alpha set does not match immutable runtime history")
    before_tree = _tree_identity(alpha_ledger.root)
    before_wealth = _tree_identity(runtime_root / "research/alpha")
    actions = [{"candidate_id": candidate_id, "archive_status":"archived_pending_requalification", "requalification_status":"required"} for candidate_id in impact["pending_research_object_ids"]]
    remediation: dict[str, Any] = {
        "schema_version":"1.0.0", "artifact_type":"phase4_research_remediation",
        "decision_id":decision_id, "trigger":"official_result_revision",
        "correction_impact_path":impact_path.relative_to(runtime_root).as_posix(),
        "correction_impact_sha256":sha256_file(impact_path),
        "old_result_revision_id":impact["old_result_revision_id"], "new_result_revision_id":impact["new_result_revision_id"],
        "candidate_actions":actions, "alpha_refund":False, "alpha_reset":False,
        "alpha_event_ids_before":sorted(alpha_view["objects"]), "alpha_event_ids_after":sorted(alpha_view["objects"]),
        "alpha_ledger_head_before":alpha_state["head_sha256"], "alpha_ledger_head_after":alpha_state["head_sha256"],
        "alpha_history_tree_sha256_before":before_tree, "alpha_history_tree_sha256_after":before_tree,
        "alpha_wealth_tree_sha256_before":before_wealth, "alpha_wealth_tree_sha256_after":before_wealth,
        "terminal":"remediation_completed",
    }
    action_objects = []
    for action in actions:
        action_object = {
            "schema_version":"1.0.0", "artifact_type":"phase4_candidate_requalification",
            **action, "decision_id":decision_id, "old_result_revision_id":impact["old_result_revision_id"],
            "new_result_revision_id":impact["new_result_revision_id"], "correction_impact_sha256":sha256_file(impact_path),
        }
        action_object["candidate_requalification_id"] = content_id("candidate-requalification", action_object)
        action_objects.append(action_object)
    remediation["candidate_action_ids"] = [row["candidate_requalification_id"] for row in action_objects]
    validate_remediation(remediation, impact)
    remediation["remediation_id"] = content_id("research-remediation", remediation)
    for action_object in action_objects:
        _append_idempotent(runtime_root, "candidate-requalifications", object_id=action_object["candidate_requalification_id"], event_type="candidate_archived_pending_requalification", event_at_utc=clock, payload=action_object, provenance=provenance)
        _write_idempotent(resolve_inside(runtime_root, f"research/candidate-requalifications/{action_object['candidate_requalification_id']}.json"), action_object)
    _append_idempotent(runtime_root, "research-remediations", object_id=remediation["remediation_id"], event_type="correction_research_remediation", event_at_utc=clock, payload=remediation, provenance=provenance)
    _write_idempotent(resolve_inside(runtime_root, f"research/remediations/{remediation['remediation_id']}/remediation.json"), remediation)
    if _tree_identity(alpha_ledger.root) != before_tree or _tree_identity(runtime_root / "research/alpha") != before_wealth:
        raise ResearchControllerViolation("correction remediation changed alpha history or wealth")
    return remediation


def validate_remediation(remediation: Mapping[str, Any], impact: Mapping[str, Any]) -> None:
    expected_actions = [
        {"candidate_id": candidate_id, "archive_status":"archived_pending_requalification", "requalification_status":"required"}
        for candidate_id in impact.get("pending_research_object_ids", [])
    ]
    if remediation.get("candidate_actions") != expected_actions:
        raise ResearchControllerViolation("remediation omits candidate archive or requalification")
    action_ids = remediation.get("candidate_action_ids")
    if not isinstance(action_ids, list) or len(action_ids) != len(expected_actions) or len(set(action_ids)) != len(action_ids):
        raise ResearchControllerViolation("remediation candidate action identities are incomplete")
    if remediation.get("alpha_refund") is not False or remediation.get("alpha_reset") is not False:
        raise ResearchControllerViolation("remediation refunds or resets alpha")
    equality_pairs = (
        ("alpha_event_ids_before", "alpha_event_ids_after"),
        ("alpha_ledger_head_before", "alpha_ledger_head_after"),
        ("alpha_history_tree_sha256_before", "alpha_history_tree_sha256_after"),
        ("alpha_wealth_tree_sha256_before", "alpha_wealth_tree_sha256_after"),
    )
    if any(remediation.get(left) != remediation.get(right) for left, right in equality_pairs):
        raise ResearchControllerViolation("remediation changed alpha history or wealth")
    if remediation.get("terminal") != "remediation_completed":
        raise ResearchControllerViolation("remediation terminal is incomplete")
