"""Export interim per-question results from a completed SAF memory-study shard.

Read-only: the script never writes to the database. For each question shard in
``completed`` state it collects that question's succeeded score calls, computes
the descriptive slice, and writes an interim report (HTML by default) that a
coding assistant such as Codex can read directly. Formal conclusions remain
those of the full six-question report released after the integrity audit.

Run from ``backend/``::

    python scripts/export_memory_study_question.py --study-id <uuid>
    python scripts/export_memory_study_question.py --study-id <uuid> --question-id q1
    python scripts/export_memory_study_question.py --study-id <uuid> --format json
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    # ``python scripts/<name>.py`` puts ``scripts/`` (not ``backend/``) on
    # sys.path.  Keep the documented direct invocation working while leaving
    # normal package execution untouched.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.experiment.memory_study.analysis import compute_question_report
from app.experiment.memory_study.report_html import (
    render_question_report_html,
    render_question_report_json,
)
from app.models.memory_study import MSCall, MSQuestionRun, MSRecord, MSStudy

DEFAULT_OUTPUT_ROOT = Path("outputs/memory-study-exports")


class ExportError(Exception):
    """Raised with a user-facing message when the export cannot proceed."""


def _generated_at() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def build_question_payload(db, study_id: str, question_id: str) -> dict:
    """Collect one completed question shard into report rows and detail rows."""
    study = db.get(MSStudy, study_id)
    if study is None:
        raise ExportError(f"study {study_id} 不存在")
    shard = db.scalar(
        select(MSQuestionRun).where(
            MSQuestionRun.study_id == study_id,
            MSQuestionRun.question_id == question_id,
        )
    )
    if shard is None:
        known = sorted(
            db.scalars(
                select(MSQuestionRun.question_id).where(
                    MSQuestionRun.study_id == study_id
                )
            )
        )
        raise ExportError(
            f"题目 {question_id} 不属于该研究的分片；可用分片：{known or '无'}"
        )
    if shard.status != "completed":
        raise ExportError(
            f"题目 {question_id} 分片状态为 {shard.status}，仅 completed 分片可导出"
        )
    record_split = {
        row.answer_id: ("train" if row.selection_kind == "training" else "test")
        for row in db.scalars(
            select(MSRecord).where(
                MSRecord.study_id == study_id, MSRecord.question_id == question_id
            )
        )
    }
    score_calls = list(
        db.scalars(
            select(MSCall)
            .where(
                MSCall.study_id == study_id,
                MSCall.question_id == question_id,
                MSCall.kind == "score",
                MSCall.status == "succeeded",
            )
            .order_by(MSCall.id)
        )
    )
    if not score_calls:
        raise ExportError(f"题目 {question_id} 没有成功的评分调用，无法生成报告")
    score_floors = (study.data_manifest_json or {}).get("score_floors") or {}
    frozen_orders = (study.data_manifest_json or {}).get("orders") or {}
    train_steps = {
        (str(order_variant), str(answer_id)): index
        for order_variant, answer_ids in (frozen_orders.get(question_id) or {}).items()
        if str(order_variant) not in {"training", "test"}
        for index, answer_id in enumerate(answer_ids, start=1)
    }
    rows = [
        {
            "call_id": call.id,
            "model": call.model,
            "question_id": call.question_id,
            "answer_id": call.answer_id,
            "condition": call.condition,
            "feedback_mode": call.feedback_mode,
            "order_variant": call.order_variant,
            "repeat": call.repeat,
            "model_score": call.model_score,
            "teacher_score": call.manual_score,
            "max_score": call.max_score,
            "score_floor": score_floors.get(call.question_id, 0.0),
            "split": record_split.get(call.answer_id, "test"),
            "train_step": train_steps.get((call.order_variant, call.answer_id)),
        }
        for call in score_calls
    ]
    failures = [
        {
            "call_id": call.id,
            "model": call.model,
            "question_id": call.question_id,
            "condition": call.condition,
            "answer_id": call.answer_id,
            "split": record_split.get(call.answer_id, "test"),
            "train_step": train_steps.get((call.order_variant, call.answer_id)),
            "failure_code": call.failure_code,
            "failure_summary": call.failure_summary,
        }
        for call in db.scalars(
            select(MSCall).where(
                MSCall.study_id == study_id,
                MSCall.question_id == question_id,
                MSCall.status == "failed",
            )
        )
    ]
    latencies = [call.latency_ms for call in score_calls if call.latency_ms is not None]
    resources = {
        "latency_ms": {
            "count": len(latencies),
            "mean": sum(latencies) / len(latencies) if latencies else None,
            "min": min(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
        "input_tokens": sum(call.input_tokens or 0 for call in score_calls),
        "output_tokens": sum(call.output_tokens or 0 for call in score_calls),
        "total_tokens": sum(call.total_tokens or 0 for call in score_calls),
        "active_slots": None,
    }
    report = compute_question_report(
        rows,
        failures=failures,
        resources=resources,
        protocol_id=study.protocol_id,
    )
    detail_rows = [
        {
            "call_id": call.id,
            "model": call.model,
            "condition": call.condition,
            "feedback_mode": call.feedback_mode,
            "order_variant": call.order_variant,
            "repeat": call.repeat,
            "answer_id": call.answer_id,
            "split": record_split.get(call.answer_id, "test"),
            "train_step": train_steps.get((call.order_variant, call.answer_id)),
            "memory_items_before": (
                0
                if call.condition == "no_memory"
                else (
                    train_steps[(call.order_variant, call.answer_id)] - 1
                    if (call.order_variant, call.answer_id) in train_steps
                    else None
                )
            ),
            "teacher_score": call.manual_score,
            "model_score": call.model_score,
            "signed_diff": call.signed_diff,
            "absolute_diff": call.absolute_diff,
            "normalized_absolute_diff": call.normalized_absolute_diff,
            "input_tokens": call.input_tokens,
            "output_tokens": call.output_tokens,
            "total_tokens": call.total_tokens,
            "latency_ms": call.latency_ms,
            "failure_code": call.failure_code,
        }
        for call in score_calls
    ]
    return {
        "study": study,
        "report": report,
        "detail_rows": detail_rows,
        "score_call_count": len(score_calls),
        "failed_call_count": len(failures),
    }


def render_csv(detail_rows: list[dict]) -> str:
    fields = [
        "call_id",
        "model",
        "condition",
        "feedback_mode",
        "order_variant",
        "repeat",
        "answer_id",
        "split",
        "train_step",
        "memory_items_before",
        "teacher_score",
        "model_score",
        "signed_diff",
        "absolute_diff",
        "normalized_absolute_diff",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "latency_ms",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows({field: row.get(field) for field in fields} for row in detail_rows)
    return output.getvalue()


def export_study(
    study_id: str,
    *,
    question_id: str | None,
    fmt: str,
    output: Path | None,
) -> list[Path]:
    with SessionLocal() as db:
        if question_id is None:
            shards = list(
                db.scalars(
                    select(MSQuestionRun)
                    .where(
                        MSQuestionRun.study_id == study_id,
                        MSQuestionRun.status == "completed",
                    )
                    .order_by(MSQuestionRun.question_id)
                )
            )
            if not shards:
                raise ExportError(
                    f"研究 {study_id} 没有 completed 分片；每题可单独导出，"
                    "分片完成后重试"
                )
            targets = [shard.question_id for shard in shards]
        else:
            targets = [question_id]
        payloads = [build_question_payload(db, study_id, target) for target in targets]
    generated_at = _generated_at()
    written: list[Path] = []
    for payload in payloads:
        name = payload["study"].name
        qid = payload["report"]["sample"]["question_id"]
        if fmt == "csv":
            suffix, content = "csv", render_csv(payload["detail_rows"])
        elif fmt == "json":
            suffix, content = "json", render_question_report_json(
                payload["report"], payload["detail_rows"]
            )
        else:
            suffix, content = "html", render_question_report_html(
                payload["report"],
                payload["detail_rows"],
                study_name=name,
                question_id=qid,
                generated_at=generated_at,
            )
        if question_id is None:
            # Auto-discovery mode: --output (if given) is always a directory.
            base = output or DEFAULT_OUTPUT_ROOT / study_id
            destination = base / f"{qid}-interim-report.{suffix}"
        elif output is not None:
            destination = output
        else:
            destination = (
                DEFAULT_OUTPUT_ROOT / study_id / f"{qid}-interim-report.{suffix}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        written.append(destination)
        print(
            f"题目 {qid}: {payload['score_call_count']} 条成功评分、"
            f"{payload['failed_call_count']} 条失败调用，sha256 "
            f"{payload['report']['report_sha256'][:12]}… → {destination}"
        )
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-id", required=True)
    parser.add_argument(
        "--question-id",
        help="single question shard; omit to export every completed shard",
    )
    parser.add_argument(
        "--format",
        choices=("html", "json", "csv"),
        default="html",
        dest="format",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="output file path (single question) or directory (multi-question)",
    )
    args = parser.parse_args()
    try:
        export_study(
            args.study_id,
            question_id=args.question_id,
            fmt=args.format,
            output=args.output,
        )
    except ExportError as error:
        print(f"导出失败：{error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
