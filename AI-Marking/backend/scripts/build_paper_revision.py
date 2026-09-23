"""Build the read-only DREsS_New 2026-09-14 manuscript revision package.

No model calls, database writes, or source-experiment mutations occur.  The
script consolidates facts, renders the Markdown manuscript, runs the declared
mechanical checks, and writes a manifest with exact input/output hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import markdown

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
ROOT = BACKEND_DIR.parent.parent
OLD = ROOT / "outputs" / "exp_dress_new_paper_closeout_0913"
FORMAL = ROOT / "outputs" / "exp_dress_new_formal_0912"
DIAG = ROOT / "outputs" / "exp_dress_new_example_diagnostic_0913"
DEFAULT_OUTPUT = ROOT / "outputs" / "exp_dress_new_paper_revision_0914"

sys.path.insert(0, str(SCRIPT_DIR))
import check_paper_draft as checker  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def source_fact(identifier: str, value: object, source: Path, note: str = "") -> dict:
    return {
        "id": identifier,
        "value": value,
        "source": str(source.relative_to(ROOT)),
        "source_sha256": sha256(source),
        **({"note": note} if note else {}),
    }


def build_facts(output: Path) -> Path:
    base = read_json(OLD / "facts.json")
    # The prior closeout had source paths but did not pin their bytes.  Add a
    # current hash here rather than treating an un-hashed historical note as
    # sufficient evidence.  A missing source is retained as an explicit
    # validation failure, not silently omitted.
    facts = []
    for fact in base["facts"]:
        enriched = dict(fact)
        source = ROOT / str(enriched.get("source", ""))
        if source.exists():
            enriched["source_sha256"] = sha256(source)
        facts.append(enriched)
    manifest = read_json(FORMAL / "manifest.json")
    report = read_json(FORMAL / "report.json")
    diagnostic_stats = read_json(OLD / "analysis" / "diagnostic_statistics.json")
    audit = read_json(OLD / "audit" / "audit_report.json")
    facts.extend([
        source_fact("revision.model_count", 2, FORMAL / "manifest.json"),
        source_fact("revision.dimension_count", 3, FORMAL / "manifest.json"),
        source_fact("revision.formal.unique_logical_calls", manifest["actual_unique_logical_calls"], FORMAL / "manifest.json"),
        source_fact("revision.formal.luna_output_count", 792, FORMAL / "analysis_v3" / "FINDINGS.md"),
        source_fact("revision.formal.luna_half_integer_levels", [0.5, 1.5, 2.5, 3.5, 4.5], FORMAL / "analysis_v3" / "FINDINGS.md"),
        source_fact("revision.formal.runner_timeout_seconds", manifest["runners"][0]["timeout_seconds"], FORMAL / "manifest.json"),
        source_fact("revision.formal.runner_cli_version", manifest["runners"][0]["cli_version"], FORMAL / "manifest.json"),
        source_fact("revision.formal.retest_quota", report["design"]["retest_quota"], FORMAL / "report.json"),
        source_fact("revision.formal.grid_minimum", 0.5, FORMAL / "manifest.json"),
        source_fact("revision.formal.grid_maximum", 5.0, FORMAL / "manifest.json"),
        source_fact("revision.formal.grid_step", 0.5, FORMAL / "manifest.json"),
        source_fact("revision.rubric_minimum", 1, FORMAL / "analysis_v3" / "closeout" / "scale_diagnosis.json"),
        source_fact("revision.rubric_maximum", 5, FORMAL / "analysis_v3" / "closeout" / "scale_diagnosis.json"),
        source_fact("revision.diagnostic.bootstrap_replicates", diagnostic_stats["primary_endpoint_recomputed"]["gpt-5.6-luna"]["replicates"], OLD / "analysis" / "diagnostic_statistics.json"),
        source_fact("revision.diagnostic.strata_count", 5, DIAG / "frozen_plan.json"),
        source_fact("revision.diagnostic.essays_per_stratum", 4, DIAG / "frozen_plan.json"),
        source_fact("revision.diagnostic.essay_count", 20, DIAG / "frozen_plan.json"),
        source_fact("revision.diagnostic.condition_count", 2, DIAG / "frozen_plan.json"),
        source_fact("revision.diagnostic.repetitions", 2, DIAG / "frozen_plan.json"),
        source_fact("revision.diagnostic.scheduled_calls", 160, DIAG / "frozen_plan.json"),
        source_fact("revision.diagnostic.example_a_value", 0.5, DIAG / "frozen_plan.json"),
        source_fact("revision.diagnostic.example_b_value", 3.0, DIAG / "frozen_plan.json"),
        source_fact("revision.audit.summary", audit["summary"], OLD / "audit" / "audit_report.json"),
    ])
    by_id = {item["id"]: item for item in facts}
    if len(by_id) != len(facts):
        raise ValueError("duplicate fact ids while consolidating revision facts")
    target = output / "evidence" / "facts_revision.json"
    write_json(target, {
        "version": "dress_new_revision_facts_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "no_model_calls": True,
        "base_facts_sha256": sha256(OLD / "facts.json"),
        "count": len(facts),
        "facts": facts,
    })
    return target


def render_markdown(source: Path, destination: Path) -> None:
    text = source.read_text(encoding="utf-8")
    before_refs, separator, refs = text.partition("## 参考文献")
    before_refs = __import__("re").sub(r"\[@([A-Za-z0-9]+)\]", r'<a class="citation" href="#ref-\1">[\1]</a>', before_refs)
    if separator:
        refs = __import__("re").sub(r"\[@([A-Za-z0-9]+)\]", r'<span id="ref-\1">[\1]</span>', refs)
        text = before_refs + separator + refs
    else:
        text = before_refs
    body = markdown.markdown(text, extensions=["tables", "fenced_code"])
    destination.write_text(
        "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>DREsS_New 修订工作稿</title>"
        "<style>body{max-width:980px;margin:32px auto;padding:0 18px;line-height:1.65;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:6px;text-align:left}th{background:#f3f5f7}code{background:#f3f3f3}</style>"
        f"<body>{body}</body></html>\n", encoding="utf-8"
    )


def traceability_fields(fact_id: str, unit: str) -> tuple[str, str, str]:
    """Conservative labels for the human-readable per-occurrence mapping."""
    model = "总体/不适用"
    if "gpt-5.6-luna" in fact_id:
        model = "Luna"
    elif "gpt-5.6-terra" in fact_id:
        model = "Terra"
    if fact_id.startswith("retest."):
        sample = "正式复测：每模型 72 篇"
    elif fact_id.startswith("agreement."):
        sample = "正式主评：每模型 720 篇"
    elif fact_id.startswith("diagnostic.") or "diagnostic" in fact_id:
        sample = "诊断：20 篇、5 层、2 条件、2 次重复"
    elif fact_id.startswith("design.") or "formal" in fact_id:
        sample = "正式运行设计/日志"
    elif fact_id.startswith("revision.audit"):
        sample = "诊断离线审计"
    else:
        sample = "见冻结来源"
    metric = fact_id.replace("agreement.", "").replace("retest.", "")
    if "weighted_qwk" in metric or metric.endswith(".qwk"):
        metric = "QWK（" + metric + "）"
    elif "weighted_mae" in metric or metric.endswith(".mae"):
        metric = "MAE（" + metric + "）"
    elif "integer_share" in metric:
        metric = "整数分使用率/差值"
    elif "bootstrap" in metric:
        metric = "bootstrap 区间/重复数"
    return model, metric, sample


def write_traceability_table(output: Path, facts_path: Path) -> None:
    mapping = read_json(output / "evidence" / "numeric_traceability.json")
    facts = {item["id"]: item for item in read_json(facts_path)["facts"]}
    rows = [
        "# 逐处数值映射表",
        "",
        "每行对应稿件中一个数值出现位置；表号、置信水平标签和有序列表为结构项。来源哈希由机械检查器在每次重建时核验。",
        "",
        "| 文档位置 | 模型 | 指标 | 样本范围 | 来源字段 | 单位 | 舍入规则 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for entry in mapping["entries"]:
        if entry["category"] == "structural":
            rows.append(f"| paper/dress_new_draft_zh.md:{entry['line']} | 不适用 | {entry['unit']} | 不适用 | 结构项 | {entry['unit']} | 不适用 |")
            continue
        fact_id = entry["fact_id"]
        model, metric, sample = traceability_fields(fact_id, entry.get("unit", "未注明"))
        path = ".".join(entry.get("value_path", []))
        if model == "总体/不适用" and "gpt-5.6-luna" in path:
            model = "Luna"
        elif model == "总体/不适用" and "gpt-5.6-terra" in path:
            model = "Terra"
        source_field = fact_id + (f".{path}" if path else "")
        rounding = entry.get("rounding", "未注明")
        rows.append(f"| paper/dress_new_draft_zh.md:{entry['line']} | {model} | {metric} | {sample} | `{source_field}` | {entry.get('unit', '未注明')} | {rounding} |")
    (output / "evidence" / "numeric_traceability.md").write_text("\n".join(rows) + "\n", encoding="utf-8")


def check_html(html: str, literature: dict) -> dict:
    """Small rendered-artifact check; layout still receives a human visual pass."""
    citation_ids = [entry["citation_key"] for entry in literature["entries"]]
    missing_citation_targets = [key for key in citation_ids if f'href="#ref-{key}"' not in html or f'id="ref-{key}"' not in html]
    missing_links = [entry["id"] for entry in literature["entries"] if entry["url"] not in html]
    return {
        "table_count": html.count("<table>"),
        "missing_citation_targets": missing_citation_targets,
        "missing_literature_links": missing_links,
        "mechanical_pass": html.count("<table>") == 2 and not missing_citation_targets and not missing_links,
        "scope_note": "This checks rendered structure only; visual inspection remains recorded in the internal review.",
    }


def mechanical_report(output: Path, facts: Path) -> Path:
    draft = output / "paper" / "dress_new_draft_zh.md"
    html = output / "paper" / "dress_new_draft_zh.html"
    mapping = output / "evidence" / "numeric_traceability.json"
    literature = output / "literature" / "literature_evidence.json"
    report = output / "paper" / "draft_check_report.json"
    draft_text = draft.read_text(encoding="utf-8")
    numbers = checker.check_number_mapping(draft_text, read_json(facts), read_json(mapping), read_json(literature))
    citations = checker.check_citations(draft_text)
    privacy = checker.check_privacy({"draft": draft_text, "html": html.read_text(encoding="utf-8")})
    html_validation = check_html(html.read_text(encoding="utf-8"), read_json(literature))
    passed = numbers["mechanical_pass"] and citations["mechanical_pass"] and privacy["mechanical_pass"] and html_validation["mechanical_pass"]
    payload = {
        "check_version": "paper_draft_check_v3",
        "draft_sha256": sha256(draft), "facts_sha256": sha256(facts),
        "mapping_sha256": sha256(mapping), "literature_evidence_sha256": sha256(literature),
        "numbers": numbers, "citations": citations, "privacy": privacy, "html_validation": html_validation,
        "semantic_review": {"status": "not_recorded", "complete": False, "reason": "requires an author-named review receipt"},
        "mechanical_pass": passed,
        "overall_verdict": "mechanical_pass_only" if passed else "mechanical_fail",
        "scope_note": "Mechanical checks replay declared bindings only. They do not establish scientific validity, novelty, ethical approval, or submission readiness.",
    }
    write_json(report, payload)
    if not passed:
        raise RuntimeError(f"revision mechanical check failed: {numbers['unmapped_count']} unmapped, {numbers['inconsistent_count']} inconsistent")
    return report


def build_manifest(output: Path, source_files: list[Path]) -> None:
    files = [path for path in output.rglob("*") if path.is_file() and path.name != "revision_manifest.json"]
    write_json(output / "revision_manifest.json", {
        "version": "dress_new_revision_manifest_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "no_model_calls": True,
        "source_files_readonly": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for path in source_files],
        "files": [{"path": str(path.relative_to(output)), "sha256": sha256(path), "bytes": path.stat().st_size} for path in sorted(files)],
    })


def write_resource_report(output: Path, started: float) -> None:
    # This package is intentionally small; report observed local build cost,
    # not a speculative model- or network-cost estimate.
    bytes_before_manifest = sum(path.stat().st_size for path in output.rglob("*") if path.is_file() and path.name != "revision_manifest.json")
    write_json(output / "resource_report.json", {
        "operation": "read-only revision-package build",
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "output_bytes_before_manifest": bytes_before_manifest,
        "output_bytes_before_manifest_mib": round(bytes_before_manifest / (1024 * 1024), 4),
        "model_calls": 0,
        "human_ratings_added": 0,
        "database_or_production_api_changes": 0,
        "resource_boundary": "single local Python process; no GPU; observed package output is far below the 500 MB planning ceiling.",
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    started = time.perf_counter()
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    facts = build_facts(output)
    write_traceability_table(output, facts)
    render_markdown(output / "paper" / "dress_new_draft_zh.md", output / "paper" / "dress_new_draft_zh.html")
    report = mechanical_report(output, facts)
    sources = [
        OLD / "facts.json", OLD / "audit" / "audit_report.json", OLD / "analysis" / "diagnostic_statistics.json",
        FORMAL / "manifest.json", FORMAL / "report.json", FORMAL / "analysis_v3" / "FINDINGS.md",
        DIAG / "frozen_plan.json", DIAG / "calls.jsonl", SCRIPT_DIR / "check_paper_draft.py", Path(__file__),
        SCRIPT_DIR / "run_paper_draft_check_tests.py", BACKEND_DIR / "tests" / "test_paper_draft_check.py",
    ]
    write_resource_report(output, started)
    build_manifest(output, sources)
    print(json.dumps({"output": str(output), "check": str(report), "mechanical_pass": True, "no_model_calls": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
