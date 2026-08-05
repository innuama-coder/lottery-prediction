from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


SECTION_GROUPS = {
    "research_question": ["research question", "question", "objective", "问题", "目标"],
    "source_traceability": ["source refs", "source reference", "traceability", "citation", "来源", "追溯", "引用"],
    "scope_boundary": ["scope", "boundary", "allowed", "forbidden", "non-goal", "范围", "边界", "非目标"],
    "source_strategy": ["source strategy", "source selection", "sampling", "来源策略", "采样", "资料选择"],
    "evidence_quality": ["evidence quality", "credibility", "freshness", "reliability", "证据质量", "可信度", "时效"],
    "method": ["method", "methodology", "analysis method", "方法", "方法论", "分析方法"],
    "synthesis": ["synthesis", "finding", "insight", "pattern", "综合", "发现", "洞察"],
    "limitations": ["limitation", "uncertainty", "assumption", "gap", "局限", "不确定", "假设", "缺口"],
    "conclusion": ["conclusion", "recommendation", "answer", "结论", "建议", "回答"],
    "verification": ["verification", "acceptance", "review", "quality check", "验收", "验证", "复核", "质量检查"],
    "risks_handoff": ["risk", "handoff", "open question", "blocker", "风险", "交付", "移交", "阻塞"],
}

ACCEPTANCE_GROUPS = {
    "verdict": ["verdict", "pass", "fail", "blocked", "结论", "通过", "失败", "阻塞"],
    "inventory": ["document inventory", "inventory", "文档清单", "交付物清单"],
    "coverage": ["coverage", "traceability", "requirement", "覆盖", "追溯", "需求"],
    "cross_document": ["cross-document", "consistency", "dependency", "跨文档", "一致性", "依赖"],
    "quality": ["quality", "Research Report", "evidence", "synthesis", "质量", "研究报告", "证据", "综合"],
    "blockers": ["blocker", "required fix", "risk", "阻塞", "必须修复", "风险"],
    "no_code": ["no code", "documentation-only", "code change", "文档", "不得实现", "代码变更"],
}

FORBIDDEN_TERMS = [
    "todo: fill",
    "placeholder",
    "lorem ipsum",
    "implemented files",
    "changed files",
    "implementation complete",
    "code changes:",
    "已实现文件",
    "已修改代码",
]


def fail(errors: list[str]) -> None:
    for error in errors:
        print(f"FAIL: {error}")
    sys.exit(1)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def find_planning(document: Path, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    cwd_candidate = Path("tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json")
    if cwd_candidate.exists():
        return cwd_candidate
    for parent in [document.resolve().parent, *document.resolve().parents]:
        candidate = parent / "tasks/research/v1.0.0-lottery-autoresearch-roadmap/research-planning.json"
        if candidate.exists():
            return candidate
    return None


def safe_rel(path: Path) -> str:
    return path.as_posix().replace("\\", "/")


def section_coverage(body: str, groups: dict[str, list[str]]) -> tuple[list[str], int]:
    missing = [name for name, aliases in groups.items() if not any(alias in body for alias in aliases)]
    return missing, len(groups) - len(missing)


def keywords(value: str) -> list[str]:
    raw = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", value.lower())
    stop = {"the", "and", "for", "with", "design", "document", "specify", "review", "against"}
    seen: list[str] = []
    for item in raw:
        if item in stop or item in seen:
            continue
        seen.append(item)
    return seen


def task_for_document(plan: dict, document: Path) -> dict | None:
    doc = safe_rel(document)
    if doc.startswith("./"):
        doc = doc[2:]
    for task in plan.get("tasks", []):
        if not isinstance(task, dict):
            continue
        declared = [str(task.get("research_report_path", ""))]
        declared.extend(str(path) for path in task.get("output_paths", []))
        if doc in declared:
            return task
    for task in plan.get("tasks", []):
        if not isinstance(task, dict):
            continue
        declared = [str(task.get("research_report_path", ""))]
        declared.extend(str(path) for path in task.get("output_paths", []))
        if any(Path(path).name == document.name for path in declared):
            return task
    return None


def validate_task_context(text: str, body: str, task: dict) -> list[str]:
    errors: list[str] = []
    task_id = str(task.get("task_id", ""))
    if task_id and task_id.lower() not in body:
        errors.append(f"research report must name its task id: {task_id}")

    source_refs = [str(ref) for ref in task.get("source_refs", []) if str(ref).strip()]
    visible_refs = [ref for ref in source_refs if ref.lower() in body]
    min_refs = min(3, len(source_refs))
    if len(visible_refs) < min_refs:
        errors.append(
            f"research report must cite at least {min_refs} planned source refs; visible refs: {', '.join(visible_refs) or '<none>'}"
        )

    attestation_path = str(task.get("review", {}).get("attestation_path", "")).strip()
    if attestation_path and attestation_path.lower() not in body:
        errors.append(f"research report must cite independent reviewer attestation: {attestation_path}")

    if "active_game" not in body or "excluded_game" not in body:
        errors.append("research report must state active_game(s) and excluded_game(s) scope")

    execution = task.get("execution", {})
    effort_class = str(execution.get("effort_class", "")).strip()
    if effort_class and effort_class.lower() not in body:
        errors.append(f"research report must name its frozen effort class: {effort_class}")
    if "hold" not in body:
        errors.append("research report must state the timeout/resource condition that yields HOLD")

    missing_acceptance_ids = [
        str(item.get("id"))
        for item in task.get("acceptance", [])
        if isinstance(item, dict) and str(item.get("id", "")).lower() not in body
    ]
    if missing_acceptance_ids:
        errors.append("research report must map every task acceptance ID: " + ", ".join(missing_acceptance_ids))

    forbidden_scope = [str(item) for item in task.get("forbidden_scope", []) if str(item).strip()]
    if forbidden_scope and "forbidden" not in body and "禁止" not in body and "不得" not in body:
        errors.append("research report must include explicit forbidden-scope or non-goal boundaries")

    if "docs/research/" not in body:
        errors.append("research report must reference docs/research/ handoff location")
    if "do not implement code" in body and "Research Report" not in body and "详细设计" not in body:
        errors.append("document reads like task instructions rather than Research Report")
    return errors


def validate_acceptance_context(body: str, task: dict | None, plan: dict | None) -> list[str]:
    errors: list[str] = []
    if not plan:
        errors.append("acceptance validation requires research-planning.json context")
        return errors
    detailed = [
        t
        for t in plan.get("tasks", [])
        if isinstance(t, dict)
        and t.get("task_id") != "TASK-999"
        and any(str(path).startswith("docs/research/") and str(path).endswith(".md") for path in t.get("output_paths", []))
    ]
    missing_tasks = [t.get("task_id") for t in detailed if str(t.get("task_id", "")).lower() not in body]
    if missing_tasks:
        errors.append("acceptance report must mention every Research Report task: " + ", ".join(missing_tasks))
    planned_docs = [
        str(path)
        for task_item in detailed
        for path in task_item.get("output_paths", [])
        if str(path).startswith("docs/research/") and str(path).endswith(".md")
    ]
    missing_docs = [
        path
        for path in planned_docs
        if path.lower() not in body and Path(path).name.lower() not in body
    ]
    if missing_docs:
        errors.append("acceptance report must inventory every planned design doc: " + ", ".join(missing_docs[:5]))
    own_docs = [
        str(path)
        for path in (task or {}).get("output_paths", [])
        if str(path).startswith("docs/research/") and str(path).endswith(".md")
    ]
    if task and not any(path.lower() in body or Path(path).name.lower() in body for path in own_docs):
        errors.append("acceptance report must name its own docs/research output path")
    if task:
        attestation_path = str(task.get("review", {}).get("attestation_path", "")).strip()
        if attestation_path and attestation_path.lower() not in body:
            errors.append(f"acceptance report must cite independent reviewer attestation: {attestation_path}")
        decision_paths = [
            str(path)
            for path in task.get("output_paths", [])
            if "decision.json" in str(path)
        ]
        for decision_path in decision_paths:
            if decision_path.lower() not in body:
                errors.append(f"acceptance report must cite machine decision artifact: {decision_path}")
    for marker in ("active_game", "excluded_game", "skipped_terminal", "stop", "go", "hold"):
        if marker not in body:
            errors.append(f"acceptance report must cover terminal/scope marker: {marker}")
    return errors


def validate(path: Path, acceptance: bool, planning_path: str | None) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"missing research report: {path}"]
    if path.suffix.lower() != ".md":
        errors.append(f"research report must be markdown: {path}")

    text = path.read_text(encoding="utf-8")
    body = normalize(text)
    min_chars = 2400 if not acceptance else 1800
    if len(text.strip()) < min_chars:
        errors.append(f"research report is too small for Research Report quality: {path}")

    headings = re.findall(r"(?m)^#{1,3}\s+\S+", text)
    min_headings = 8 if not acceptance else 6
    if len(headings) < min_headings:
        errors.append(f"research report has too few meaningful markdown headings: {path}")

    if acceptance:
        missing_groups, covered = section_coverage(body, ACCEPTANCE_GROUPS)
        if covered < len(ACCEPTANCE_GROUPS):
            errors.append(f"acceptance report missing required review dimensions: {', '.join(missing_groups)}")
    else:
        missing_groups, covered = section_coverage(body, SECTION_GROUPS)
        if covered < 10:
            errors.append(f"research report missing required research-report dimensions: {', '.join(missing_groups)}")

    for forbidden in FORBIDDEN_TERMS:
        if forbidden in body:
            errors.append(f"research report contains forbidden placeholder/report wording: {forbidden}")

    if "docs/" not in body and "source" not in body and "citation" not in body and "来源" not in body:
        errors.append("research report lacks visible source traceability markers")

    plan_path = find_planning(path, planning_path)
    plan = load_json(plan_path) if plan_path and plan_path.exists() else None
    task = task_for_document(plan, path) if plan else None
    if plan and not task:
        errors.append(f"research report is not declared as a deliverable in {plan_path}")
    if task and not acceptance:
        errors.extend(validate_task_context(text, body, task))
    if acceptance:
        errors.extend(validate_acceptance_context(body, task, plan))

    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--planning")
    parser.add_argument("document")
    parser.add_argument("--acceptance", action="store_true")
    args = parser.parse_args()

    errors = validate(Path(args.document), args.acceptance, args.planning)
    if errors:
        fail(errors)
    print(f"PASS: Research Report quality validation {args.document}")


if __name__ == "__main__":
    main()

