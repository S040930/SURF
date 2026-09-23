"""Descriptive, question-macro analysis for the SAF memory study."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from statistics import mean
from typing import Iterable

from app.experiment.memory_study import PROTOCOL_ID

PRIMARY_CONDITIONS = (
    "no_memory",
    "retrieval_full",
    "mem0_full",
    "amem_full",
)
MEMORY_CONDITIONS = PRIMARY_CONDITIONS[1:]
TIE_EPSILON = 1e-12


def _order_plan(rows: list[dict]) -> dict[str, tuple[str, ...]]:
    """Return the required history orders for each observed condition.

    The persisted result rows carry the frozen protocol order names.  Keeping
    this inference row-based preserves compatibility with historical V3
    reports while allowing V3-r2 to use its V4 original/shuffled pair.
    """
    observed = _expected_orders(rows)
    conditions = {str(row["condition"]) for row in rows}
    return {condition: observed for condition in conditions}


def _default_order_plan(rows: list[dict]) -> dict[str, tuple[str, ...]]:
    """Infer the shared frozen order plan from persisted result rows."""
    return _order_plan(rows)


def _order_sensitivity_label(order_variants: tuple[str, ...]) -> str:
    if order_variants == ("original", "shuffled"):
        return "v4_original_vs_shuffled_range_for_memory_conditions"
    return f"{len(order_variants)}_order_range_for_memory_conditions"


def _metric(row: dict, key: str) -> float:
    maximum = float(row["max_score"])
    if maximum <= 0:
        raise ValueError("max_score must be positive")
    signed = float(row["model_score"]) - float(row["teacher_score"])
    if key == "signed_error":
        return signed
    if key == "absolute_error":
        return abs(signed)
    if key == "normalized_signed_error":
        return signed / maximum
    if key == "normalized_absolute_error":
        return abs(signed) / maximum
    raise ValueError(f"unknown metric {key}")


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _question_macro(rows: Iterable[dict], metric: str) -> float | None:
    by_question: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_question[str(row["question_id"])].append(_metric(row, metric))
    if not by_question:
        return None
    return mean(mean(values) for values in by_question.values())


def _group_mean(
    rows: Iterable[dict], metric: str, *keys: str
) -> dict[str, float | None]:
    groups: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for row in rows:
        groups[tuple(str(row[key]) for key in keys)].append(row)
    return {
        " / ".join(group): _question_macro(group_rows, metric)
        for group, group_rows in sorted(groups.items())
    }


def _descriptive_metrics(rows: list[dict]) -> dict[str, float | int | None]:
    """Report directional and absolute model/human discrepancy without CI."""
    if not rows:
        return {
            "n": 0,
            "signed_bias": None,
            "mae": None,
            "normalized_mae": None,
            "median_absolute_error": None,
            "within_one_score": None,
        }
    signed = [_metric(row, "signed_error") for row in rows]
    absolute = [_metric(row, "absolute_error") for row in rows]
    normalized = [_metric(row, "normalized_absolute_error") for row in rows]
    return {
        "n": len(rows),
        "signed_bias": mean(signed),
        "mae": mean(absolute),
        "normalized_mae": mean(normalized),
        "median_absolute_error": _median(absolute),
        "within_one_score": sum(value <= 1 for value in absolute) / len(absolute),
    }


def _expected_orders(rows: list[dict]) -> tuple[str, ...]:
    return tuple(sorted({str(row["order_variant"]) for row in rows}))


def _answer_order_means(
    rows: list[dict],
    metric: str,
    expected_orders: tuple[str, ...],
    expected_orders_by_condition: dict[str, tuple[str, ...]] | None = None,
) -> tuple[dict[tuple[str, str, str, str], float], dict[tuple[str, str], int]]:
    """Average repeats within order, then orders within an answer.

    Only answers with every expected historical order are retained.  The
    returned exclusion counts are keyed by (model, condition).
    """
    order_values: dict[tuple[str, str, str, str, str], list[float]] = defaultdict(list)
    candidates: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for row in rows:
        model = str(row["model"])
        question = str(row["question_id"])
        condition = str(row["condition"])
        answer = str(row["answer_id"])
        order = str(row["order_variant"])
        order_values[(model, question, condition, answer, order)].append(
            _metric(row, metric)
        )
        candidates[(model, condition)].add((question, answer))

    by_answer: dict[tuple[str, str, str, str], dict[str, float]] = defaultdict(dict)
    for (model, question, condition, answer, order), values in order_values.items():
        by_answer[(model, question, condition, answer)][order] = mean(values)

    complete: dict[tuple[str, str, str, str], float] = {}
    included: dict[tuple[str, str], int] = defaultdict(int)
    for key, values in by_answer.items():
        required = set(
            (expected_orders_by_condition or {}).get(key[2], expected_orders)
        )
        if set(values) == required:
            complete[key] = mean(values[order] for order in required)
            included[(key[0], key[2])] += 1
    excluded = {
        key: len(answer_keys) - included.get(key, 0)
        for key, answer_keys in candidates.items()
    }
    return complete, excluded


def _condition_nae(
    test_rows: list[dict],
    primary_conditions: tuple[str, ...] = PRIMARY_CONDITIONS,
    expected_orders_by_condition: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, dict[str, dict[str, object]]]:
    orders = _expected_orders(test_rows)
    values, excluded = _answer_order_means(
        test_rows,
        "normalized_absolute_error",
        orders,
        expected_orders_by_condition,
    )
    grouped: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for (model, question, condition, _answer), value in values.items():
        if condition in primary_conditions:
            grouped[(model, condition, question)].append(value)
    result: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
    models_conditions = sorted(
        {
            (str(row["model"]), str(row["condition"]))
            for row in test_rows
            if str(row["condition"]) in primary_conditions
        }
    )
    for model, condition in models_conditions:
        by_question = {
            question: mean(question_values)
            for (m, c, question), question_values in sorted(grouped.items())
            if m == model and c == condition
        }
        n_answers = sum(
            len(question_values)
            for (m, c, _question), question_values in grouped.items()
            if m == model and c == condition
        )
        result[model][condition] = {
            "estimate": mean(by_question.values()) if by_question else None,
            "by_question": by_question,
            "n_answers": n_answers,
            "excluded_answers": excluded.get((model, condition), 0),
            "orders": list(
                (expected_orders_by_condition or {}).get(condition, orders)
            ),
        }
    return {model: dict(conditions) for model, conditions in result.items()}


def _memory_gain(
    test_rows: list[dict],
    memory_conditions: tuple[str, ...] = MEMORY_CONDITIONS,
    expected_orders_by_condition: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, dict[str, dict[str, object]]]:
    orders = _expected_orders(test_rows)
    values, _excluded = _answer_order_means(
        test_rows,
        "normalized_absolute_error",
        orders,
        expected_orders_by_condition,
    )
    models = sorted({str(row["model"]) for row in test_rows})
    result: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
    for model in models:
        baseline = {
            (question, answer): value
            for (m, question, condition, answer), value in values.items()
            if m == model and condition == "no_memory"
        }
        for condition in memory_conditions:
            target = {
                (question, answer): value
                for (m, question, c, answer), value in values.items()
                if m == model and c == condition
            }
            common = sorted(set(baseline) & set(target))
            gains = {key: baseline[key] - target[key] for key in common}
            by_question_values: dict[str, list[float]] = defaultdict(list)
            for (question, _answer), gain in gains.items():
                by_question_values[question].append(gain)
            by_question = {
                question: mean(items)
                for question, items in sorted(by_question_values.items())
            }
            result[model][condition] = {
                "estimate": mean(by_question.values()) if by_question else None,
                "by_question": by_question,
                "n_answers": len(gains),
                "excluded_answers": len(set(baseline) | set(target)) - len(common),
                "improved": sum(value > TIE_EPSILON for value in gains.values()),
                "tied": sum(abs(value) <= TIE_EPSILON for value in gains.values()),
                "worse": sum(value < -TIE_EPSILON for value in gains.values()),
                "direction": "positive_means_memory_improves_scoring",
            }
    return {model: dict(conditions) for model, conditions in result.items()}


def _order_sensitivity(
    test_rows: list[dict],
    expected_orders_by_condition: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, dict[str, dict[str, object]]]:
    orders = _expected_orders(test_rows)
    per_order: dict[tuple[str, str, str, str], list[float]] = defaultdict(list)
    for row in test_rows:
        per_order[(
            str(row["model"]), str(row["question_id"]),
            str(row["condition"]), str(row["order_variant"]),
        )].append(_metric(row, "normalized_absolute_error"))

    result: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
    model_conditions = sorted({(key[0], key[2]) for key in per_order})
    for model, condition in model_conditions:
        condition_orders = tuple(
            (expected_orders_by_condition or {}).get(condition, orders)
        )
        by_question: dict[str, dict[str, object]] = {}
        questions = sorted({key[1] for key in per_order if key[0] == model and key[2] == condition})
        for question in questions:
            answer_sets = []
            values_by_order_answer: dict[str, dict[str, list[float]]] = defaultdict(
                lambda: defaultdict(list)
            )
            for row in test_rows:
                if (
                    str(row["model"]) == model
                    and str(row["question_id"]) == question
                    and str(row["condition"]) == condition
                ):
                    values_by_order_answer[str(row["order_variant"])][str(row["answer_id"])].append(
                        _metric(row, "normalized_absolute_error")
                    )
            required_orders = condition_orders
            if set(values_by_order_answer) != set(required_orders):
                continue
            answer_sets = [
                set(values_by_order_answer[order]) for order in required_orders
            ]
            common_answers = set.intersection(*answer_sets) if answer_sets else set()
            if not common_answers:
                continue
            order_nae = {
                order: mean(
                    mean(values_by_order_answer[order][answer])
                    for answer in sorted(common_answers)
                )
                for order in required_orders
            }
            by_question[question] = {
                "order_nae": order_nae,
                "range": max(order_nae.values()) - min(order_nae.values()),
                "n_answers": len(common_answers),
            }
        ranges = [float(item["range"]) for item in by_question.values()]
        result[model][condition] = {
            "mean_range": mean(ranges) if ranges else None,
            "by_question": by_question,
            "orders": list(condition_orders),
        }
    return {model: dict(conditions) for model, conditions in result.items()}


def _training_trajectory(train_rows: list[dict]) -> dict[str, dict[str, object]]:
    grouped: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for row in train_rows:
        if row.get("train_step") is None:
            raise ValueError("training score row missing train_step")
        grouped[(
            str(row["model"]), str(row["condition"]),
            str(row["question_id"]), str(row["order_variant"]),
        )].append(row)
    result: dict[str, dict[str, object]] = defaultdict(dict)
    for (model, condition, question, order), items in sorted(grouped.items()):
        ordered = sorted(items, key=lambda row: int(row["train_step"]))
        steps = [int(row["train_step"]) for row in ordered]
        if len(steps) != len(set(steps)):
            raise ValueError(
                f"duplicate training step for {model}/{condition}/{question}/{order}"
            )
        running: list[float] = []
        records = []
        for row in ordered:
            nae = _metric(row, "normalized_absolute_error")
            running.append(nae)
            step = int(row["train_step"])
            records.append({
                "train_step": step,
                "answer_id": str(row["answer_id"]),
                "memory_items_before": 0 if condition == "no_memory" else step - 1,
                "teacher_score": float(row["teacher_score"]),
                "model_score": float(row["model_score"]),
                "nae": nae,
                "cumulative_mean_nae": mean(running),
            })
        result[model][f"{condition} / {question} / {order}"] = {
            "condition": condition,
            "question_id": question,
            "order_variant": order,
            "n_steps": len(records),
            "records": records,
        }
    return {model: dict(streams) for model, streams in result.items()}


def _signed_bias_by_condition(
    test_rows: list[dict],
    expected_orders_by_condition: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, dict[str, object]]:
    orders = _expected_orders(test_rows)
    values, excluded = _answer_order_means(
        test_rows,
        "normalized_signed_error",
        orders,
        expected_orders_by_condition,
    )
    grouped: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for (model, question, condition, _answer), value in values.items():
        grouped[(model, condition, question)].append(value)
    result: dict[str, dict[str, object]] = defaultdict(dict)
    for model, condition in sorted({(key[0], key[1]) for key in grouped}):
        by_question = {
            question: mean(items)
            for (m, c, question), items in sorted(grouped.items())
            if m == model and c == condition
        }
        result[model][condition] = {
            "estimate": mean(by_question.values()) if by_question else None,
            "by_question": by_question,
            "excluded_answers": excluded.get((model, condition), 0),
        }
    return {model: dict(conditions) for model, conditions in result.items()}


def _feedback_ablation(
    test_rows: list[dict],
    pairs: dict[str, tuple[str, str]] | None = None,
    expected_orders_by_condition: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, dict[str, object]]:
    """NAE(no_feedback) - NAE(full); positive means textual feedback helped."""
    pairs = pairs or {
        "retrieval": ("retrieval_full", "retrieval_no_feedback"),
        "mem0": ("mem0_full", "mem0_no_feedback"),
        "amem": ("amem_full", "amem_no_feedback"),
    }
    orders = _expected_orders(test_rows)
    values, _excluded = _answer_order_means(
        test_rows,
        "normalized_absolute_error",
        orders,
        expected_orders_by_condition,
    )
    models = sorted({str(row["model"]) for row in test_rows})
    result: dict[str, dict[str, object]] = defaultdict(dict)
    for model in models:
        for framework, (full_condition, no_feedback_condition) in pairs.items():
            full = {
                (question, answer): value
                for (m, question, condition, answer), value in values.items()
                if m == model and condition == full_condition
            }
            no_feedback = {
                (question, answer): value
                for (m, question, condition, answer), value in values.items()
                if m == model and condition == no_feedback_condition
            }
            common = sorted(set(full) & set(no_feedback))
            deltas: dict[str, list[float]] = defaultdict(list)
            for question, answer in common:
                deltas[question].append(
                    no_feedback[(question, answer)] - full[(question, answer)]
                )
            by_question = {
                question: mean(items) for question, items in sorted(deltas.items())
            }
            result[model][framework] = {
                "estimate": mean(by_question.values()) if by_question else None,
                "by_question": by_question,
                "n_answers": len(common),
                "direction": "positive_means_text_feedback_reduced_error",
            }
    return {model: dict(frameworks) for model, frameworks in result.items()}


def _paired_delta(
    rows: list[dict], baseline: str, target: str, model: str
) -> float | None:
    selected = [
        row
        for row in rows
        if str(row["model"]) == model
        and str(row["condition"]) in {baseline, target}
    ]
    orders = _expected_orders(selected)
    values, _excluded = _answer_order_means(
        selected,
        "normalized_absolute_error",
        orders,
        _default_order_plan(selected),
    )
    baseline_values = {
        (question, answer): value
        for (m, question, condition, answer), value in values.items()
        if condition == baseline
    }
    target_values = {
        (question, answer): value
        for (m, question, condition, answer), value in values.items()
        if condition == target
    }
    common = set(baseline_values) & set(target_values)
    if not common:
        return None
    return mean(target_values[key] - baseline_values[key] for key in common)


def _paired_delta_detail(
    rows: list[dict], baseline: str, target: str, model: str
) -> dict:
    """Per-question paired deltas after averaging each answer's history orders.

    Reuses the same answer-level pairing key as ``_paired_delta``.  Cells are
    the mean normalized-error delta per question; the scalar summary is over
    question means so every question is weighted equally.  No resampling.
    """
    selected = [
        row
        for row in rows
        if str(row["model"]) == model
        and str(row["condition"]) in {baseline, target}
    ]
    orders = _expected_orders(selected)
    values, _excluded = _answer_order_means(
        selected,
        "normalized_absolute_error",
        orders,
        _default_order_plan(selected),
    )
    baseline_values = {
        (question, answer): value
        for (m, question, condition, answer), value in values.items()
        if condition == baseline
    }
    target_values = {
        (question, answer): value
        for (m, question, condition, answer), value in values.items()
        if condition == target
    }
    cells: dict[tuple[str, str], list[float]] = defaultdict(list)
    for question, answer in sorted(set(baseline_values) & set(target_values)):
        cells[(question, "order_mean")].append(
            target_values[(question, answer)] - baseline_values[(question, answer)]
        )
    if not cells:
        return {
            "n_paired": 0,
            "n_cells": 0,
            "cells": {},
            "mean": None,
            "min": None,
            "max": None,
            "median": None,
        }
    cell_means = {
        f"{question} / {order}": mean(deltas)
        for (question, order), deltas in sorted(cells.items())
    }
    values_list = list(cell_means.values())
    return {
        "n_paired": sum(len(deltas) for deltas in cells.values()),
        "n_cells": len(cell_means),
        "cells": cell_means,
        "mean": mean(values_list),
        "min": min(values_list),
        "max": max(values_list),
        "median": _median(values_list),
    }


def _raw_paired_delta(
    rows: list[dict], baseline: str, target: str, model: str
) -> float | None:
    cells: dict[tuple[str, str, str, int], dict[str, dict]] = defaultdict(dict)
    for row in rows:
        if str(row["model"]) != model or row["condition"] not in {baseline, target}:
            continue
        key = (
            str(row["question_id"]),
            str(row["order_variant"]),
            str(row["answer_id"]),
            int(row["repeat"]),
        )
        cells[key][str(row["condition"])] = row
    deltas = []
    for values in cells.values():
        if baseline in values and target in values:
            deltas.append(
                _metric(values[target], "normalized_absolute_error")
                - _metric(values[baseline], "normalized_absolute_error")
            )
    return mean(deltas) if deltas else None


def _improvement_counts(
    rows: list[dict], baseline: str, target: str, model: str
) -> dict[str, int]:
    selected = [
        row
        for row in rows
        if str(row["model"]) == model
        and str(row["condition"]) in {baseline, target}
    ]
    orders = _expected_orders(selected)
    values, _excluded = _answer_order_means(
        selected,
        "normalized_absolute_error",
        orders,
        _default_order_plan(selected),
    )
    by_question: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for (m, question, condition, _answer), value in values.items():
        by_question[question][condition].append(value)
    counts = {"improved": 0, "tied": 0, "worse": 0, "questions": 0}
    for values in by_question.values():
        if baseline not in values or target not in values:
            continue
        base, candidate = (
            mean(values[baseline]),
            mean(values[target]),
        )
        if base is None or candidate is None:
            continue
        counts["questions"] += 1
        if candidate < base:
            counts["improved"] += 1
        elif candidate > base:
            counts["worse"] += 1
        else:
            counts["tied"] += 1
    return counts


def _observed_grid(rows: list[dict]) -> dict[str, dict[str, object]]:
    """Per-question grid context over distinct answers.

    ``denominator`` is the frozen per-question score ceiling stamped on every
    row (equal for train and test).  ``observed_*`` and ``n_answers`` come from
    distinct answers so the count is not inflated by conditions/repeats.
    ``training_observed_max`` is the legacy training-observed ceiling kept as a
    transparency value (None when no training rows are present).
    """
    by_question: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_question[str(row["question_id"])].append(row)
    result: dict[str, dict[str, object]] = {}
    for question, group in sorted(by_question.items()):
        teacher_by_answer: dict[str, float] = {}
        train_by_answer: dict[str, float] = {}
        denominator = float(group[0]["max_score"])
        score_floor = float(group[0].get("score_floor", 0.0))
        for row in group:
            teacher_by_answer[str(row["answer_id"])] = float(row["teacher_score"])
            if row.get("split", "test") == "train":
                train_by_answer[str(row["answer_id"])] = float(row["teacher_score"])
        scores = list(teacher_by_answer.values())
        train_scores = list(train_by_answer.values())
        result[question] = {
            "denominator": denominator,
            "score_floor": score_floor,
            "observed_min": min(scores),
            "observed_max": max(scores),
            "training_observed_max": max(train_scores) if train_scores else None,
            "n_answers": len(scores),
        }
    return result


def _test_primary_blocks(
    test_rows: list[dict],
    *,
    primary_conditions: tuple[str, ...] = PRIMARY_CONDITIONS,
    memory_conditions: tuple[str, ...] = MEMORY_CONDITIONS,
    expected_orders_by_condition: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, object]:
    """Test-only final-memory outcomes; training rows never enter this block."""
    return {
        "condition_nae": _condition_nae(
            test_rows, primary_conditions, expected_orders_by_condition
        ),
        "memory_gain_vs_no_memory": _memory_gain(
            test_rows, memory_conditions, expected_orders_by_condition
        ),
        "test_successful_scores": len(test_rows),
    }


def _model_comparison(
    rows: list[dict], model: str, protocol_id: str = PROTOCOL_ID
) -> dict[str, object]:
    del protocol_id
    return {
        "mem0_full_minus_retrieval_full": _paired_delta(
            rows, "retrieval_full", "mem0_full", model
        ),
        "amem_full_minus_retrieval_full": _paired_delta(
            rows, "retrieval_full", "amem_full", model
        ),
        "mem0_improvement_counts": _improvement_counts(
            rows, "retrieval_full", "mem0_full", model
        ),
        "amem_improvement_counts": _improvement_counts(
            rows, "retrieval_full", "amem_full", model
        ),
        "mem0_full_minus_no_memory": _paired_delta(
            rows, "no_memory", "mem0_full", model
        ),
        "amem_full_minus_no_memory": _paired_delta(
            rows, "no_memory", "amem_full", model
        ),
        "retrieval_full_minus_no_memory": _paired_delta(
            rows, "no_memory", "retrieval_full", model
        ),
        "mem0_improvement_counts_vs_no_memory": _improvement_counts(
            rows, "no_memory", "mem0_full", model
        ),
        "amem_improvement_counts_vs_no_memory": _improvement_counts(
            rows, "no_memory", "amem_full", model
        ),
        "retrieval_improvement_counts_vs_no_memory": _improvement_counts(
            rows, "no_memory", "retrieval_full", model
        ),
        "mem0_full_minus_retrieval_full_detail": _paired_delta_detail(
            rows, "retrieval_full", "mem0_full", model
        ),
        "amem_full_minus_retrieval_full_detail": _paired_delta_detail(
            rows, "retrieval_full", "amem_full", model
        ),
        "mem0_full_minus_no_memory_detail": _paired_delta_detail(
            rows, "no_memory", "mem0_full", model
        ),
        "amem_full_minus_no_memory_detail": _paired_delta_detail(
            rows, "no_memory", "amem_full", model
        ),
        "retrieval_full_minus_no_memory_detail": _paired_delta_detail(
            rows, "no_memory", "retrieval_full", model
        ),
    }


def compute_report(
    rows: list[dict],
    *,
    failures: list[dict] | None = None,
    resources: dict | None = None,
    protocol_id: str | None = None,
) -> dict:
    """Compute the pre-registered descriptive estimands only.

    The function intentionally has no resampling path and never treats a
    failed call as a zero-valued score.
    """
    failures = failures or []
    resources = dict(resources or {})
    if not rows:
        raise ValueError("cannot analyze an empty successful-score set")
    models = sorted({str(row["model"]) for row in rows})
    questions = sorted({str(row["question_id"]) for row in rows})
    splits = {
        split: [row for row in rows if row.get("split", "test") == split]
        for split in ("train", "test")
    }
    test_rows = splits["test"]
    primary_conditions = PRIMARY_CONDITIONS
    memory_conditions = MEMORY_CONDITIONS
    feedback_pairs = {
        "retrieval": ("retrieval_full", "retrieval_no_feedback"),
        "mem0": ("mem0_full", "mem0_no_feedback"),
        "amem": ("amem_full", "amem_no_feedback"),
    }
    expected_orders_by_condition = _default_order_plan(test_rows)
    observed_order_variants = _expected_orders(test_rows)
    grid = _observed_grid(rows)
    split_groups: dict[str, dict[str, dict[str, dict[str, float | int | None]]]] = {}
    for split, split_rows in splits.items():
        split_groups[split] = {}
        for model in models:
            split_groups[split][model] = {}
            for condition in sorted({str(row["condition"]) for row in split_rows}):
                split_groups[split][model][condition] = _descriptive_metrics(
                    [
                        row
                        for row in split_rows
                        if str(row["model"]) == model
                        and str(row["condition"]) == condition
                    ]
                )
    report = {
        "protocol": protocol_id or PROTOCOL_ID,
        "report_type": "descriptive_exploratory",
        "analysis_method": {
            "metric_schema": "memory-effect-v2",
            "primary_metric": "answer_order_mean_then_question_macro_normalized_absolute_error",
            "primary_contrast": "no_memory_minus_memory_positive_means_improvement",
            "question_weighting": "six_selected_questions_equal_weight"
            if len(questions) == 6
            else "selected_questions_equal_weight",
            "order_handling": "average_historical_orders_within_answer_before_question_mean",
            "order_variants": list(observed_order_variants),
            "order_sensitivity": _order_sensitivity_label(observed_order_variants),
            "failed_scores": "missing_not_zero",
            "bootstrap": False,
            "resampling": False,
            "significance_tests": False,
            "population_inference": False,
            "split_metrics": "train_and_test_are_reported_separately",
            "primary_scope": "test_only_new_answers",
            "normalization": "per_question_score_ceiling",
            "model_comparison_scope": "test_only_complete_answer_pairs",
            "training_trajectory_scope": "diagnostic_only_score_before_current_answer_write",
            "analysis_amendment": "adopted_after_reviewing_first_two_formal_questions",
        },
        "sample": {
            "questions": questions,
            "models": models,
            "successful_scores": len(rows),
            "train_successful_scores": len(splits["train"]),
            "test_successful_scores": len(splits["test"]),
            "failed_calls": len(failures),
        },
        "primary": {
            "scope": "test",
            **_test_primary_blocks(
                test_rows,
                primary_conditions=primary_conditions,
                memory_conditions=memory_conditions,
                expected_orders_by_condition=expected_orders_by_condition,
            ),
        },
        "grid_context": grid,
        "split_metrics": split_groups,
        "model_comparison": {
            model: _model_comparison(test_rows, model, protocol_id or PROTOCOL_ID)
            for model in models
        },
        "secondary": {
            "history_order_sensitivity": {
                model: {
                    condition: value
                    for condition, value in conditions.items()
                    if condition != "no_memory"
                }
                for model, conditions in _order_sensitivity(
                    test_rows, expected_orders_by_condition
                ).items()
            },
            "no_memory_run_variation": {
                model: conditions.get("no_memory")
                for model, conditions in _order_sensitivity(
                    test_rows, expected_orders_by_condition
                ).items()
                if conditions.get("no_memory") is not None
            },
            "feedback_ablation": _feedback_ablation(
                test_rows, feedback_pairs, expected_orders_by_condition
            ),
            "resource": resources,
        },
        "diagnostics": {
            "training_memory_trajectory": _training_trajectory(splits["train"]),
            "signed_bias": _signed_bias_by_condition(
                test_rows, expected_orders_by_condition
            ),
        },
        "failures": failures,
            "statements": [
            f"主指标仅覆盖 NEW（测试集）答案：先在同一答案内平均冻结历史顺序"
            f"（{ '、'.join(observed_order_variants) }），"
            "再在题内平均并对六题等权；归一化分母为每题评分上限。",
            "归一化分母冻结为每题数据库建的评分上限（该题全部历史评分的最大值）；"
            "各题网格下界（多数从 0 起，部分从 0.25 起）与训练实测最高分见 grid_context。",
            "记忆增益定义为无记忆 NAE 减去记忆条件 NAE，正值表示记忆改善评分；"
            f"仅使用基线和目标条件的冻结顺序（{ '、'.join(observed_order_variants) }）"
            "均完整的答案配对。",
            f"历史顺序敏感性比较冻结顺序（{ '、'.join(observed_order_variants) }）"
            "下最终记忆状态的题内平均 NAE 极差；"
            "无记忆对应值仅表示重复运行波动。",
            "训练轨迹记录当前答案写入记忆前的评分 NAE，仅作记忆形成过程诊断，"
            "不进入 NEW 测试集主指标。",
            "该评价结构是在查看前两道正式题后登记的分析修订，不追溯称为预注册。",
            "不进行 bootstrap、其他重采样、显著性检验或总体推断置信区间。",
            "回答、历史顺序和重复评分不是独立题目；不以统计显著性措辞表述。",
        ],
    }
    report["report_sha256"] = hashlib.sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return report


def compute_question_report(
    rows: list[dict],
    *,
    failures: list[dict] | None = None,
    resources: dict | None = None,
    protocol_id: str | None = None,
) -> dict:
    """Compute the same descriptive estimands for one completed question shard.

    This is an interim (mid-run) slice for local inspection only; the
    pre-registered conclusions remain those of the full six-question report
    released after the integrity audit.
    """
    failures = failures or []
    resources = dict(resources or {})
    if not rows:
        raise ValueError("cannot analyze an empty successful-score set")
    questions = sorted({str(row["question_id"]) for row in rows})
    if len(questions) != 1:
        raise ValueError("question report requires exactly one question_id")
    models = sorted({str(row["model"]) for row in rows})
    splits = {
        split: [row for row in rows if row.get("split", "test") == split]
        for split in ("train", "test")
    }
    test_rows = splits["test"]
    primary_conditions = PRIMARY_CONDITIONS
    memory_conditions = MEMORY_CONDITIONS
    feedback_pairs = {
        "retrieval": ("retrieval_full", "retrieval_no_feedback"),
        "mem0": ("mem0_full", "mem0_no_feedback"),
        "amem": ("amem_full", "amem_no_feedback"),
    }
    expected_orders_by_condition = _default_order_plan(test_rows)
    observed_order_variants = _expected_orders(test_rows)
    grid = _observed_grid(rows)
    split_groups: dict[str, dict[str, dict[str, dict[str, float | int | None]]]] = {}
    for split, split_rows in splits.items():
        split_groups[split] = {}
        for model in models:
            split_groups[split][model] = {}
            for condition in sorted({str(row["condition"]) for row in split_rows}):
                split_groups[split][model][condition] = _descriptive_metrics(
                    [
                        row
                        for row in split_rows
                        if str(row["model"]) == model
                        and str(row["condition"]) == condition
                    ]
                )
    report = {
        "protocol": protocol_id or PROTOCOL_ID,
        "report_type": "question_slice_descriptive",
        "analysis_method": {
            "metric_schema": "memory-effect-v2",
            "primary_metric": "answer_order_mean_then_question_macro_normalized_absolute_error",
            "primary_contrast": "no_memory_minus_memory_positive_means_improvement",
            "question_weighting": (
                "single_question_slice; the six-question equal weighting does not apply"
            ),
            "failed_scores": "missing_not_zero",
            "order_variants": list(observed_order_variants),
            "order_sensitivity": _order_sensitivity_label(observed_order_variants),
            "bootstrap": False,
            "resampling": False,
            "significance_tests": False,
            "population_inference": False,
            "split_metrics": "train_and_test_are_reported_separately",
            "primary_scope": "test_only_new_answers",
            "normalization": "per_question_score_ceiling",
            "model_comparison_scope": "test_only_complete_answer_pairs",
            "training_trajectory_scope": "diagnostic_only_score_before_current_answer_write",
            "analysis_amendment": "adopted_after_reviewing_first_two_formal_questions",
        },
        "sample": {
            "question_id": questions[0],
            "questions": questions,
            "models": models,
            "successful_scores": len(rows),
            "train_successful_scores": len(splits["train"]),
            "test_successful_scores": len(splits["test"]),
            "failed_calls": len(failures),
        },
        "primary": {
            "scope": "test",
            **_test_primary_blocks(
                test_rows,
                primary_conditions=primary_conditions,
                memory_conditions=memory_conditions,
                expected_orders_by_condition=expected_orders_by_condition,
            ),
        },
        "grid_context": grid,
        "split_metrics": split_groups,
        "model_comparison": {
            model: _model_comparison(test_rows, model, protocol_id or PROTOCOL_ID)
            for model in models
        },
        "secondary": {
            "history_order_sensitivity": {
                model: {
                    condition: value
                    for condition, value in conditions.items()
                    if condition != "no_memory"
                }
                for model, conditions in _order_sensitivity(
                    test_rows, expected_orders_by_condition
                ).items()
            },
            "no_memory_run_variation": {
                model: conditions.get("no_memory")
                for model, conditions in _order_sensitivity(
                    test_rows, expected_orders_by_condition
                ).items()
                if conditions.get("no_memory") is not None
            },
            "feedback_ablation": _feedback_ablation(
                test_rows, feedback_pairs, expected_orders_by_condition
            ),
            "resource": resources,
        },
        "diagnostics": {
            "training_memory_trajectory": _training_trajectory(splits["train"]),
            "signed_bias": _signed_bias_by_condition(
                test_rows, expected_orders_by_condition
            ),
        },
        "failures": failures,
        "statements": [
            "这是单题分片的中期草稿，仅供本地整理与核对。",
            "正式结论以完整性审计通过后的全量六题报告为准。",
            f"主指标仅覆盖该题 NEW（测试集）答案；先在答案内平均冻结历史顺序"
            f"（{ '、'.join(observed_order_variants) }），"
            "记忆增益为无记忆 NAE 减去记忆条件 NAE。",
            "训练阶段逐答案 NAE 仅作记忆形成轨迹诊断，不进入测试主指标。",
            "该评价结构是在查看前两道正式题后登记的分析修订。",
            "不进行 bootstrap、重采样、显著性检验或总体推断。",
        ],
    }
    report["report_sha256"] = hashlib.sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return report


__all__ = ["compute_question_report", "compute_report"]
