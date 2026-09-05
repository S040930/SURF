"""DREsS_New dataset adapter and human-agreement research template.

Dataset: ``DREsS_New.tsv`` (Yoo et al., ACL 2025) — 2,279 real-classroom EFL
essays with expert rubric-based scores on the original three-dimension rubric
(content / organization / language, 1–5 in 0.5 steps).  The adapter pins the
file hash and every audit constant verified against the shipped file; the
template implements the pre-registered sampling plan and the descriptive
human-agreement analysis.
"""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.experiment.core.contracts import Channel, ScoringContract
from app.experiment.core.registry import (
    AuditItem,
    DatasetAudit,
    GroupSpec,
    MaterializedInput,
    SamplingPlan,
    register_dataset,
    register_template,
)
from app.experiment.core.sampling import (
    SampleItem,
    normalized_prompt,
    select_retest,
    stable_key,
    stratified_select,
)
from app.experiment.core.analysis import (
    baseline_predictions,
    calibration_curve,
    exact_agreement_rate,
    paired_bootstrap_diff,
    quadratic_weighted_kappa,
    spearman_correlation,
    transition_matrix,
    weighted_mae,
    within_agreement_rate,
)

DATASET_KEY = "dress_new"
TEMPLATE_ID = "dress_new_human_agreement_v1"
TEMPLATE_NAME = "DREsS_New 人工评分一致性验证"
BOOTSTRAP_REPLICATES = 5_000

FILE_NAME = "DREsS_New.tsv"
EXPECTED_SHA256 = (
    "901c4b505cfd57b3ff4dace2926c0a02423a692cac335475311487162509d140"
)
EXPECTED_COLUMNS = (
    "id",
    "prompt",
    "essay",
    "content",
    "organization",
    "language",
    "total",
)
CHANNEL_KEYS = ("content", "organization", "language")

# Pre-registered audit constants, verified against the pinned file.
EXPECTED_RAW_ROWS = 2_279
EXPECTED_EMPTY_ESSAYS = 300
EXPECTED_CONFLICT_GROUPS = 5
EXPECTED_CONFLICT_ROWS = 10
EXPECTED_MERGED_DUPLICATE_RECORDS = 3
EXPECTED_UNIQUE_INPUTS = 1_966
# Prompts are normalized by whitespace collapse (including non-breaking
# spaces), so two raw variants that differ only in whitespace share a cell.
EXPECTED_DISTINCT_PROMPTS = 51
EXPECTED_LOW_TAIL = 96
EXPECTED_STRATA_SIZES = {"1": 278, "2": 498, "3": 572, "4": 424, "5": 194}

# Pre-registered sampling plan.
PILOT_QUOTA_PER_STRATUM = 6  # 30 pilot inputs, drawn from the non-tail pool
STRATUM_QUOTAS = {1: 150, 2: 175, 3: 200, 4: 125, 5: 70}  # formal: 720
RETEST_QUOTA = {1: 15, 2: 18, 3: 20, 4: 12, 5: 7}  # retest: 72

ORIGINAL_RUBRIC_TEXT = (
    "DREsS rubric (Yoo et al., ACL 2025, Table 2 — verbatim):\n"
    "\n"
    "Content: Paragraph is well-developed and relevant to the argument, "
    "supported with strong reasons and examples.\n"
    "\n"
    "Organization: The argument is very effectively structured and developed, "
    "making it easy for the reader to follow the ideas and understand how the "
    "writer is building the argument. Paragraphs use coherence devices "
    "effectively while focusing on a single main idea.\n"
    "\n"
    "Language: The writing displays sophisticated control of a wide range of "
    "vocabulary and collocations. The essay follows grammar and usage rules "
    "throughout the paper. Spelling and punctuation are correct throughout "
    "the paper.\n"
    "\n"
    "Scores: The essays are scored on a range of 1 to 5, with increments of "
    "0.5, based on the three rubrics: content, organization, and language."
)


class DressNewDataGateError(ValueError):
    """Raised when the pinned DREsS_New file fails the audit."""


def input_identity(prompt: str, essay: str) -> str:
    """Canonical identity of one input: whitespace-trimmed prompt + raw essay."""
    return hashlib.sha256(
        json.dumps(
            {"prompt": prompt.strip(), "essay": essay},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def stratum_of(total_x2: int) -> int:
    """Five strata on the recomputed total score (x2 units)."""
    if total_x2 <= 15:
        return 1
    if total_x2 <= 19:
        return 2
    if total_x2 <= 23:
        return 3
    if total_x2 <= 26:
        return 4
    return 5


def is_low_tail(labels_x2: dict[str, int]) -> bool:
    """Forced-inclusion rule: any dimension at or below 1.5."""
    return any(labels_x2[channel] <= 3 for channel in CHANNEL_KEYS)


def _read_rows(path: Path):
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t", quotechar='"')
        columns = tuple(reader.fieldnames or ())
        for row in reader:
            yield columns, row


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DressNewAdapter:
    key = DATASET_KEY
    name = "DREsS_New（真实课堂作文，专家三维评分）"
    access_level = "restricted"
    license_note = (
        "DREsS 仅限获授权用户访问，不允许个人分享；正文与审计数据不得离开受限根目录。"
    )

    def audit(self, root: Path) -> DatasetAudit:
        path = root / FILE_NAME
        if not path.is_file():
            raise DressNewDataGateError(f"missing dataset file: {path}")
        file_sha256 = _sha256_file(path)
        if file_sha256 != EXPECTED_SHA256:
            raise DressNewDataGateError(
                f"{FILE_NAME} sha256 {file_sha256} does not match the pinned "
                f"{EXPECTED_SHA256}"
            )

        raw_rows = 0
        empty_essay_rows = 0
        total_mismatches: list[dict[str, Any]] = []
        by_identity: dict[str, dict[str, Any]] = {}
        conflict_keys: set[str] = set()
        columns: tuple[str, ...] = ()
        for row_columns, row in _read_rows(path):
            raw_rows += 1
            columns = row_columns
            prompt = row["prompt"]
            essay = row["essay"]
            labels: dict[str, int] | None = None
            total_mismatch = False
            try:
                scores = [float(row[channel]) for channel in CHANNEL_KEYS]
                labels = {channel: int(round(score * 2)) for channel, score in zip(CHANNEL_KEYS, scores)}
                file_total = float(row["total"])
                recomputed = sum(scores)
                if abs(file_total - recomputed) > 1e-6:
                    total_mismatch = True
            except (KeyError, TypeError, ValueError):
                total_mismatch = False
            if essay.strip() == "":
                empty_essay_rows += 1
                if total_mismatch:
                    total_mismatches.append(
                        {
                            "source_id": row["id"],
                            "in_pool": False,
                            "file_total": row["total"],
                            "recomputed_total": None if labels is None else round(sum(labels[c] for c in CHANNEL_KEYS) / 2, 2),
                        }
                    )
                continue
            key = input_identity(prompt, essay)
            record = by_identity.get(key)
            if record is None:
                by_identity[key] = {
                    "labels": labels,
                    "word_count": len(essay.split()),
                    "prompt_norm": normalized_prompt(prompt),
                    "prompt_sha256": hashlib.sha256(
                        prompt.strip().encode("utf-8")
                    ).hexdigest(),
                    "source_id": row["id"],
                    "rows": 1,
                }
            else:
                record["rows"] += 1
                first_labels = record["labels"]
                if (
                    labels is not None
                    and first_labels is not None
                    and labels != first_labels
                ):
                    conflict_keys.add(key)
            if total_mismatch:
                total_mismatches.append(
                    {
                        "source_id": row["id"],
                        "in_pool": True,
                        "file_total": float(row["total"]),
                        "recomputed_total": round(
                            sum(labels[c] for c in CHANNEL_KEYS) / 2, 2
                        ),
                    }
                )

        if columns != EXPECTED_COLUMNS:
            raise DressNewDataGateError(
                f"unexpected columns {columns}; expected {EXPECTED_COLUMNS}"
            )
        if raw_rows != EXPECTED_RAW_ROWS:
            raise DressNewDataGateError(
                f"expected {EXPECTED_RAW_ROWS} raw rows, found {raw_rows}"
            )
        if empty_essay_rows != EXPECTED_EMPTY_ESSAYS:
            raise DressNewDataGateError(
                f"expected {EXPECTED_EMPTY_ESSAYS} empty-essay rows, "
                f"found {empty_essay_rows}"
            )
        if len(conflict_keys) != EXPECTED_CONFLICT_GROUPS:
            raise DressNewDataGateError(
                f"expected {EXPECTED_CONFLICT_GROUPS} label-conflict groups, "
                f"found {len(conflict_keys)}"
            )
        canonical_keys = [key for key in by_identity if key not in conflict_keys]
        if len(canonical_keys) != EXPECTED_UNIQUE_INPUTS:
            raise DressNewDataGateError(
                f"expected {EXPECTED_UNIQUE_INPUTS} unique inputs, "
                f"found {len(canonical_keys)}"
            )
        if len(by_identity) - len(canonical_keys) != EXPECTED_CONFLICT_GROUPS:
            raise DressNewDataGateError("conflict group bookkeeping drifted")

        items: list[AuditItem] = []
        strata_sizes: Counter[str] = Counter()
        distinct_prompts: set[str] = set()
        low_tail = 0
        conflict_rows = 0
        merged_duplicates = 0
        for key in canonical_keys:
            record = by_identity[key]
            labels = record["labels"]
            assert labels is not None
            merged_duplicates += record["rows"] - 1
            total_x2 = sum(labels[channel] for channel in CHANNEL_KEYS)
            stratum = stratum_of(total_x2)
            strata_sizes[str(stratum)] += 1
            distinct_prompts.add(record["prompt_norm"])
            if is_low_tail(labels):
                low_tail += 1
            items.append(
                AuditItem(
                    key=key,
                    prompt_sha256=record["prompt_sha256"],
                    prompt_norm=record["prompt_norm"],
                    labels_x2=dict(labels),
                    word_count=record["word_count"],
                    provenance={"source_id": record["source_id"]},
                )
            )
        for key in conflict_keys:
            conflict_rows += by_identity[key]["rows"]
        if conflict_rows != EXPECTED_CONFLICT_ROWS:
            raise DressNewDataGateError(
                f"expected {EXPECTED_CONFLICT_ROWS} rows in conflict groups, "
                f"found {conflict_rows}"
            )
        if merged_duplicates != EXPECTED_MERGED_DUPLICATE_RECORDS:
            raise DressNewDataGateError(
                f"expected {EXPECTED_MERGED_DUPLICATE_RECORDS} merged duplicate "
                f"records, found {merged_duplicates}"
            )
        if len(distinct_prompts) != EXPECTED_DISTINCT_PROMPTS:
            raise DressNewDataGateError(
                f"expected {EXPECTED_DISTINCT_PROMPTS} distinct prompts, "
                f"found {len(distinct_prompts)}"
            )
        if low_tail != EXPECTED_LOW_TAIL:
            raise DressNewDataGateError(
                f"expected {EXPECTED_LOW_TAIL} forced-inclusion (any dim <= 1.5) "
                f"inputs, found {low_tail}"
            )
        if dict(sorted(strata_sizes.items())) != EXPECTED_STRATA_SIZES:
            raise DressNewDataGateError(
                f"strata sizes drifted: {dict(sorted(strata_sizes.items()))}"
            )
        pool_mismatches = [item for item in total_mismatches if item["in_pool"]]

        report: dict[str, Any] = {
            "dataset_key": DATASET_KEY,
            "datasets": [
                {
                    "file": FILE_NAME,
                    "sha256": file_sha256,
                    "bytes": path.stat().st_size,
                    "rows": raw_rows,
                }
            ],
            "columns": list(EXPECTED_COLUMNS),
            "raw_rows": raw_rows,
            "empty_essay_rows": empty_essay_rows,
            "conflict_groups": len(conflict_keys),
            "conflict_rows": conflict_rows,
            "merged_duplicate_records": merged_duplicates,
            "unique_inputs": len(canonical_keys),
            "distinct_prompts": len(distinct_prompts),
            "low_tail_inputs": low_tail,
            "strata_sizes": dict(sorted(strata_sizes.items())),
            "total_column_mismatches": {
                "count": len(total_mismatches),
                "in_scoring_pool": len(pool_mismatches),
                "detail": total_mismatches,
                "rule": "综合分一律由三维重算，文件 total 列仅作交叉核对",
            },
        }
        ordered_items = sorted(items, key=lambda item: item.key)
        return DatasetAudit(
            dataset_key=DATASET_KEY, report=report, items=tuple(ordered_items)
        )

    def materialize(self, root: Path, audit: DatasetAudit, keys) -> list[MaterializedInput]:
        wanted = set(keys)
        found: dict[str, MaterializedInput] = {}
        seen_per_key: dict[str, int] = defaultdict(int)
        for _columns, row in _read_rows(root / FILE_NAME):
            essay = row["essay"]
            if essay.strip() == "":
                continue
            key = input_identity(row["prompt"], essay)
            if key not in wanted:
                continue
            seen_per_key[key] += 1
            if key in found:
                continue
            labels = {channel: int(round(float(row[channel]) * 2)) for channel in CHANNEL_KEYS}
            found[key] = MaterializedInput(
                key=key,
                prompt=row["prompt"].strip(),
                essay=essay,
                labels_x2=labels,
                word_count=len(essay.split()),
                provenance={"source_id": row["id"]},
            )
        missing = wanted - set(found)
        if missing:
            raise DressNewDataGateError(
                f"materialization missed {len(missing)} selected inputs"
            )
        return [found[key] for key in sorted(found)]

    def contract(self) -> ScoringContract:
        return ScoringContract(
            channels=(
                Channel(key="content", label="Content"),
                Channel(key="organization", label="Organization"),
                Channel(key="language", label="Language"),
            ),
            grid_min_x2=1,
            grid_max_x2=10,
        )


class DressNewHumanAgreementTemplate:
    """Descriptive human-agreement validation on the original DREsS rubric.

    No pass/fail thresholds: the locked report is validity evidence, not a
    publication gate.  Population estimates use the design weights recorded at
    snapshot time; unweighted numbers are sample diagnostics only.
    """

    template_id = TEMPLATE_ID
    name = TEMPLATE_NAME
    dataset_key = DATASET_KEY
    runner_count = 2
    require_runner_alignment = True
    seed = "20260905"
    uses_observation_slots = False

    def sampling_plan(self, *, kind, audit, excluded_keys=()):
        excluded = set(excluded_keys)
        items = []
        for item in audit.items:
            if item.key in excluded:
                continue
            if kind == "pilot_run" and is_low_tail(item.labels_x2):
                continue
            items.append(
                SampleItem(
                    key=item.key,
                    stratum=stratum_of(sum(item.labels_x2.values())),
                    cell=item.prompt_norm,
                    forced=is_low_tail(item.labels_x2),
                )
            )
        if kind == "pilot_run":
            quotas = {stratum: PILOT_QUOTA_PER_STRATUM for stratum in range(1, 6)}
            selections = stratified_select(
                items, quota_by_stratum=quotas, seed=f"{self.seed}|pilot"
            )
            return SamplingPlan(selections=tuple(selections), retest_keys=frozenset())
        selections = stratified_select(
            items, quota_by_stratum=dict(STRATUM_QUOTAS), seed=f"{self.seed}|formal"
        )
        retest = select_retest(
            selections, quota_by_stratum=dict(RETEST_QUOTA), seed=f"{self.seed}|retest"
        )
        return SamplingPlan(selections=tuple(selections), retest_keys=frozenset(retest))

    def run_group_specs(self, *, kind):
        if kind == "pilot_run":
            return [GroupSpec(group_key="primary", run_index=0)]
        return [
            GroupSpec(group_key="primary", run_index=0),
            GroupSpec(group_key="retest", run_index=1),
        ]

    def build_report(self, *, project, rows, retest_rows, attempts):
        channels = list(CHANNEL_KEYS)
        models = sorted({row["model"] for row in rows})
        grid_min_x2, grid_max_x2 = 1, 10

        per_model: dict[str, dict[str, Any]] = {}
        for model in models:
            model_rows = [row for row in rows if row["model"] == model]
            per_model[model] = {
                "count": len(model_rows),
                "channels": {
                    channel: self._channel_metrics(
                        model_rows,
                        channel,
                        grid_min_x2=grid_min_x2,
                        grid_max_x2=grid_max_x2,
                    )
                    for channel in channels
                },
            }

        comparison: dict[str, Any] = {}
        if len(models) == 2:
            left_rows = [row for row in rows if row["model"] == models[0]]
            right_rows = [row for row in rows if row["model"] == models[1]]
            for channel in channels:
                comparison[channel] = {
                    "qwk_diff": self._paired_diff(
                        left_rows, right_rows, channel, "qwk", grid_min_x2, grid_max_x2
                    ),
                    "mae_diff": self._paired_diff(
                        left_rows, right_rows, channel, "mae", grid_min_x2, grid_max_x2
                    ),
                }

        baseline_rows = [row for row in rows if row["model"] == models[0]] if models else []
        baseline = {
            "description": "log(word_count) + prompt 固定效应的确定性 10 折交叉验证基线",
            "disclosure": (
                "篇幅与题目启发式基线，仅作对照参考；它不是人类评分的替代品。"
            ),
            "channels": {
                channel: self._baseline_metrics(
                    baseline_rows, channel, grid_min_x2=grid_min_x2, grid_max_x2=grid_max_x2
                )
                for channel in channels
            },
        }

        retest: dict[str, Any] = {}
        for model in models:
            model_retest = [row for row in retest_rows if row["model"] == model]
            retest[model] = {
                "count": len(model_retest),
                "channels": {
                    channel: {
                        "exact_rate": round(
                            exact_agreement_rate(
                                [row["labels_x2"][channel] for row in model_retest],
                                [row["predictions_x2"][channel] for row in model_retest],
                            ),
                            4,
                        ),
                        "mae": round(
                            weighted_mae(
                                [row["labels_x2"][channel] for row in model_retest],
                                [row["predictions_x2"][channel] for row in model_retest],
                            ),
                            4,
                        ),
                        "transition_matrix": transition_matrix(
                            [row["labels_x2"][channel] for row in model_retest],
                            [row["predictions_x2"][channel] for row in model_retest],
                            min_x2=grid_min_x2,
                            max_x2=grid_max_x2,
                        ),
                    }
                    for channel in channels
                },
            }

        status_counts: Counter[str] = Counter(item["status"] for item in attempts)
        latencies = sorted(
            item["latency_ms"] for item in attempts if item["status"] == "succeeded"
        )
        weights = [row["weight"] for row in rows if row["model"] == models[0]] if models else []

        report: dict[str, Any] = {
            "template_id": TEMPLATE_ID,
            "report_type": "human_agreement",
            "design": {
                "sampling_seed": self.seed,
                "formal_quotas": {str(key): value for key, value in sorted(STRATUM_QUOTAS.items())},
                "retest_quota": {str(key): value for key, value in sorted(RETEST_QUOTA.items())},
                "bootstrap_replicates": BOOTSTRAP_REPLICATES,
                "design_weights": (
                    "总体指标使用设计权重 1/π；强制纳入尾部 π=1。"
                    "未加权结果仅作样本诊断展示。"
                ),
            },
            "sample_diagnostics": {
                "primary_rows_per_model": {
                    model: per_model[model]["count"] for model in models
                },
                "sum_design_weights": round(sum(weights), 4) if weights else None,
                "attempt_status_counts": dict(sorted(status_counts.items())),
                "latency_ms": {
                    "p50": statistics.median(latencies) if latencies else None,
                    "p95": _percentile(latencies, 0.95),
                },
            },
            "per_model": per_model,
            "model_comparison": comparison,
            "baseline": baseline,
            "retest": retest,
            "statements": [
                "本报告为描述性效度证据，不设自动通过或可发表阈值。",
                "同一批输入用于两个模型；配对 bootstrap 差异只作描述性比较。",
                "Spearman 相关为未加权样本诊断；加权指标为 QWK、MAE 与一致率。",
                "受限外推声明：这是同一数据集家族中的真实人工标签验证，"
                "不是独立外部语料验证。",
                "温度不可控披露：codex exec 不暴露温度控制。",
            ],
        }
        figures = self._figures(models, per_model, retest)
        return report, figures

    def _channel_metrics(self, model_rows, channel, *, grid_min_x2, grid_max_x2):
        labels = [row["labels_x2"][channel] for row in model_rows]
        predictions = [row["predictions_x2"][channel] for row in model_rows]
        weights = [row["weight"] for row in model_rows]
        return {
            "weighted_qwk": round(
                quadratic_weighted_kappa(
                    labels,
                    predictions,
                    min_x2=grid_min_x2,
                    max_x2=grid_max_x2,
                    weights=weights,
                ),
                4,
            ),
            "weighted_mae": round(weighted_mae(labels, predictions, weights=weights), 4),
            "unweighted_mae": round(weighted_mae(labels, predictions), 4),
            "spearman": round(spearman_correlation(labels, predictions), 4),
            "exact_rate": round(exact_agreement_rate(labels, predictions, weights=weights), 4),
            "within_half_point_rate": round(
                within_agreement_rate(
                    labels, predictions, tolerance_x2=1, weights=weights
                ),
                4,
            ),
            "within_one_point_rate": round(
                within_agreement_rate(
                    labels, predictions, tolerance_x2=2, weights=weights
                ),
                4,
            ),
            "calibration": calibration_curve(
                labels,
                predictions,
                min_x2=grid_min_x2,
                max_x2=grid_max_x2,
                weights=weights,
            ),
        }

    def _paired_diff(self, left_rows, right_rows, channel, metric, grid_min_x2, grid_max_x2):
        if metric == "qwk":
            def metric_fn(labels, scores, weights):
                return quadratic_weighted_kappa(
                    labels,
                    scores,
                    min_x2=grid_min_x2,
                    max_x2=grid_max_x2,
                    weights=weights,
                )
        else:

            def metric_fn(labels, scores, weights):
                return weighted_mae(labels, scores, weights=weights)

        left = sorted(left_rows, key=lambda row: row["input_sha256"])
        right = sorted(right_rows, key=lambda row: row["input_sha256"])
        assert [row["input_sha256"] for row in left] == [
            row["input_sha256"] for row in right
        ], "paired comparison requires both models on identical inputs"
        return paired_bootstrap_diff(
            left=metric_fn,
            right=metric_fn,
            left_labels=[row["labels_x2"][channel] for row in left],
            left_scores=[row["predictions_x2"][channel] for row in left],
            right_labels=[row["labels_x2"][channel] for row in right],
            right_scores=[row["predictions_x2"][channel] for row in right],
            weights=[row["weight"] for row in left],
            replicates=BOOTSTRAP_REPLICATES,
            seed=f"{self.seed}|bootstrap|{channel}|{metric}",
        )

    def _baseline_metrics(self, rows, channel, *, grid_min_x2, grid_max_x2):
        ordered = sorted(rows, key=lambda row: row["input_sha256"])
        labels = [row["labels_x2"][channel] for row in ordered]
        predictions_raw = baseline_predictions(
            keys=[row["input_sha256"] for row in ordered],
            word_counts=[row["word_count"] for row in ordered],
            prompts=[row["prompt_sha256"] for row in ordered],
            labels_x2=labels,
            seed=f"{self.seed}|baseline|{channel}",
        )
        predictions = [
            int(min(max(round(value * 2), grid_min_x2), grid_max_x2))
            for value in predictions_raw
        ]
        weights = [row["weight"] for row in ordered]
        return {
            "weighted_qwk": round(
                quadratic_weighted_kappa(
                    labels,
                    predictions,
                    min_x2=grid_min_x2,
                    max_x2=grid_max_x2,
                    weights=weights,
                ),
                4,
            ),
            "weighted_mae": round(weighted_mae(labels, predictions, weights=weights), 4),
            "exact_rate": round(exact_agreement_rate(labels, predictions, weights=weights), 4),
            "within_half_point_rate": round(
                within_agreement_rate(labels, predictions, tolerance_x2=1, weights=weights),
                4,
            ),
            "within_one_point_rate": round(
                within_agreement_rate(labels, predictions, tolerance_x2=2, weights=weights),
                4,
            ),
        }

    def _figures(self, models, per_model, retest):
        figures: list[dict[str, str]] = []
        for channel in CHANNEL_KEYS:
            figures.append(
                {
                    "name": f"calibration_{channel}",
                    "svg": _calibration_svg(channel, models, per_model),
                }
            )
        for model in models:
            for channel in CHANNEL_KEYS:
                figures.append(
                    {
                        "name": f"retest_transition_{model}_{channel}",
                        "svg": _transition_svg(
                            model,
                            channel,
                            retest[model]["channels"][channel]["transition_matrix"],
                        ),
                    }
                )
        return figures


GRID_VALUES = tuple(value / 2 for value in range(1, 11))


def _calibration_svg(channel: str, models: list[str], per_model: dict[str, Any]) -> str:
    width, height = 640, 360
    left, top, plot_w, plot_h = 64.0, 28.0, 528.0, 280.0
    colors = ["#6366f1", "#14b8a6"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="calibration {channel}">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{left}" y="20" font-size="14" fill="#0f172a">校准曲线 — {channel}</text>',
    ]
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = top + plot_h * (1 - fraction)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="#e2e8f0"/>'
        )
    diagonal_x1 = left
    diagonal_y1 = top + plot_h * (1 - 0.5 / 5.0)
    diagonal_x2 = left + plot_w
    diagonal_y2 = top
    parts.append(
        f'<line x1="{diagonal_x1:.1f}" y1="{diagonal_y1:.1f}" x2="{diagonal_x2:.1f}" '
        f'y2="{diagonal_y2:.1f}" stroke="#94a3b8" stroke-dasharray="4 4"/>'
    )
    for index, model in enumerate(models):
        points = per_model[model]["channels"][channel]["calibration"]
        color = colors[index % len(colors)]
        dots = []
        for point in points:
            x = left + plot_w * (point["score"] - 0.5) / 4.5
            y = top + plot_h * (1 - min(max(point["mean_label"], 0.0), 5.0) / 5.0)
            radius = 3 + min(point["count"], 60) / 8.0
            dots.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{color}" fill-opacity="0.85"/>'
            )
        parts.append(f'<g data-model="{model}">{"".join(dots)}</g>')
    parts.append("</svg>")
    return "".join(parts)


def _transition_svg(model: str, channel: str, matrix: list[list[int]]) -> str:
    size = len(matrix)
    cell = 36.0
    width = int(120 + cell * size + 40)
    height = int(90 + cell * size)
    maximum = max((value for row in matrix for value in row), default=0) or 1
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="retest transition {model} {channel}">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="16" y="28" font-size="14" fill="#0f172a">复测分数转移 — {model} · {channel}</text>',
    ]
    for row in range(size):
        y = 70 + row * cell
        if row % 2 == 0:
            parts.append(
                f'<text x="{100}" y="{y + cell / 2 + 4:.0f}" text-anchor="end" '
                f'font-size="9" fill="#64748b">{GRID_VALUES[row]:g}</text>'
            )
        for column in range(size):
            count = matrix[row][column]
            intensity = count / maximum
            x = 110 + column * cell
            fill = "#6366f1" if count else "#f1f5f9"
            opacity = 0.15 + 0.85 * intensity
            parts.append(
                f'<rect x="{x:.0f}" y="{y:.0f}" width="{cell - 2:.0f}" height="{cell - 2:.0f}" '
                f'rx="3" fill="{fill}" fill-opacity="{opacity:.2f}"/>'
            )
    for column in range(0, size, 2):
        x = 110 + column * cell + cell / 2
        parts.append(
            f'<text x="{x:.0f}" y="{70 + size * cell + 16:.0f}" text-anchor="middle" '
            f'font-size="9" fill="#64748b">{GRID_VALUES[column]:g}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _percentile(values: list[int], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    low, high = int(index), min(int(index) + 1, len(ordered) - 1)
    fraction = index - low
    return ordered[low] * (1 - fraction) + ordered[high] * fraction


register_dataset(DressNewAdapter())
register_template(DressNewHumanAgreementTemplate())

__all__ = [
    "DATASET_KEY",
    "DressNewAdapter",
    "DressNewDataGateError",
    "DressNewHumanAgreementTemplate",
    "EXPECTED_SHA256",
    "ORIGINAL_RUBRIC_TEXT",
    "TEMPLATE_ID",
]
