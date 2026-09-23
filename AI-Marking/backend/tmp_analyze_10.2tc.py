"""Read-only mid-run slice analysis for 10.2_TC (formal study). Never writes DB."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.experiment.memory_study.analysis import compute_question_report
from app.models.memory_study import MSCall, MSRecord, MSStudy

STUDY_ID = "92df5bcb-9678-4e02-8882-1718f95cb6cb"
QID = "10.2_TC"


def main() -> None:
    with SessionLocal() as db:
        study = db.get(MSStudy, STUDY_ID)
        record_split = {
            row.answer_id: ("train" if row.selection_kind == "training" else "test")
            for row in db.scalars(
                select(MSRecord).where(
                    MSRecord.study_id == STUDY_ID, MSRecord.question_id == QID
                )
            )
        }
        score_floors = (study.data_manifest_json or {}).get("score_floors") or {}
        frozen_orders = (study.data_manifest_json or {}).get("orders") or {}
        train_steps = {
            (str(order_variant), str(answer_id)): index
            for order_variant, answer_ids in (frozen_orders.get(QID) or {}).items()
            if str(order_variant) not in {"training", "test"}
            for index, answer_id in enumerate(answer_ids, start=1)
        }

        score_calls = list(
            db.scalars(
                select(MSCall)
                .where(
                    MSCall.study_id == STUDY_ID,
                    MSCall.question_id == QID,
                    MSCall.kind == "score",
                    MSCall.status == "succeeded",
                )
                .order_by(MSCall.id)
            )
        )
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
                    MSCall.study_id == STUDY_ID,
                    MSCall.question_id == QID,
                    MSCall.status == "failed",
                )
            )
        ]
        latencies = [c.latency_ms for c in score_calls if c.latency_ms is not None]
        resources = {
            "latency_ms": {
                "count": len(latencies),
                "mean": round(sum(latencies) / len(latencies), 1) if latencies else None,
                "min": min(latencies) if latencies else None,
                "max": max(latencies) if latencies else None,
            },
            "input_tokens": sum(c.input_tokens or 0 for c in score_calls),
            "output_tokens": sum(c.output_tokens or 0 for c in score_calls),
            "total_tokens": sum(c.total_tokens or 0 for c in score_calls),
            "active_slots": None,
        }
        report = compute_question_report(
            rows,
            failures=failures,
            resources=resources,
            protocol_id=study.protocol_id,
        )

        # Compact per-condition coverage table
        cov: dict[tuple, dict] = defaultdict(lambda: {"train": 0, "test": 0})
        for r in rows:
            cov[(r["condition"], r["order_variant"])][r["split"]] += 1
        print("== succeeded score coverage (condition x order) ==")
        for k in sorted(cov):
            print(f"{k[0]:<24} {k[1]:<10} train={cov[k]['train']:>3} test={cov[k]['test']:>3}")

        print("\n== primary (test) ==")
        print(json.dumps(report["primary"], ensure_ascii=False, indent=2))
        print("\n== split_metrics (raw descriptive) ==")
        print(json.dumps(report["split_metrics"], ensure_ascii=False, indent=2))
        print("\n== grid_context ==")
        print(json.dumps(report["grid_context"], ensure_ascii=False, indent=2))
        print("\n== order_sensitivity ==")
        print(json.dumps(report["secondary"]["history_order_sensitivity"], ensure_ascii=False, indent=2))
        print("\n== resources ==")
        print(json.dumps(report["secondary"]["resource"], ensure_ascii=False, indent=2))
        print("\n== failures ==")
        print(json.dumps(report["failures"], ensure_ascii=False, indent=2))
        print("\n== report sha ==")
        print(report["report_sha256"])

if __name__ == "__main__":
    main()