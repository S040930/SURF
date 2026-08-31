"""Descriptive pilot-level score analysis for r23 project (offline, read-only).

Runs the platform's own pre-registered metric functions on the completed
technical pilot, with a reduced bootstrap replicate count. Not a locked
report; pilot scores are embargoed by protocol and must not drive the
confirmatory formal run.
"""

from __future__ import annotations

import json
import sys

import pandas as pd
from sqlalchemy import select

from app.db.session import SessionLocal
from app.experiment.r23.analysis import AnalysisRow, build_report
from app.models.r23 import (
    R23EvaluationObservation,
    R23Project,
    R23SampleObservation,
    R23UniqueEvaluation,
)

PROJECT_NAME = "1"
REPLICATES = 300


def main() -> None:
    with SessionLocal() as db:
        project = db.execute(
            select(R23Project).where(R23Project.name == PROJECT_NAME)
        ).scalar_one()
        pairs = db.execute(
            select(R23UniqueEvaluation, R23SampleObservation)
            .join(
                R23EvaluationObservation,
                R23EvaluationObservation.evaluation_id == R23UniqueEvaluation.id,
            )
            .join(
                R23SampleObservation,
                R23SampleObservation.id == R23EvaluationObservation.observation_id,
            )
            .where(
                R23UniqueEvaluation.project_id == project.id,
                R23UniqueEvaluation.run_index == 0,
                R23UniqueEvaluation.status == "succeeded",
            )
        ).all()

    rows = [
        AnalysisRow(
            model_binding_id=evaluation.model_binding_id,
            dimension=observation.dimension,
            cluster_id=observation.derived_base_id or observation.prompt_sha256,
            prompt_sha256=observation.prompt_sha256,
            input_sha256=observation.input_sha256,
            label_x2=observation.label_x2,
            content_x2=int(evaluation.content_score_x2),
            organization_x2=int(evaluation.organization_score_x2),
            language_x2=int(evaluation.language_score_x2),
            word_count=observation.word_count,
        )
        for evaluation, observation in pairs
    ]
    print(f"rows={len(rows)}")

    frame = pd.DataFrame(
        {
            "dimension": r.dimension,
            "level": r.label_x2 / 2,
            "content": r.content_x2 / 2,
            "organization": r.organization_x2 / 2,
            "language": r.language_x2 / 2,
            "words": r.word_count,
        }
        for r in rows
    )
    print("\n=== per-level mean predicted score per channel ===")
    for dimension in ("content", "organization", "language"):
        sub = frame[frame.dimension == dimension]
        print(f"\n-- {dimension} corpus (n={len(sub)}) --")
        piv = sub.groupby("level").agg(
            n=("level", "size"),
            content=("content", "mean"),
            organization=("organization", "mean"),
            language=("language", "mean"),
        )
        print(piv.round(2).to_string())

    print("\n=== word counts by dimension ===")
    print(frame.groupby("dimension")["words"].describe().round(0).to_string())

    report = build_report(rows, bootstrap_replicates=REPLICATES)

    keep_cell = {
        "dimension",
        "evidence_level",
        "n",
        "mpa",
        "mpa_ci95",
        "mpa_p_raw",
        "mpa_p_holm",
        "h1_pass",
        "qwk_intended_label_recovery",
        "qwk_ci95",
        "qwk_p_raw",
        "qwk_p_holm",
        "h2_pass",
        "mae",
        "spearman_rho",
        "extreme_level_difference",
    }
    slim = {
        "bootstrap_replicates": report["bootstrap_replicates"],
        "cells": [
            {k: v for k, v in cell.items() if k in keep_cell}
            for cell in report["cells"]
        ],
        "organization_selectivity": report["organization_selectivity"],
        "stable_sensitivity": report["stable_sensitivity"],
        "language_length_prompt_baseline": report["language_length_prompt_baseline"],
        "language_common_prompt_sensitivity": report[
            "language_common_prompt_sensitivity"
        ],
        "adjacent": {
            cell["dimension"]: cell["adjacent_level_effects"]
            for cell in report["cells"]
        },
        "permutation": {
            cell["dimension"]: cell["permutation_negative_control"]
            for cell in report["cells"]
        },
    }
    print("\n=== platform build_report (reduced bootstrap) ===")
    print(json.dumps(slim, indent=1, ensure_ascii=False, default=float))


if __name__ == "__main__":
    sys.exit(main())
