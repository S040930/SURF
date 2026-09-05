"""DREsS_CASE adapter and rubric-sensitivity template on the unified core.

This is a behaviour-preserving port of the frozen r23 protocol: the dataset
gate, pairing reconstruction, fixed sampling, and the pre-registered analysis
are delegated verbatim to ``app.experiment.r23`` (never modified), so a golden
test can prove byte-identical behaviour.  CASE is an observation-level design
(one analysis row per dataset observation; colliding texts share a deduped
evaluation), which the unified core hosts through its observation-slot layer.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.experiment.core.contracts import Channel, ScoringContract
from app.experiment.core.registry import (
    AuditItem,
    DatasetAudit,
    GroupSpec,
    MaterializedInput,
    SamplingPlan,
    Selection,
    SlotSpec,
    register_dataset,
    register_template,
)
from app.experiment.r23.dataset import (
    DataGateError,
    _canonical_sha,
    _normalize,
    audit_data_root,
    materialize_sample,
    select_metadata,
)
from app.experiment.r23.protocol import BOOTSTRAP_REPLICATES
from app.experiment.r23.protocol import ProjectKind as R23ProjectKind
from app.experiment.r23 import SAMPLING_SEED

DATASET_KEY = "dress_case"
TEMPLATE_ID = "dress_case_sensitivity_v1"
TEMPLATE_NAME = "DREsS_CASE rubric 敏感性（r23 协议移植）"

CHANNEL_KEYS = ("content", "organization", "language")
# r23 builds evaluations in its observation order (content, language,
# organization); a cross-dimension shared input lands in the first observed
# dimension's run group, exactly as the frozen snapshot code did.
_KIND_MAP = {"pilot_run": "pilot_run", "formal": "formal"}


class DressCaseDataGateError(ValueError):
    """Raised when the pinned DREsS_CASE files fail the audit."""


def _slot_spec(observation) -> SlotSpec:
    # ``observation`` is a frozen r23 SampleObservation.
    return SlotSpec(
        input_key=observation.input_sha256,
        slot_key=observation.observation_key,
        channel=observation.dimension,
        label_x2=observation.label_x2,
        rerun=observation.rerun,
        provenance={
            "source_id_hash": observation.source_id_hash,
            "source_row": observation.source_row,
            "derived_base_id": observation.derived_base_id,
            "corruption_repeat": observation.corruption_repeat,
            "collision": observation.collision,
        },
    )


class DressCaseAdapter:
    key = DATASET_KEY
    name = "DREsS_CASE（合成腐蚀作文，三维固定水平）"
    access_level = "restricted"
    license_note = (
        "DREsS 仅限获授权用户访问，不允许个人分享；正文与审计数据不得离开受限根目录。"
    )

    def audit(self, root: Path) -> DatasetAudit:
        try:
            report = audit_data_root(root)
        except DataGateError as exc:
            raise DressCaseDataGateError(str(exc)) from exc
        # Embed both pre-registereed phase selections so the template's
        # sampling plan is a pure function of the audit, delegated verbatim to
        # the frozen sampling code. One item per observation, in the frozen
        # order, so the flattened slot order matches select_metadata exactly.
        items: list[AuditItem] = []
        for kind in ("pilot_run", "formal"):
            observations = materialize_sample(root, kind)
            for observation in observations:
                items.append(
                    AuditItem(
                        key=observation.input_sha256,
                        prompt_sha256=observation.prompt_sha256,
                        prompt_norm=observation.prompt,
                        labels_x2={observation.dimension: observation.label_x2},
                        word_count=observation.word_count,
                        provenance={
                            "kind": kind,
                            "slot": asdict(_slot_spec(observation)),
                        },
                    )
                )
        return DatasetAudit(
            dataset_key=DATASET_KEY, report=report, items=tuple(items)
        )

    def materialize(self, root: Path, audit: DatasetAudit, keys) -> list[MaterializedInput]:
        """Second streaming pass over the three pinned files for the given keys.

        Hashes use the frozen r23 normalization/canonicalization so keys match
        the legacy pipeline exactly.
        """
        import csv
        import sys

        from app.experiment.r23.dataset import DATASET_SPECS, EXPECTED_COLUMNS, _iter_rows

        csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
        wanted = set(keys)
        found: dict[str, MaterializedInput] = {}
        for spec in DATASET_SPECS:
            for source_row, row, fields in _iter_rows(root / spec.filename):
                if fields != EXPECTED_COLUMNS[spec.dimension]:
                    raise DressCaseDataGateError(
                        f"field mismatch while materializing {spec.filename}"
                    )
                prompt = _normalize(row["prompt"])
                essay = _normalize(row["essay"])
                key = _canonical_sha({"prompt": prompt, "essay": essay})
                if key not in wanted or key in found:
                    continue
                found[key] = MaterializedInput(
                    key=key,
                    prompt=prompt,
                    essay=essay,
                    # CASE labels live on observation slots, not on inputs.
                    labels_x2={},
                    word_count=len(essay.split()),
                    provenance={"dimension_of_first_row": spec.dimension},
                )
        missing = wanted - set(found)
        if missing:
            raise DressCaseDataGateError(
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
            grid_min_x2=2,
            grid_max_x2=10,
        )


class DressCaseSensitivityTemplate:
    """The r23 monotonicity/discrimination/selectivity design, verbatim.

    Sampling delegates to the frozen ``select_metadata``/``materialize_sample``
    and analysis to the frozen ``build_report``; see the golden tests for the
    byte-identity guarantees.  Cross-dimension input coincidences are part of
    the frozen design, so the template intentionally ignores ``excluded_keys``.
    """

    template_id = TEMPLATE_ID
    name = TEMPLATE_NAME
    dataset_key = DATASET_KEY
    runner_count = 1
    require_runner_alignment = False
    seed = str(SAMPLING_SEED)
    uses_observation_slots = True
    # Pre-registered 5,000 replicates; tests may shrink this on the class.
    analysis_bootstrap_replicates = BOOTSTRAP_REPLICATES

    def sampling_plan(self, *, kind, audit, excluded_keys=()):
        if kind not in _KIND_MAP:
            raise DressCaseDataGateError(f"unknown project kind: {kind}")
        slots: list[SlotSpec] = []
        first_dimension: dict[str, str] = {}
        for item in audit.items:
            if item.provenance.get("kind") != kind:
                continue
            slot = SlotSpec(**item.provenance["slot"])
            slots.append(slot)
            first_dimension.setdefault(item.key, slot.channel)
        selections = tuple(
            Selection(
                key=key,
                stratum=0,
                cell_key="",
                inclusion_probability=1.0,
                design_weight=1.0,
                forced=False,
                group_key=first_dimension[key],
            )
            for key in first_dimension
        )
        retest_keys = frozenset(slot.input_key for slot in slots if slot.rerun)
        return SamplingPlan(
            selections=selections, retest_keys=retest_keys, slots=tuple(slots)
        )

    def run_group_specs(self, *, kind):
        # Frozen order: run_index outer, dimension content → organization →
        # language, exactly as the r23 snapshot created its run groups.
        specs = [
            GroupSpec(group_key=dimension, run_index=run_index)
            for run_index in (0, 1)
            for dimension in CHANNEL_KEYS
        ]
        if kind == R23ProjectKind.PILOT.value:
            return [spec for spec in specs if spec.run_index == 0]
        return specs

    def build_report(self, *, project, rows, retest_rows, attempts):
        from app.experiment.r23.analysis import AnalysisRow, build_report as r23_build_report
        from app.experiment.r23.protocol import BOOTSTRAP_REPLICATES
        from app.services.r23 import R23Service

        analysis_rows = [
            AnalysisRow(
                model_binding_id=row["binding_id"],
                dimension=row["channel"],
                cluster_id=row["cluster_id"] or row["prompt_sha256"],
                prompt_sha256=row["prompt_sha256"],
                input_sha256=row["input_sha256"],
                label_x2=int(row["label_x2"]),
                content_x2=int(row["predictions_x2"]["content"]),
                organization_x2=int(row["predictions_x2"]["organization"]),
                language_x2=int(row["predictions_x2"]["language"]),
                word_count=int(row["word_count"]),
            )
            for row in rows
        ]
        report = r23_build_report(
            analysis_rows,
            bootstrap_replicates=getattr(self, "analysis_bootstrap_replicates", BOOTSTRAP_REPLICATES),
        )
        report["repeatability"] = self._repeatability(rows, retest_rows)
        report["manifest_sha256"] = project.get("manifest_sha256")
        report["model_bindings"] = [
            {
                "id": binding["id"],
                "position": binding["position"],
                "model": binding["model"],
                "reasoning_effort": binding["reasoning_effort"],
                "speed_mode": binding["speed_mode"],
                "service_tier": binding.get("service_tier"),
                "config_sha256": binding["config_sha256"],
            }
            for binding in project.get("runner_bindings", [])
        ]
        figure = R23Service._report_svg(report)
        return report, [{"name": "report_figure", "svg": figure}]

    def _repeatability(self, primary_rows, retest_rows) -> dict[str, Any]:
        """Replicates the frozen r23 service repeatability block verbatim."""
        primary: dict[tuple[str, str], dict[str, int]] = {}
        retest: dict[tuple[str, str], dict[str, int]] = {}
        for row in primary_rows:
            primary[(row["binding_id"], row["input_sha256"])] = row["predictions_x2"]
        for row in retest_rows:
            retest[(row["binding_id"], row["input_sha256"])] = row["predictions_x2"]
        diffs: dict[str, list[float]] = defaultdict(list)
        exact: dict[str, int] = defaultdict(int)
        totals: dict[str, int] = defaultdict(int)
        for key, first in primary.items():
            second = retest.get(key)
            if second is None:
                continue
            model_id = key[0]
            channel_diffs = [
                abs(int(first[channel]) - int(second[channel])) / 2
                for channel in CHANNEL_KEYS
            ]
            diffs[model_id].extend(channel_diffs)
            exact[model_id] += int(
                all(first[channel] == second[channel] for channel in CHANNEL_KEYS)
            )
            totals[model_id] += 1
        return {
            model_id: {
                "paired_inputs": totals[model_id],
                "three_channel_exact_rate": (
                    exact[model_id] / totals[model_id] if totals[model_id] else None
                ),
                "mean_absolute_channel_difference": (
                    sum(values) / len(values) if values else None
                ),
            }
            for model_id, values in diffs.items()
        }


register_dataset(DressCaseAdapter())
register_template(DressCaseSensitivityTemplate())

__all__ = [
    "DATASET_KEY",
    "DressCaseAdapter",
    "DressCaseDataGateError",
    "DressCaseSensitivityTemplate",
    "TEMPLATE_ID",
]
