"""r22 pilot service using the existing lifecycle tables and worker lease.

The r21 service remains the compatibility path for frozen r21 projects.  r22
creates a distinct protocol-id project, freezes the 40/10 schedule, and lets
the shared worker dispatch to the r22 executor.
"""

from __future__ import annotations

import inspect
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.time import utc_now_naive
from app.experiment.r20.dataset import build_frozen_dataset
from app.experiment.r22 import COMPRESSION_VERSION, PROTOCOL_ID
from app.experiment.r22.protocol import (
    CONDITIONS,
    PILOT_SCHEDULE,
    PROMPT_ENVELOPE_VERSION,
    TRAJECTORIES,
    expected_question_calls,
)
from app.models.r21 import R21Exposure, R21Project, R21RunGroup, R21Stream
from app.services.r21 import (
    R21DomainError,
    R21Service,
    canonical_sha256,
)


def _r22_analysis_sha() -> str:
    from app.experiment.r22 import analysis

    return canonical_sha256(inspect.getsource(analysis))


class R22Service(R21Service):
    """Lifecycle adapter for the expanded technical pilot."""

    def list_projects(self) -> list[dict[str, Any]]:
        runtime = self.runtime_status()
        return [
            self.project_out(row, runtime=runtime, include_performance=False)
            for row in self.db.scalars(
                select(R21Project).where(R21Project.protocol_id == PROTOCOL_ID).order_by(R21Project.created_at.desc())
            )
        ]

    def get_project(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._require(project.protocol_id == PROTOCOL_ID, "not_found", "r22 project does not exist")
        return self.project_out(project)

    def create_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("kind") != "pilot_run":
            raise R21DomainError("validation", "r22 currently supports pilot_run only")
        result = super().create_project(payload)
        project = self._project(result["id"])
        project.protocol_id = PROTOCOL_ID
        project.analysis_code_sha256 = _r22_analysis_sha()
        project.runner_runtime_json = {
            **project.runner_runtime_json,
            "prompt_envelope_version": PROMPT_ENVELOPE_VERSION,
            "compression_version": COMPRESSION_VERSION,
        }
        project.manifest_json = {"protocol": PROTOCOL_ID}
        self.db.commit()
        return self.project_out(project)

    def freeze_project(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._require(project.protocol_id == PROTOCOL_ID, "conflict", "not an r22 project")
        self._require(project.status == "draft", "conflict", "only draft projects can be frozen")
        dataset = build_frozen_dataset(
            settings.R20_SAF_ARCHIVE_PATH,
            settings.R20_SAF_SPLIT_MAP_PATH,
            pilot_schedule=PILOT_SCHEDULE,
            manifest_protocol=PROTOCOL_ID,
        )
        questions = sorted(key for key, value in dataset.roles.items() if value == "pilot_run")
        expected, order = 0, {}
        available = {row.answer_id: row for row in dataset.records if row.excluded_reason is None}
        for question_index, question in enumerate(questions):
            test_ids = dataset.test_endpoints[question]
            expected += expected_question_calls(PILOT_SCHEDULE)
            for position, answer_id in enumerate(test_ids):
                self._add_record(project, available[answer_id], question, dataset.max_scores[question], "test", 0, position, False)
            for trajectory, ids in dataset.trajectories[question].items():
                for position, answer_id in enumerate(ids, 1):
                    self._add_record(project, available[answer_id], question, dataset.max_scores[question], "memory", int(trajectory), position, False)
            sequence = []
            for condition_index, condition in enumerate(CONDITIONS):
                per_stream = PILOT_SCHEDULE.memory_count + (
                    PILOT_SCHEDULE.memory_count if condition != "nm" else 0
                ) + PILOT_SCHEDULE.test_count * len(PILOT_SCHEDULE.checkpoints) * 2
                self.db.add(R21RunGroup(
                    project_id=project.id, question_id=question, condition=condition,
                    status="pending", order_rank=question_index * 3 + condition_index,
                    expected_calls=per_stream * len(TRAJECTORIES),
                ))
                for trajectory in TRAJECTORIES:
                    self.db.add(R21Stream(
                        project_id=project.id, question_id=question, condition=condition,
                        trajectory=trajectory, order_rank=condition_index * 3 + trajectory - 1,
                        status="pending",
                    ))
                    sequence.append({"condition": condition, "trajectory": trajectory})
            order[question] = sequence
        manifest = {
            **dataset.manifest,
            "protocol": PROTOCOL_ID,
            "selected_questions": questions,
            "schedule": PILOT_SCHEDULE.as_dict(),
            "expected_calls": expected,
            "runner_config_sha256": project.runner_config_sha256,
            "prompt_version_sha256": project.prompt_version_sha256,
            "analysis_code_sha256": project.analysis_code_sha256,
            "prompt_envelope_version": project.runner_runtime_json.get("prompt_envelope_version"),
            "call_order": order,
        }
        project.manifest_json = manifest
        project.manifest_sha256 = canonical_sha256(manifest)
        project.data_sha256 = dataset.manifest["manifest_sha256"]
        project.status, project.frozen_at = "frozen", utc_now_naive()
        self.db.commit()
        return self.project_out(project)

    def start_project(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        self._require(project.protocol_id == PROTOCOL_ID, "conflict", "not an r22 project")
        self._require(project.status == "frozen", "conflict", "only a frozen project can start")
        self._require_no_other_running_project(project.id)
        current = build_frozen_dataset(
            settings.R20_SAF_ARCHIVE_PATH,
            settings.R20_SAF_SPLIT_MAP_PATH,
            pilot_schedule=PILOT_SCHEDULE,
            manifest_protocol=PROTOCOL_ID,
        )
        self._require(current.manifest["manifest_sha256"] == project.data_sha256, "conflict", "frozen data differs from the project manifest")
        group = self.db.query(R21RunGroup).filter_by(project_id=project.id, status="pending").order_by(R21RunGroup.order_rank).first()
        if group is None:
            raise R21DomainError("conflict", "project has no pending run groups")
        group.status, group.started_at = "queued", utc_now_naive()
        project.status, project.started_at = "running", project.started_at or utc_now_naive()
        self.db.add(R21Exposure(project_id=project.id, question_id=None, action="start_project", detail_json={"first_group_id": group.id}))
        self.db.commit()
        return self.project_out(project)
