"""End-to-end unified-core lifecycle test with a synthetic dataset/template.

The synthetic adapter and template register under unique keys and exercise the
full generic pipeline: data gate, runner alignment, run snapshot, serial
execution with two models sharing inputs, retest, report locking, export
privacy, and byte-reproducible reports.
"""

import re
from pathlib import Path
from uuid import uuid4

import pytest

from app.experiment.core.contracts import Channel, ScoringContract
from app.experiment.core.executor import run_one
from app.experiment.core.analysis import quadratic_weighted_kappa, weighted_mae
from app.experiment.core.registry import (
    AuditItem,
    DatasetAudit,
    GroupSpec,
    MaterializedInput,
    RegistryError,
    SamplingPlan,
    get_dataset,
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
from app.experiment.r21.codex_runner import CodexResult
from app.models.experiments import (
    ExpProject,
    ExpProjectRunner,
    ExpRunGroup,
    ExpUniqueEvaluation,
)
from app.services.experiments import ExperimentDomainError, ExperimentService

DATASET_KEY = "core-test-dataset"
TEMPLATE_ID = "core-test-template"
CONTRACT = ScoringContract(
    channels=(
        Channel(key="trait_a", label="Trait A"),
        Channel(key="trait_b", label="Trait B"),
    ),
    grid_min_x2=2,
    grid_max_x2=10,
)


class SyntheticAdapter:
    key = DATASET_KEY
    name = "Synthetic Core Dataset"
    access_level = "restricted"
    license_note = "synthetic test fixture"

    def audit(self, root: Path) -> DatasetAudit:
        items = []
        for index in range(24):
            prompt = f"prompt {index % 3}"
            items.append(
                AuditItem(
                    key=stable_key(DATASET_KEY, "input", str(index)),
                    prompt_sha256=stable_key("prompt", prompt),
                    prompt_norm=normalized_prompt(prompt),
                    labels_x2={
                        "trait_a": 2 + (index % 9),
                        "trait_b": 2 + ((index * 3) % 9),
                    },
                    word_count=100 + index,
                    provenance={"row_id": index},
                )
            )
        report = {
            "dataset_key": DATASET_KEY,
            "datasets": [
                {
                    "file": "synthetic.tsv",
                    "sha256": stable_key(DATASET_KEY, "file"),
                    "rows": 24,
                }
            ],
            "unique_inputs": 24,
        }
        return DatasetAudit(dataset_key=DATASET_KEY, report=report, items=tuple(items))

    def materialize(self, root, audit, keys):
        wanted = set(keys)
        materialized = []
        for item in audit.items:
            if item.key not in wanted:
                continue
            index = item.provenance["row_id"]
            materialized.append(
                MaterializedInput(
                    key=item.key,
                    prompt=f"prompt {index % 3}",
                    essay=f"essay text {index}",
                    labels_x2=dict(item.labels_x2),
                    word_count=item.word_count,
                    provenance=dict(item.provenance),
                )
            )
        return materialized

    def contract(self):
        return CONTRACT


class SyntheticTemplate:
    template_id = TEMPLATE_ID
    name = "Core Test Template"
    dataset_key = DATASET_KEY
    runner_count = 2
    require_runner_alignment = True
    seed = "core-test-seed"

    def sampling_plan(self, *, kind, audit, excluded_keys=()):
        items = []
        for item in audit.items:
            if item.key in set(excluded_keys):
                continue
            stratum = 1 if sum(item.labels_x2.values()) <= 10 else 2
            items.append(
                SampleItem(
                    key=item.key,
                    stratum=stratum,
                    cell=item.prompt_norm,
                    forced=item.labels_x2["trait_a"] <= 2,
                )
            )
        if kind == "pilot_run":
            # The pilot pool deliberately avoids the forced low tail.
            pilot_items = [
                SampleItem(
                    key=item.key, stratum=item.stratum, cell=item.cell, forced=False
                )
                for item in items
            ]
            selections = stratified_select(
                pilot_items, quota_by_stratum={1: 1, 2: 1}, seed=f"{self.seed}|pilot"
            )
            return SamplingPlan(selections=tuple(selections), retest_keys=frozenset())
        selections = stratified_select(
            items, quota_by_stratum={1: 4, 2: 4}, seed=f"{self.seed}|formal"
        )
        retest = select_retest(
            selections, quota_by_stratum={1: 1, 2: 1}, seed=f"{self.seed}|retest"
        )
        return SamplingPlan(selections=tuple(selections), retest_keys=frozenset(retest))

    def run_group_specs(self, *, kind):
        return [
            GroupSpec(group_key="primary", run_index=0),
            GroupSpec(group_key="retest", run_index=1),
        ]

    def build_report(self, *, project, rows, retest_rows, attempts):
        models = sorted({row["model"] for row in rows})
        per_model: dict[str, dict[str, dict[str, float]]] = {}
        for model in models:
            model_rows = [row for row in rows if row["model"] == model]
            weights = [row["weight"] for row in model_rows]
            metrics: dict[str, dict[str, float]] = {}
            for channel in ("trait_a", "trait_b"):
                labels = [row["labels_x2"][channel] for row in model_rows]
                predictions = [row["predictions_x2"][channel] for row in model_rows]
                metrics[channel] = {
                    "qwk": quadratic_weighted_kappa(
                        labels, predictions, min_x2=2, max_x2=10, weights=weights
                    ),
                    "mae": weighted_mae(labels, predictions, weights=weights),
                }
            per_model[model] = metrics
        report = {
            "template_id": project["template_id"],
            "kind": project["kind"],
            "models": models,
            "per_model": per_model,
            "retest_pairs": len(retest_rows),
            "attempt_count": len(attempts),
        }
        return report, [{"name": "summary", "svg": "<svg><rect/></svg>"}]


try:
    register_dataset(SyntheticAdapter())
    register_template(SyntheticTemplate())
except RegistryError:
    pass


class FakeRunner:
    def frozen_runtime(self, config):
        return {
            **config,
            "service_tier": "fast" if config["speed_mode"] == "fast" else "default",
            "executable_path": "/fake/codex",
            "executable_sha256": "f" * 64,
            "cli_version": "codex-fake 1",
        }

    def assert_matches(self, runtime):
        assert runtime["cli_version"] == "codex-fake 1"

    def run(self, *, messages, schema, runtime):
        match = re.search(r"essay text (\d+)", messages[1]["content"])
        assert match, "prompt envelope must contain the essay"
        index = int(match.group(1))
        answer = {
            "trait_a": 2 + (index % 9),
            "trait_b": 2 + ((index * 3) % 9),
        }
        if runtime["model"] == "model-b":
            answer = {key: max(2, value - 2) for key, value in answer.items()}
        return CodexResult(
            value={key: value / 2 for key, value in answer.items()},
            latency_ms=25,
            exit_code=0,
            stderr_excerpt="",
        )


RUBRIC_TEXT = (
    "Trait A assesses content relevance and development across levels. "
    "Trait B assesses organization and language control. Scores use the "
    "half-point grid from 1 through 5: trait_a and trait_b are both scored."
)


def _service(db_session):
    return ExperimentService(
        db_session, runner_factory=FakeRunner, datasets_root=Path("/restricted")
    )


def _setup(db_session):
    service = _service(db_session)
    runners = [
        service.create_runner_config(
            {
                "name": f"runner-{model}",
                "model": model,
                "reasoning_effort": "medium",
                "speed_mode": "standard",
                "timeout_seconds": 120,
            }
        )
        for model in ("model-a", "model-b")
    ]
    rubric = service.create_rubric(
        {"template_id": TEMPLATE_ID, "name": "rubric-v1", "rubric": RUBRIC_TEXT}
    )
    _, revision, _ = service.ensure_dataset_revision(DATASET_KEY)
    return service, runners, rubric, revision


def _create_formal(service, runners, rubric, revision, name, *, pilot_id=None):
    return service.create_project(
        {
            "name": name,
            "kind": "formal",
            "template_id": TEMPLATE_ID,
            "dataset_revision_id": revision.id,
            "rubric_id": rubric["id"],
            "runner_config_ids": [runners[0]["id"], runners[1]["id"]],
            "pilot_project_id": pilot_id,
            "data_processing_confirmed": True,
        }
    )


def test_full_formal_lifecycle_with_two_aligned_models(db_session):
    service, runners, rubric, revision = _setup(db_session)

    status = service.data_status(DATASET_KEY)
    assert status["ready"] is True
    assert status["contract"]["grid_min_x2"] == 2

    project = _create_formal(service, runners, rubric, revision, "core-formal")
    assert project["progress"]["total"] == 0
    started = service.start_project(project["id"])
    # 8 sampled inputs + 2 retest inputs per binding = 10 logical calls each.
    assert started["progress"]["total"] == 20
    assert started["manifest_summary"]["input_count"] == 8
    assert started["manifest_summary"]["retest_input_count"] == 2
    assert started["manifest_summary"]["forced_input_count"] == 3

    while run_one(db_session, worker_id="test", runner_factory=FakeRunner):
        pass

    project = service.get_project(project["id"])
    assert project["status"] == "completed"
    assert project["results_embargoed"] is False

    calls = service.list_calls(project["id"])
    assert all("scores" in item for item in calls["items"])
    assert all("essay" not in str(calls).casefold() for item in calls["items"])

    report = service.get_report(project["id"])
    assert report["results_embargoed"] is False
    assert report["report"]["report_type"] == "formal"
    assert report["report"]["retest_pairs"] == 4
    assert report["report"]["per_model"]["model-a"]["trait_a"]["qwk"] == pytest.approx(1.0)
    assert report["report"]["per_model"]["model-b"]["trait_a"]["qwk"] < 1.0
    assert [figure["name"] for figure in report["figures"]] == ["summary"]

    manifest, manifest_sha = service.export_manifest(project["id"])
    assert manifest_sha == project["manifest_sha256"]
    assert "essay text" not in manifest

    csv_payload, csv_sha = service.export_results_csv(project["id"])
    assert hashlib_sha(csv_payload) == csv_sha
    assert "essay text" not in csv_payload
    assert "prediction_trait_a" in csv_payload

    report_payload, report_sha = service.export_report_json(project["id"])
    import json as _json

    assert _json.loads(report_payload) == report["report"]
    assert report_sha == report["report_sha256"]
    figures_zip, zip_sha = service.export_figures_zip(project["id"])
    assert len(figures_zip) > 0
    assert zip_sha


def test_locked_report_is_byte_reproducible_across_projects(db_session):
    service, runners, rubric, revision = _setup(db_session)
    first = _create_formal(service, runners, rubric, revision, "formal-first")
    service.start_project(first["id"])
    while run_one(db_session, worker_id="test", runner_factory=FakeRunner):
        pass
    second = _create_formal(service, runners, rubric, revision, "formal-second")
    service.start_project(second["id"])
    while run_one(db_session, worker_id="test", runner_factory=FakeRunner):
        pass

    first_report = service.get_report(first["id"])
    second_report = service.get_report(second["id"])
    assert first_report["report_sha256"] == second_report["report_sha256"]
    assert first_report["report"] == second_report["report"]


def test_pilot_is_sealed_and_excluded_from_the_formal_pool(db_session):
    service, runners, rubric, revision = _setup(db_session)
    pilot = service.create_project(
        {
            "name": "core-pilot",
            "kind": "pilot_run",
            "template_id": TEMPLATE_ID,
            "dataset_revision_id": revision.id,
            "rubric_id": rubric["id"],
            "runner_config_ids": [runners[0]["id"], runners[1]["id"]],
            "data_processing_confirmed": True,
        }
    )
    started = service.start_project(pilot["id"])
    assert started["progress"]["total"] == 4  # 2 inputs × 2 bindings
    while run_one(db_session, worker_id="test", runner_factory=FakeRunner):
        pass
    pilot = service.get_project(pilot["id"])
    assert pilot["status"] == "completed"
    assert pilot["results_embargoed"] is True
    report = service.get_report(pilot["id"])
    assert report["report"]["report_type"] == "technical_pilot"
    assert "trait_a" not in str(report["report"])
    calls = service.list_calls(pilot["id"])
    assert all("scores" not in item for item in calls["items"])

    formal = _create_formal(
        service, runners, rubric, revision, "core-formal-after-pilot",
        pilot_id=pilot["id"],
    )
    started = service.start_project(formal["id"])
    # The 2 pilot inputs are excluded from the formal pool before sampling.
    assert started["manifest_summary"]["input_count"] == 8
    pilot_keys = {
        item["input_sha256"]
        for item in service._project(pilot["id"]).manifest_json["inputs"]
    }
    formal_keys = {
        item["input_sha256"]
        for item in service._project(formal["id"]).manifest_json["inputs"]
    }
    assert pilot_keys
    assert pilot_keys.isdisjoint(formal_keys)


def test_misaligned_comparison_runners_are_rejected(db_session):
    service, runners, rubric, revision = _setup(db_session)
    slow_runner = service.create_runner_config(
        {
            "name": "runner-slow",
            "model": "model-c",
            "reasoning_effort": "medium",
            "speed_mode": "standard",
            "timeout_seconds": 300,
        }
    )
    with pytest.raises(ExperimentDomainError) as excinfo:
        service.create_project(
            {
                "name": "misaligned",
                "kind": "formal",
                "template_id": TEMPLATE_ID,
                "dataset_revision_id": revision.id,
                "rubric_id": rubric["id"],
                "runner_config_ids": [runners[0]["id"], slow_runner["id"]],
                "data_processing_confirmed": True,
            }
        )
    assert excinfo.value.code == "validation"
    # A duplicate model identity is also rejected.
    twin = service.create_runner_config(
        {
            "name": "runner-twin",
            "model": "model-a",
            "reasoning_effort": "medium",
            "speed_mode": "standard",
            "timeout_seconds": 120,
        }
    )
    with pytest.raises(ExperimentDomainError):
        service.create_project(
            {
                "name": "duplicate-model",
                "kind": "formal",
                "template_id": TEMPLATE_ID,
                "dataset_revision_id": revision.id,
                "rubric_id": rubric["id"],
                "runner_config_ids": [runners[0]["id"], twin["id"]],
                "data_processing_confirmed": True,
            }
        )


def test_read_only_legacy_project_refuses_mutations(db_session):
    service, runners, rubric, revision = _setup(db_session)
    project = _create_formal(service, runners, rubric, revision, "legacy-imported")
    row = service._project(project["id"])
    row.read_only = True
    db_session.commit()
    with pytest.raises(ExperimentDomainError) as excinfo:
        service.start_project(project["id"])
    assert excinfo.value.code == "forbidden"


def test_retry_restores_call_group_and_project_state(db_session):
    project = ExpProject(
        id=str(uuid4()),
        template_id=TEMPLATE_ID,
        name=f"retry-{uuid4()}",
        kind="pilot_run",
        status="attention_required",
        data_processing_confirmed=True,
        data_status_json={},
        manifest_json={},
    )
    binding = ExpProjectRunner(
        id=str(uuid4()),
        project_id=project.id,
        runner_config_id="runner",
        position=1,
        model="model-a",
        config_sha256="c" * 64,
        frozen_runtime_json={},
    )
    group = ExpRunGroup(
        project_id=project.id,
        binding_id=binding.id,
        group_key="primary",
        run_index=0,
        status="blocked",
        order_rank=1,
        expected_calls=1,
        completed_calls=0,
    )
    db_session.add_all([project, binding, group])
    db_session.flush()
    evaluation = ExpUniqueEvaluation(
        project_id=project.id,
        binding_id=binding.id,
        run_group_id=group.id,
        input_id=1,
        input_sha256="i" * 64,
        run_index=0,
        status="attention_required",
        attempt_count=1,
        failure_code="interrupted",
    )
    db_session.add(evaluation)
    db_session.commit()

    result = ExperimentService(db_session).retry_call(project.id, evaluation.id)
    assert result["status"] == "pending"
    assert evaluation.failure_code is None
    assert project.status == "running"
    assert group.status == "running"


def hashlib_sha(payload: str) -> str:
    import hashlib

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hashlib_sha_payload(payload: str) -> str:
    return hashlib_sha(payload)
