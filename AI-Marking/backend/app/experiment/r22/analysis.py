"""Descriptive r22 MAE/NAE analysis for training and test calls."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from statistics import mean
from typing import Iterable

from app.experiment.r22.report import summarize_recovery


def _error(prediction: float, truth: float) -> float:
    return abs(float(prediction) - float(truth))


def training_step_errors(rows: Iterable[dict]) -> list[dict]:
    """Return one raw absolute error for every pre-update training score."""
    output = []
    for row in rows:
        if row.get("kind", "train_score") != "train_score":
            continue
        prediction, truth = row.get("model_score"), row.get("teacher_score")
        if prediction is None or truth is None:
            continue
        error = _error(prediction, truth)
        max_score = float(row.get("max_score") or 0)
        output.append(
            {
                "question_id": row["question_id"],
                "condition": row["condition"],
                "trajectory": int(row["trajectory"]),
                "step": int(row.get("position", row.get("history_count", 0) + 1)),
                "answer_id": row["answer_id"],
                "model_score": float(prediction),
                "teacher_score": float(truth),
                "absolute_error": error,
                "normalized_error": error / max_score if max_score else None,
            }
        )
    return sorted(
        output,
        key=lambda row: (
            row["question_id"], row["condition"], row["trajectory"], row["step"]
        ),
    )


def _by_step(errors: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in errors:
        grouped[(row["condition"], row["step"])].append(row)
    return [
        {
            "condition": condition,
            "step": step,
            "mae": mean(row["absolute_error"] for row in rows),
            "n": len(rows),
        }
        for (condition, step), rows in sorted(grouped.items())
    ]


def _train_by_condition(errors: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in errors:
        grouped[row["condition"]].append(row)
    return [
        {"condition": condition, "mae": mean(row["absolute_error"] for row in rows), "n": len(rows)}
        for condition, rows in sorted(grouped.items())
    ]


def _test_cells(rows: Iterable[dict]) -> list[dict]:
    grouped: dict[tuple, list[float]] = defaultdict(list)
    truth: dict[tuple, float] = {}
    max_scores: dict[tuple, float] = {}
    for row in rows:
        if row.get("kind", "test_score") != "test_score":
            continue
        if row.get("model_score") is None or row.get("teacher_score") is None:
            continue
        key = (
            row["question_id"],
            row["condition"],
            int(row["trajectory"]),
            row["answer_id"],
            int(row["history_count"]),
        )
        grouped[key].append(float(row["model_score"]))
        truth[key] = float(row["teacher_score"])
        max_scores[key] = float(row["max_score"])
    output = []
    for key, predictions in sorted(grouped.items()):
        question, condition, trajectory, answer_id, history = key
        prediction = mean(predictions)
        error = _error(prediction, truth[key])
        output.append(
            {
                "question_id": question,
                "condition": condition,
                "trajectory": trajectory,
                "answer_id": answer_id,
                "history": history,
                "repeats": len(predictions),
                "model_score": prediction,
                "teacher_score": truth[key],
                "absolute_error": error,
                "normalized_error": error / max_scores[key] if max_scores[key] else None,
            }
        )
    return output


def _test_summary(cells: list[dict], metric: str) -> list[dict]:
    grouped: dict[tuple[int, str], list[float]] = defaultdict(list)
    for row in cells:
        grouped[(row["history"], row["condition"])].append(row[metric])
    return [
        {
            "history": history,
            "condition": condition,
            "mae" if metric == "absolute_error" else "nae": mean(values),
            "n": len(values),
        }
        for (history, condition), values in sorted(grouped.items())
    ]


def compute_analysis(
    training_rows: Iterable[dict],
    test_rows: Iterable[dict],
    *,
    compression_audits: Iterable[object] = (),
    resources: dict | None = None,
) -> dict:
    """Build the locked r22 report payload."""
    train_errors = training_step_errors(training_rows)
    test_cells = _test_cells(test_rows)
    payload = {
        "protocol": "r22",
        "estimand": "raw_absolute_error_primary",
        "training_step_errors": train_errors,
        "training_mae_by_step": _by_step(train_errors),
        "training_mae_by_condition": _train_by_condition(train_errors),
        "test_mae_by_history": _test_summary(test_cells, "absolute_error"),
        "test_nae_by_history": _test_summary(test_cells, "normalized_error"),
        "resources": dict(resources or {}),
        "compression_statistics": summarize_recovery(compression_audits),
    }
    payload["analysis_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()
    return payload


__all__ = ["compute_analysis", "training_step_errors"]
