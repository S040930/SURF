"""Add the unified experiment platform (exp_) tables.

Revision ID: t5u6v7w8x9y0
Revises: s4i5j6k7l8m9
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "t5u6v7w8x9y0"
down_revision: Union[str, None] = "s4i5j6k7l8m9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES: dict[str, list[sa.Column]] = {
    "exp_datasets": [
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("key", sa.String(length=48), nullable=False, unique=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("access_level", sa.String(length=24), nullable=False),
        sa.Column("license_note", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ],
    "exp_dataset_revisions": [
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.String(length=36),
            sa.ForeignKey("exp_datasets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision_label", sa.String(length=96), nullable=False),
        sa.Column("file_specs_json", sa.JSON(), nullable=False),
        sa.Column("audit_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "dataset_id", "revision_label", name="uq_exp_revision_label"
        ),
    ],
    "exp_scoring_contracts": [
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "dataset_revision_id",
            sa.String(length=36),
            sa.ForeignKey("exp_dataset_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("channels_json", sa.JSON(), nullable=False),
        sa.Column("grid_min_x2", sa.Integer(), nullable=False),
        sa.Column("grid_max_x2", sa.Integer(), nullable=False),
        sa.Column("schema_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ],
    "exp_rubric_versions": [
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("template_id", sa.String(length=96), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("rubric_text", sa.Text(), nullable=False),
        sa.Column("rubric_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Index("ix_exp_rubric_status", "status"),
    ],
    "exp_runner_configs": [
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("reasoning_effort", sa.String(length=24), nullable=False),
        sa.Column(
            "speed_mode", sa.String(length=16), nullable=False, server_default="standard"
        ),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("config_sha256", sa.String(length=64), nullable=False),
        sa.Column("frozen_runtime_json", sa.JSON()),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Index("ix_exp_runner_status", "status"),
    ],
    "exp_projects": [
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("template_id", sa.String(length=96), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "dataset_revision_id",
            sa.String(length=36),
            sa.ForeignKey("exp_dataset_revisions.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "rubric_version_id",
            sa.String(length=36),
            sa.ForeignKey("exp_rubric_versions.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "pilot_project_id",
            sa.String(length=36),
            sa.ForeignKey("exp_projects.id", ondelete="RESTRICT"),
        ),
        sa.Column("formal_signature", sa.String(length=64)),
        sa.Column("data_processing_confirmed", sa.Boolean(), nullable=False),
        sa.Column("data_processing_confirmed_at", sa.DateTime()),
        sa.Column("data_status_json", sa.JSON(), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64)),
        sa.Column("results_embargoed", sa.Boolean(), nullable=False),
        sa.Column("report_sha256", sa.String(length=64)),
        sa.Column("read_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_system", sa.String(length=32), nullable=False),
        sa.Column("source_project_id", sa.String(length=36)),
        sa.Column("import_verification_json", sa.JSON()),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("frozen_at", sa.DateTime()),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("terminated_at", sa.DateTime()),
        sa.UniqueConstraint("name", name="uq_exp_project_name"),
        sa.UniqueConstraint(
            "source_system", "source_project_id", name="uq_exp_project_source"
        ),
        sa.Index("ix_exp_project_status", "status"),
        sa.Index("ix_exp_project_template", "template_id"),
    ],
    "exp_project_runners": [
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("exp_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "runner_config_id",
            sa.String(length=36),
            sa.ForeignKey("exp_runner_configs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("config_sha256", sa.String(length=64), nullable=False),
        sa.Column("frozen_runtime_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("project_id", "position", name="uq_exp_binding_position"),
        sa.UniqueConstraint(
            "project_id", "runner_config_id", name="uq_exp_binding_runner"
        ),
    ],
    "exp_inputs": [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("exp_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("essay_text", sa.Text(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("stratum", sa.Integer()),
        sa.Column("cell_key", sa.String(length=96)),
        sa.Column("inclusion_probability", sa.Float()),
        sa.Column("design_weight", sa.Float()),
        sa.Column("forced", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("selection_kind", sa.String(length=16), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("project_id", "input_sha256", name="uq_exp_input_key"),
        sa.Index("ix_exp_input_selection", "project_id", "selection_kind"),
    ],
    "exp_input_labels": [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "input_id",
            sa.Integer(),
            sa.ForeignKey("exp_inputs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(length=48), nullable=False),
        sa.Column("label_x2", sa.Integer(), nullable=False),
        sa.UniqueConstraint("input_id", "channel", name="uq_exp_input_label"),
    ],
    "exp_run_groups": [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("exp_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "binding_id",
            sa.String(length=36),
            sa.ForeignKey("exp_project_runners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("group_key", sa.String(length=48), nullable=False),
        sa.Column("run_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("order_rank", sa.Integer(), nullable=False),
        sa.Column("expected_calls", sa.Integer(), nullable=False),
        sa.Column("completed_calls", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.UniqueConstraint(
            "project_id",
            "binding_id",
            "group_key",
            "run_index",
            name="uq_exp_run_group",
        ),
        sa.Index("ix_exp_run_group_order", "project_id", "order_rank"),
    ],
    "exp_unique_evaluations": [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("exp_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "binding_id",
            sa.String(length=36),
            sa.ForeignKey("exp_project_runners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_group_id",
            sa.Integer(),
            sa.ForeignKey("exp_run_groups.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "input_id",
            sa.Integer(),
            sa.ForeignKey("exp_inputs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("run_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("failure_code", sa.String(length=64)),
        sa.Column("failure_summary", sa.Text()),
        sa.Column("worker_id", sa.String(length=64)),
        sa.Column("lease_until", sa.DateTime()),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime()),
        sa.UniqueConstraint(
            "project_id",
            "binding_id",
            "input_sha256",
            "run_index",
            name="uq_exp_unique_evaluation",
        ),
        sa.Index("ix_exp_evaluation_lease", "status", "lease_until"),
        sa.CheckConstraint("run_index >= 0", name="ck_exp_run_index_non_negative"),
    ],
    "exp_call_scores": [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "evaluation_id",
            sa.Integer(),
            sa.ForeignKey("exp_unique_evaluations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(length=48), nullable=False),
        sa.Column("score_x2", sa.Integer(), nullable=False),
        sa.UniqueConstraint("evaluation_id", "channel", name="uq_exp_call_score"),
        sa.CheckConstraint(
            "score_x2 >= 1 AND score_x2 <= 10", name="ck_exp_score_x2_grid"
        ),
    ],
    "exp_call_attempts": [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("exp_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evaluation_id",
            sa.Integer(),
            sa.ForeignKey("exp_unique_evaluations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("requested_model", sa.String(length=160), nullable=False),
        sa.Column("reasoning_effort", sa.String(length=24), nullable=False),
        sa.Column(
            "speed_mode", sa.String(length=16), nullable=False, server_default="standard"
        ),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("output_sha256", sa.String(length=64)),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("error_summary", sa.Text()),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "evaluation_id", "attempt_number", name="uq_exp_attempt_number"
        ),
        sa.Index("ix_exp_attempt_project", "project_id", "started_at"),
    ],
    "exp_events": [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("exp_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("detail_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Index("ix_exp_event_project", "project_id", "created_at"),
    ],
    "exp_locked_reports": [
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("exp_projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("report_sha256", sa.String(length=64), nullable=False),
        sa.Column("figures_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ],
}

_ORDER = (
    "exp_datasets",
    "exp_dataset_revisions",
    "exp_scoring_contracts",
    "exp_rubric_versions",
    "exp_runner_configs",
    "exp_projects",
    "exp_project_runners",
    "exp_inputs",
    "exp_input_labels",
    "exp_run_groups",
    "exp_unique_evaluations",
    "exp_call_scores",
    "exp_call_attempts",
    "exp_events",
    "exp_locked_reports",
)


def _existing_tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _existing_tables()
    for table in _ORDER:
        if table in existing:
            continue
        op.create_table(table, *_TABLES[table])


def downgrade() -> None:
    existing = _existing_tables()
    for table in reversed(_ORDER):
        if table in existing:
            op.drop_table(table)
