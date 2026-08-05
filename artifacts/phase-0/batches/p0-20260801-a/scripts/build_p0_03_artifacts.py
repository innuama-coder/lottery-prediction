"""Build the deterministic Phase-0 rule bundle artifact from audited boundaries."""

from __future__ import annotations

import json
from pathlib import Path


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


def main() -> None:
    mappings = []
    for bundle in BUNDLES:
        start = int(bundle["effective_start_issue"])
        end = int(bundle["effective_end_issue"])
        for issue in range(start, end + 1):
            mappings.append({"game": bundle["game"], "issue_id": str(issue), "bundle_id": bundle["bundle_id"]})
    mappings.sort(key=lambda item: (item["game"], item["issue_id"]))
    artifact = {
        "schema_version": "1.0.0",
        "artifact_type": "rule_bundles",
        "contract_version": "1.3",
        "bundles": BUNDLES,
        "issue_mappings": mappings,
    }
    ARTIFACT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
