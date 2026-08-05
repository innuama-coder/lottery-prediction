"""Build the deterministic Phase-0 rule bundle artifact from audited boundaries."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from urllib.parse import urlparse


ARTIFACT = Path("artifacts/phase-0/rule-bundles.json")


def segment(bundle_id, game, start, end, number, draw, prize, promotions, evidence):
    return {
        "bundle_id": bundle_id,
        "game": game,
        "number_space_version": number,
        "draw_process_version": draw,
        "prize_rule_version": prize,
        "active_promotion_ids": promotions,
        "effective_start_issue": start,
        "effective_end_issue": end,
        "evidence_refs": evidence,
    }


DLT_BASE_OLD = ["https://m.lottery.gov.cn/ksjz/m/yxgz_dlt/"]
DLT_BASE_NEW = [
    "https://www.mof.gov.cn/gp/xxgkml/zhs/202601/t20260116_3982042.htm",
    "https://www.gdlottery.cn/html/ticaidongtai/20260116/93540.html",
    "https://www.gdlottery.cn/f_html/kjgg/P085_26013.html",
    "https://www.gdlottery.cn/f_html/kjgg/P085_26014.html",
]
SSQ_BASE_OLD = ["https://mzt.hunan.gov.cn/mzt/fc/fcyxfc/ssqfc/202011/t20201125_13966203.html"]
SSQ_BASE_NEW = [
    "https://www.mof.gov.cn/gp/xxgkml/zhs/202601/t20260116_3982040.htm",
    "https://mzt.hunan.gov.cn/mzt/fc/xwzxfc/tzggfc/202601/t20260116_33894962.html",
    "https://mzt.hunan.gov.cn/mzt/fc/xwzxfc/tzggfc/202601/33894962/files/9b6723ae4d8743099b90d5edfe7d39ea.pdf",
]


BUNDLES = [
    segment("dlt-2024-base-001-029", "dlt", "2024001", "2024029", "DLT_NS_5OF35_2OF12_V1", "DLT_DRAW_MECHANICAL_FRONT_BACK_V1", "DLT_PRIZE_2019_9TIER", [], DLT_BASE_OLD),
    segment("dlt-2024-promo-both-030-049", "dlt", "2024030", "2024049", "DLT_NS_5OF35_2OF12_V1", "DLT_DRAW_MECHANICAL_FRONT_BACK_V1", "DLT_PRIZE_2019_9TIER", ["DLT_2024_FIXED_TIER", "DLT_2024_HIGH_TIER"], ["https://www.gdlottery.cn/html/gonggao/20240501/90180.html", "https://api.js-lottery.com/tzgg/tzgg/cms/post-141661.html"]),
    segment("dlt-2024-promo-fixed-050-062", "dlt", "2024050", "2024062", "DLT_NS_5OF35_2OF12_V1", "DLT_DRAW_MECHANICAL_FRONT_BACK_V1", "DLT_PRIZE_2019_9TIER", ["DLT_2024_FIXED_TIER"], ["https://www.gdlottery.cn/f_html/kjgg/P085_24050.html", "https://api.js-lottery.com/tzgg/tzgg/cms/post-141661.html"]),
    segment("dlt-2024-base-063-150", "dlt", "2024063", "2024150", "DLT_NS_5OF35_2OF12_V1", "DLT_DRAW_MECHANICAL_FRONT_BACK_V1", "DLT_PRIZE_2019_9TIER", [], DLT_BASE_OLD),
    segment("dlt-2025-base-001-037", "dlt", "2025001", "2025037", "DLT_NS_5OF35_2OF12_V1", "DLT_DRAW_MECHANICAL_FRONT_BACK_V1", "DLT_PRIZE_2019_9TIER", [], DLT_BASE_OLD),
    segment("dlt-2025-promo-038-057", "dlt", "2025038", "2025057", "DLT_NS_5OF35_2OF12_V1", "DLT_DRAW_MECHANICAL_FRONT_BACK_V1", "DLT_PRIZE_2019_9TIER", ["DLT_2025_ALL_TIER"], ["https://m.lottery.gov.cn/xxgk/tzgg/dlttz/20250401/10047591.html", "https://www.gdlottery.cn/f_html/kjgg/P085_25057.html"]),
    segment("dlt-2025-base-058-150", "dlt", "2025058", "2025150", "DLT_NS_5OF35_2OF12_V1", "DLT_DRAW_MECHANICAL_FRONT_BACK_V1", "DLT_PRIZE_2019_9TIER", [], [*DLT_BASE_OLD, "https://www.gdlottery.cn/f_html/kjgg/P085_25058.html"]),
    segment("dlt-2026-old-001-013", "dlt", "2026001", "2026013", "DLT_NS_5OF35_2OF12_V1", "DLT_DRAW_MECHANICAL_FRONT_BACK_V1", "DLT_PRIZE_2019_9TIER", [], [*DLT_BASE_OLD, "https://www.gdlottery.cn/f_html/kjgg/P085_26013.html"]),
    segment("dlt-2026-new-014-049", "dlt", "2026014", "2026049", "DLT_NS_5OF35_2OF12_V1", "DLT_DRAW_MECHANICAL_FRONT_BACK_V1", "DLT_PRIZE_2026_7TIER", [], DLT_BASE_NEW),
    segment("dlt-2026-new-promo-050", "dlt", "2026050", "2026050", "DLT_NS_5OF35_2OF12_V1", "DLT_DRAW_MECHANICAL_FRONT_BACK_V1", "DLT_PRIZE_2026_7TIER", ["DLT_2026_FIXED_TIER"], [*DLT_BASE_NEW, "https://m.lottery.gov.cn/xxgk/tzgg/dlttz/20260428/10053457.html"]),
    segment("ssq-2024-base-001-125", "ssq", "2024001", "2024125", "SSQ_NS_6OF33_1OF16_V1", "SSQ_DRAW_MECHANICAL_RED_THEN_BLUE_V1", "SSQ_PRIZE_2018_6TIER", [], SSQ_BASE_OLD),
    segment("ssq-2024-promo-both-126-144", "ssq", "2024126", "2024144", "SSQ_NS_6OF33_1OF16_V1", "SSQ_DRAW_MECHANICAL_RED_THEN_BLUE_V1", "SSQ_PRIZE_2018_6TIER", ["SSQ_2024_FIRST_PRIZE_SPECIAL", "SSQ_2024_SIXTH_PRIZE_DOUBLE"], ["https://www.gdfc.org.cn/datas/content/content_270602.html?subjectID=91", "https://www.gdfc.org.cn/datas/content/content_271962.html?subjectID=91"]),
    segment("ssq-2024-promo-first-145", "ssq", "2024145", "2024145", "SSQ_NS_6OF33_1OF16_V1", "SSQ_DRAW_MECHANICAL_RED_THEN_BLUE_V1", "SSQ_PRIZE_2018_6TIER", ["SSQ_2024_FIRST_PRIZE_SPECIAL"], ["https://www.gdfc.org.cn/datas/content/content_271962.html?subjectID=91"]),
    segment("ssq-2024-base-146-150", "ssq", "2024146", "2024150", "SSQ_NS_6OF33_1OF16_V1", "SSQ_DRAW_MECHANICAL_RED_THEN_BLUE_V1", "SSQ_PRIZE_2018_6TIER", [], SSQ_BASE_OLD),
    segment("ssq-2025-base-001-150", "ssq", "2025001", "2025150", "SSQ_NS_6OF33_1OF16_V1", "SSQ_DRAW_MECHANICAL_RED_THEN_BLUE_V1", "SSQ_PRIZE_2018_6TIER", [], [*SSQ_BASE_OLD, "https://www.cwl.gov.cn/c/2025/01/02/600959.shtml"]),
    segment("ssq-2026-old-001-013", "ssq", "2026001", "2026013", "SSQ_NS_6OF33_1OF16_V1", "SSQ_DRAW_MECHANICAL_RED_THEN_BLUE_V1", "SSQ_PRIZE_2018_6TIER", [], SSQ_BASE_OLD),
    segment("ssq-2026-new-fuyun-014-050", "ssq", "2026014", "2026050", "SSQ_NS_6OF33_1OF16_V1", "SSQ_DRAW_MECHANICAL_RED_THEN_BLUE_V1", "SSQ_PRIZE_2026_BASE", ["SSQ_2026_FUYUN_SPECIAL"], [*SSQ_BASE_NEW, "https://mzt.hunan.gov.cn/mzt/fc/xwzxfc/tzggfc/202602/t20260203_33909312.html", "https://www.gdfc.org.cn/datas/content/content_283202.html?subjectID=14"]),
]


def evidence_id(game: str, url: str) -> str:
    """Return an append-stable identifier; adding evidence never renumbers old entries."""
    digest = hashlib.sha256(f"{game}\0{url}".encode("utf-8")).hexdigest()[:12].upper()
    return f"EV-RULE-{game.upper()}-{digest}"


def ref(game: str, url: str) -> str:
    return evidence_id(game, url)


def build_evidence() -> list[dict[str, object]]:
    pairs = sorted({(bundle["game"], url) for bundle in BUNDLES for url in bundle["evidence_refs"]})
    return [
        {
            "evidence_id": evidence_id(game, url),
            "game": game,
            "url": url,
            "publisher": urlparse(url).hostname or "official publisher",
            "claim": "Official rule, promotion, or issue-boundary evidence used by at least one registered bundle.",
            "status": "verified",
            "observed_on": "2026-08-01",
        }
        for game, url in pairs
    ]


def build_version_registry() -> dict[str, list[dict[str, object]]]:
    return {
        "number_space_versions": [
            {"version_id": "DLT_NS_5OF35_2OF12_V1", "game": "dlt", "front_count": 5, "front_min": 1, "front_max": 35, "back_count": 2, "back_min": 1, "back_max": 12, "evidence_refs": [ref("dlt", DLT_BASE_OLD[0])]},
            {"version_id": "SSQ_NS_6OF33_1OF16_V1", "game": "ssq", "front_count": 6, "front_min": 1, "front_max": 33, "back_count": 1, "back_min": 1, "back_max": 16, "evidence_refs": [ref("ssq", SSQ_BASE_OLD[0])]},
        ],
        "draw_process_versions": [
            {"version_id": "DLT_DRAW_MECHANICAL_FRONT_BACK_V1", "game": "dlt", "description": "Mechanically drawn front zone followed by back zone; device identity is outside this version axis.", "evidence_refs": [ref("dlt", DLT_BASE_OLD[0])]},
            {"version_id": "SSQ_DRAW_MECHANICAL_RED_THEN_BLUE_V1", "game": "ssq", "description": "Mechanically drawn red balls followed by the blue ball; device identity is outside this version axis.", "evidence_refs": [ref("ssq", SSQ_BASE_OLD[0])]},
        ],
        "prize_rule_versions": [
            {"version_id": "DLT_PRIZE_2019_9TIER", "game": "dlt", "description": "DLT base nine-tier prize rules before issue 2026014.", "evidence_refs": [ref("dlt", DLT_BASE_OLD[0])]},
            {"version_id": "DLT_PRIZE_2026_7TIER", "game": "dlt", "description": "DLT base seven-tier prize rules effective from issue 2026014.", "evidence_refs": [ref("dlt", DLT_BASE_NEW[0]), ref("dlt", DLT_BASE_NEW[1])]},
            {"version_id": "SSQ_PRIZE_2018_6TIER", "game": "ssq", "description": "SSQ base six-tier prize rules before issue 2026014.", "evidence_refs": [ref("ssq", SSQ_BASE_OLD[0])]},
            {"version_id": "SSQ_PRIZE_2026_BASE", "game": "ssq", "description": "SSQ base prize rules effective from issue 2026014, separate from the Fuyun overlay.", "evidence_refs": [ref("ssq", SSQ_BASE_NEW[0]), ref("ssq", SSQ_BASE_NEW[2])]},
        ],
    }


def fixed_promotion(promotion_id: str, game: str, start: str, end: str | None, urls: list[str]) -> dict[str, object]:
    return {
        "promotion_id": promotion_id, "game": game, "kind": "fixed_issue_range",
        "effective_start_issue": start, "effective_end_issue": end,
        "evidence_refs": [ref(game, url) for url in urls], "state_machine": None,
    }


def build_promotion_registry() -> list[dict[str, object]]:
    return [
        fixed_promotion("DLT_2024_FIXED_TIER", "dlt", "2024030", "2024062", ["https://www.gdlottery.cn/html/gonggao/20240501/90180.html", "https://www.gdlottery.cn/f_html/kjgg/P085_24050.html"]),
        fixed_promotion("DLT_2024_HIGH_TIER", "dlt", "2024030", "2024049", ["https://www.gdlottery.cn/html/gonggao/20240501/90180.html", "https://api.js-lottery.com/tzgg/tzgg/cms/post-141661.html"]),
        fixed_promotion("DLT_2025_ALL_TIER", "dlt", "2025038", "2025057", ["https://m.lottery.gov.cn/xxgk/tzgg/dlttz/20250401/10047591.html", "https://www.gdlottery.cn/f_html/kjgg/P085_25057.html"]),
        fixed_promotion("DLT_2026_FIXED_TIER", "dlt", "2026050", None, ["https://m.lottery.gov.cn/xxgk/tzgg/dlttz/20260428/10053457.html"]),
        fixed_promotion("SSQ_2024_FIRST_PRIZE_SPECIAL", "ssq", "2024126", "2024145", ["https://www.gdfc.org.cn/datas/content/content_271962.html?subjectID=91"]),
        fixed_promotion("SSQ_2024_SIXTH_PRIZE_DOUBLE", "ssq", "2024126", "2024144", ["https://www.gdfc.org.cn/datas/content/content_270602.html?subjectID=91"]),
        {
            "promotion_id": "SSQ_2026_FUYUN_SPECIAL", "game": "ssq", "kind": "state_machine",
            "effective_start_issue": "2026014", "effective_end_issue": None,
            "evidence_refs": [ref("ssq", "https://mzt.hunan.gov.cn/mzt/fc/xwzxfc/tzggfc/202602/t20260203_33909312.html"), ref("ssq", "https://www.gdfc.org.cn/datas/content/content_283202.html?subjectID=14")],
            "state_machine": {
                "initial_state": "inactive", "states": ["inactive", "active"],
                "transitions": [
                    {"from_state": "inactive", "to_state": "active", "trigger_field": "post_draw_pool_yuan", "operator": "greater_than_or_equal", "threshold_yuan": 1500000000},
                    {"from_state": "active", "to_state": "inactive", "trigger_field": "post_draw_pool_yuan", "operator": "less_than", "threshold_yuan": 300000000},
                ],
            },
        },
    ]


def build_activity_ledger() -> list[dict[str, object]]:
    evidence_refs = [
        ref("ssq", "https://mzt.hunan.gov.cn/mzt/fc/xwzxfc/tzggfc/202602/t20260203_33909312.html"),
        ref("ssq", "https://www.gdfc.org.cn/datas/content/content_283202.html?subjectID=14"),
    ]
    ledger = []
    for issue in range(2026014, 2026051):
        ledger.append(
            {
                "issue_id": str(issue),
                "promotion_id": "SSQ_2026_FUYUN_SPECIAL",
                "active_for_issue": True,
                "post_draw_pool_yuan": None,
                "post_draw_pool_lower_bound_yuan": 300000000,
                "post_draw_pool_upper_bound_yuan": None,
                "active_for_next_issue": True,
                "transition_reason": (
                    "seeded_active_by_official_issue_evidence"
                    if issue == 2026014
                    else "remained_active_pool_not_below_exit_threshold"
                ),
                "evidence_refs": evidence_refs,
            }
        )
    return ledger


def main() -> None:
    mappings = []
    for bundle in BUNDLES:
        start = int(bundle["effective_start_issue"])
        end = int(bundle["effective_end_issue"])
        for issue in range(start, end + 1):
            mappings.append({"game": bundle["game"], "issue_id": str(issue), "bundle_id": bundle["bundle_id"]})
    mappings.sort(key=lambda item: (item["game"], item["issue_id"]))
    bundles = [dict(bundle, evidence_refs=[ref(bundle["game"], url) for url in bundle["evidence_refs"]]) for bundle in BUNDLES]
    artifact = {
        "schema_version": "1.1.0",
        "artifact_type": "rule_bundles",
        "contract_version": "1.3",
        "evidence": build_evidence(),
        "version_registry": build_version_registry(),
        "promotion_registry": build_promotion_registry(),
        "activity_ledger": build_activity_ledger(),
        "bundles": bundles,
        "issue_mappings": mappings,
    }
    ARTIFACT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
