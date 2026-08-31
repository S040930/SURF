"""Question-macro r20 estimands with crossed question/group bootstrap."""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from statistics import mean

ANALYSIS_VERSION = "r20-v4"
BOOTSTRAP_REPS = 10_000
BOOTSTRAP_SEED = "r20-question-group-bootstrap-v1"
CONDITIONS = ("nm", "crm", "arm")


def _average_repeats(
    rows: list[dict], history: int = 40, expected_repeats: int | None = None
) -> dict:
    grouped = defaultdict(list)
    truth = {}
    metadata = {}
    for row in rows:
        if row.get("model_score") is None or row.get("teacher_score") is None:
            continue
        if row["history_count"] != history:
            continue
        key = (
            row["model_id"],
            row["question_id"],
            row["group_id"],
            row["entry_id"],
            row["condition"],
            row["trajectory"],
        )
        grouped[key].append(float(row["model_score"]))
        truth[key] = float(row["teacher_score"])
        metadata[key] = float(row["max_score"])
    if expected_repeats is not None:
        invalid = [
            key for key, values in grouped.items() if len(values) != expected_repeats
        ]
        if invalid:
            raise ValueError(
                f"r20 history={history} requires exactly {expected_repeats} repeats per cell"
            )
    return {
        key: {
            "prediction": mean(values),
            "truth": truth[key],
            "max_score": metadata[key],
            "normalized_error": abs(mean(values) - truth[key]) / metadata[key],
            "raw_error": abs(mean(values) - truth[key]),
        }
        for key, values in grouped.items()
    }


def _paired_differences(averaged: dict) -> list[dict]:
    cells = defaultdict(dict)
    for key, value in averaged.items():
        model, question, group, entry, condition, trajectory = key
        cells[(model, question, group, entry, trajectory)][condition] = value
    output = []
    for (model, question, group, entry, trajectory), values in cells.items():
        if set(values) != set(CONDITIONS):
            raise ValueError(
                f"unpaired r20 endpoint for {model}/{question}/{entry}/{trajectory}"
            )
        output.append(
            {
                "model_id": model,
                "question_id": question,
                "group_id": group,
                "entry_id": entry,
                "trajectory": trajectory,
                "arm_minus_crm": values["arm"]["normalized_error"]
                - values["crm"]["normalized_error"],
                "memory_minus_nm": (
                    values["arm"]["normalized_error"]
                    + values["crm"]["normalized_error"]
                )
                / 2
                - values["nm"]["normalized_error"],
                "raw_arm_minus_crm": values["arm"]["raw_error"]
                - values["crm"]["raw_error"],
            }
        )
    return output


def _question_macro(rows: list[dict], metric: str, model: str | None = None) -> float:
    selected = [row for row in rows if model is None or row["model_id"] == model]
    per_question = defaultdict(list)
    for row in selected:
        per_question[row["question_id"]].append(row[metric])
    if not per_question:
        raise ValueError("r20 analysis has no complete paired endpoints")
    return mean(mean(values) for values in per_question.values())


def _crossed_bootstrap(
    rows: list[dict], metric: str, reps: int, seed: str
) -> dict[str, float | int]:
    questions = sorted({row["question_id"] for row in rows})
    groups = sorted({row["group_id"] for row in rows})
    if not questions or not groups:
        raise ValueError("crossed bootstrap requires questions and groups")
    rng = random.Random(seed)
    samples = []
    for _ in range(reps):
        q_weight = defaultdict(int)
        g_weight = defaultdict(int)
        for _index in questions:
            q_weight[rng.choice(questions)] += 1
        for _index in groups:
            g_weight[rng.choice(groups)] += 1
        per_question = defaultdict(list)
        for row in rows:
            weight = q_weight[row["question_id"]] * g_weight[row["group_id"]]
            if weight:
                per_question[row["question_id"]].extend([row[metric]] * weight)
        question_values = [
            mean(values)
            for question, values in per_question.items()
            for _ in range(q_weight[question])
        ]
        if question_values:
            samples.append(mean(question_values))
    ordered = sorted(samples)
    if not ordered:
        raise ValueError("crossed bootstrap produced no samples")
    return {
        "lower": ordered[int(0.025 * len(ordered))],
        "upper": ordered[min(len(ordered) - 1, int(0.975 * len(ordered)))],
        "reps": len(ordered),
    }


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(quantile * len(ordered)))]


def _resource_detail(resources: dict | None) -> dict:
    resources = dict(resources or {})
    latencies = list(resources.pop("latencies_ms", []))
    resources["latency_ms_summary"] = {
        "mean": mean(latencies) if latencies else None,
        "p50": _percentile(latencies, 0.50),
        "p95": _percentile(latencies, 0.95),
    }
    return resources


def _loo(rows: list[dict], metric: str) -> dict[str, float]:
    questions = sorted({row["question_id"] for row in rows})
    return {
        question: _question_macro(
            [row for row in rows if row["question_id"] != question], metric
        )
        for question in questions
    }


def _trajectory_variation(rows: list[dict], metric: str) -> dict[str, float]:
    return {
        str(trajectory): _question_macro(
            [row for row in rows if row["trajectory"] == trajectory], metric
        )
        for trajectory in sorted({row["trajectory"] for row in rows})
    }


def _history_effects(rows: list[dict]) -> dict[str, dict]:
    result = {}
    for history in (10, 20):
        averaged = _average_repeats(rows, history, expected_repeats=1)
        if not averaged:
            continue
        paired = _paired_differences(averaged)
        result[str(history)] = {
            "arm_minus_crm": _question_macro(paired, "arm_minus_crm"),
            "memory_minus_nm": _question_macro(paired, "memory_minus_nm"),
        }
    return result


def compute_analysis(
    rows: list[dict],
    resources: dict | None = None,
    failures: list[dict] | None = None,
    *,
    final_history: int = 40,
    bootstrap_reps: int = BOOTSTRAP_REPS,
    seed: str = BOOTSTRAP_SEED,
) -> dict:
    paired = _paired_differences(
        _average_repeats(rows, final_history, expected_repeats=2)
    )
    models = sorted({row["model_id"] for row in paired})
    per_question = {
        question: mean(
            row["arm_minus_crm"] for row in paired if row["question_id"] == question
        )
        for question in sorted({row["question_id"] for row in paired})
    }
    core = {
        "estimand": "normalized_absolute_error",
        "primary_arm_minus_crm": _question_macro(paired, "arm_minus_crm"),
        "secondary_memory_minus_nm": _question_macro(paired, "memory_minus_nm"),
        "raw_arm_minus_crm": _question_macro(paired, "raw_arm_minus_crm"),
        "bootstrap_95": _crossed_bootstrap(
            paired, "arm_minus_crm", bootstrap_reps, seed
        ),
        "by_model": {
            model: {
                "arm_minus_crm": _question_macro(paired, "arm_minus_crm", model),
                "memory_minus_nm": _question_macro(paired, "memory_minus_nm", model),
            }
            for model in models
        },
        "per_question_arm_minus_crm": per_question,
        "leave_one_question_out": _loo(paired, "arm_minus_crm"),
        "trajectory_arm_minus_crm": _trajectory_variation(paired, "arm_minus_crm"),
        "exploratory_checkpoints": _history_effects(rows),
    }
    payload = {
        "core": core,
        "supplement": {
            "resources": _resource_detail(resources),
            "failures": failures or [],
        },
    }
    payload["analysis_sha256"] = hashlib.sha256(repr(payload).encode()).hexdigest()
    return payload
