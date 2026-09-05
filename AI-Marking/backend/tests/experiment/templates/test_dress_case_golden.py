"""Golden tests: the ported CASE template is behaviour-identical to frozen r23.

These tests delegate to the shipped restricted corpus (same convention as the
r23 live tests) and prove, on the real data, that
(a) the template's sampling plan reproduces the frozen r23 selection exactly,
    slot for slot, and
(b) the template's analysis core is byte-identical to ``r23.build_report``.
"""

from pathlib import Path

import pytest

from app.experiment.core.registry import get_dataset, get_template
from app.experiment.r23.analysis import AnalysisRow, build_report as r23_build_report
from app.experiment.r23.dataset import materialize_sample
from app.experiment.templates.dress_case import (
    DATASET_KEY,
    TEMPLATE_ID,
    DressCaseAdapter,
    DressCaseSensitivityTemplate,
)

DRESS_ROOT = Path("/Users/mac/Desktop/SURF/DREsS")
CASE_FILE = DRESS_ROOT / "DREsS_CASE_content.tsv"

pytestmark = pytest.mark.skipif(
    not CASE_FILE.is_file(),
    reason="restricted DREsS_CASE corpus not present on this machine",
)


@pytest.fixture(scope="module")
def adapter():
    return DressCaseAdapter()


def _analysis_rows_for_golden() -> list[AnalysisRow]:
    """Two org bases, two prompts per channel, all nine levels, one model."""
    rows = []

    def add(dimension, cluster, prompt, level_x2, score_x2, index):
        rows.append(
            AnalysisRow(
                model_binding_id="binding-1",
                dimension=dimension,
                cluster_id=cluster,
                prompt_sha256=prompt,
                input_sha256=f"input-{dimension}-{index:03d}",
                label_x2=level_x2,
                content_x2=score_x2[0],
                organization_x2=score_x2[1],
                language_x2=score_x2[2],
                word_count=100 + index,
            )
        )

    index = 0
    for base in ("org-0001", "org-0002"):
        for level_x2 in range(2, 11):
            score = (
                min(10, level_x2 + 1),
                min(10, level_x2),
                min(10, level_x2 - 1 if level_x2 > 2 else 2),
            )
            add("organization", base, f"prompt-{base}", level_x2, score, index)
            index += 1
    for dimension in ("content", "language"):
        for prompt in (f"{dimension}-p1", f"{dimension}-p2"):
            for level_x2 in range(2, 11):
                score = (
                    min(10, level_x2),
                    min(10, level_x2 + (1 if dimension == "content" else -1) if level_x2 > 2 else 2),
                    min(10, level_x2),
                )
                add(dimension, prompt, prompt, level_x2, score, index)
                index += 1
    return rows


def _generic_rows(analysis_rows, run_index=0):
    return [
        {
            "binding_id": row.model_binding_id,
            "position": 1,
            "model": "model-a",
            "group_key": row.dimension,
            "run_index": run_index,
            "input_sha256": row.input_sha256,
            "prompt_sha256": row.prompt_sha256,
            "word_count": row.word_count,
            "stratum": None,
            "forced": False,
            "inclusion_probability": 1.0,
            "weight": 1.0,
            "labels_x2": {row.dimension: row.label_x2},
            "predictions_x2": {
                "content": row.content_x2,
                "organization": row.organization_x2,
                "language": row.language_x2,
            },
            "slot_key": f"slot-{row.input_sha256}",
            "channel": row.dimension,
            "label_x2": row.label_x2,
            "cluster_id": row.cluster_id,
            "provenance": {},
        }
        for row in analysis_rows
    ]


def test_template_is_registered_with_slot_semantics():
    assert get_dataset(DATASET_KEY).key == DATASET_KEY
    template = get_template(TEMPLATE_ID)
    assert template.runner_count == 1
    assert template.uses_observation_slots is True


def test_sampling_plan_reproduces_the_frozen_selection_exactly(adapter):
    template = DressCaseSensitivityTemplate()
    audit = adapter.audit(DRESS_ROOT)
    for kind in ("pilot_run", "formal"):
        frozen = materialize_sample(DRESS_ROOT, kind)
        plan = template.sampling_plan(kind=kind, audit=audit)
        assert [slot.slot_key for slot in plan.slots] == [
            observation.observation_key for observation in frozen
        ]
        for slot, observation in zip(plan.slots, frozen, strict=True):
            assert slot.input_key == observation.input_sha256
            assert slot.channel == observation.dimension
            assert slot.label_x2 == observation.label_x2
            assert slot.rerun == observation.rerun
            assert slot.provenance["source_id_hash"] == observation.source_id_hash
            assert slot.provenance["derived_base_id"] == observation.derived_base_id
            assert slot.provenance["collision"] == observation.collision
        unique_inputs = []
        first_dimension = {}
        for observation in frozen:
            if observation.input_sha256 not in first_dimension:
                first_dimension[observation.input_sha256] = observation.dimension
                unique_inputs.append(observation.input_sha256)
        assert [selection.key for selection in plan.selections] == unique_inputs
        assert all(
            selection.group_key == first_dimension[selection.key]
            for selection in plan.selections
        )
        assert plan.retest_keys == frozenset(
            observation.input_sha256 for observation in frozen if observation.rerun
        )


def test_analysis_core_is_byte_identical_to_frozen_build_report(monkeypatch):
    monkeypatch.setattr(DressCaseSensitivityTemplate, "analysis_bootstrap_replicates", 40)
    template = DressCaseSensitivityTemplate()
    analysis_rows = _analysis_rows_for_golden()
    frozen = r23_build_report(analysis_rows, bootstrap_replicates=40)

    rows = _generic_rows(analysis_rows)
    retest_rows = [
        dict(row, run_index=1, predictions_x2=dict(row["predictions_x2"]))
        for row in rows[:18]
    ]
    project = {
        "template_id": TEMPLATE_ID,
        "manifest_sha256": "m" * 64,
        "runner_bindings": [
            {
                "id": "binding-1",
                "position": 1,
                "model": "model-a",
                "reasoning_effort": "medium",
                "speed_mode": "standard",
                "service_tier": "default",
                "config_sha256": "c" * 64,
            }
        ],
    }
    report, figures = template.build_report(
        project=project, rows=rows, retest_rows=retest_rows, attempts=[]
    )

    golden_keys = set(frozen)
    assert {key: report[key] for key in golden_keys} == frozen
    assert set(report) == golden_keys | {"repeatability", "manifest_sha256", "model_bindings"}
    assert report["manifest_sha256"] == "m" * 64
    assert report["model_bindings"][0]["model"] == "model-a"
    assert report["repeatability"]["binding-1"]["paired_inputs"] == 18
    assert report["repeatability"]["binding-1"]["three_channel_exact_rate"] == 1.0
    assert report["repeatability"]["binding-1"]["mean_absolute_channel_difference"] == 0.0
    assert [figure["name"] for figure in figures] == ["report_figure"]


def test_analysis_detects_a_real_gap_identically(monkeypatch):
    """A deliberately non-monotone model yields the same numbers in both stacks."""
    monkeypatch.setattr(DressCaseSensitivityTemplate, "analysis_bootstrap_replicates", 40)
    template = DressCaseSensitivityTemplate()
    analysis_rows = _analysis_rows_for_golden()
    inverted = [
        AnalysisRow(
            model_binding_id=row.model_binding_id,
            dimension=row.dimension,
            cluster_id=row.cluster_id,
            prompt_sha256=row.prompt_sha256,
            input_sha256=row.input_sha256,
            label_x2=row.label_x2,
            content_x2=12 - row.label_x2,
            organization_x2=12 - row.label_x2,
            language_x2=12 - row.label_x2,
            word_count=row.word_count,
        )
        for row in analysis_rows
    ]
    frozen = r23_build_report(inverted, bootstrap_replicates=40)
    report, _ = template.build_report(
        project={"template_id": TEMPLATE_ID, "manifest_sha256": None, "runner_bindings": []},
        rows=_generic_rows(inverted),
        retest_rows=[],
        attempts=[],
    )
    assert {key: report[key] for key in set(frozen)} == frozen
    assert all(cell["mpa"] == 0.0 for cell in report["cells"])
