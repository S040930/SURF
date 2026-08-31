"""HTTP contracts for r20."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from app.experiment.r20.protocol import ProjectKind


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


class R20ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    kind: ProjectKind
    model_config_ids: list[str] = Field(min_length=2, max_length=2)
    prompt_version_id: str = Field(min_length=1)
    pilot_project_id: str | None = None

    @model_validator(mode="after")
    def distinct_models(self):
        if len(set(self.model_config_ids)) != 2:
            raise ValueError("r20 requires two distinct model configurations")
        return self


class R20ModelConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    config_json: dict
    api_key: str | None = Field(default=None, max_length=1000)


class R20ModelConfigOut(BaseModel):
    id: str
    name: str
    config_json: dict
    config_sha256: str
    status: str
    frozen_at: datetime | None = None
    created_at: datetime | None = None
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("frozen_at", "created_at")
    def model_dates(self, value):
        return _iso(value)


class R20PromptVersionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    scoring: str = Field(min_length=1, max_length=12000)
    crm_update: str = Field(min_length=1, max_length=12000)
    arm_update: str = Field(min_length=1, max_length=12000)


class R20PromptVersionOut(BaseModel):
    id: str
    protocol_id: str
    parent_version_id: str | None = None
    name: str
    templates_json: dict
    templates_sha256: str
    validated_model_config_ids_json: list[str] | None = None
    status: str
    frozen_at: datetime | None = None
    created_at: datetime | None = None
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("frozen_at", "created_at")
    def prompt_dates(self, value):
        return _iso(value)


class R20PromptValidationSuiteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_config_ids: list[str] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def distinct_models(self):
        if len(set(self.model_config_ids)) != 2:
            raise ValueError("validation requires two distinct model configurations")
        return self


class R20PromptValidationSuiteOut(BaseModel):
    id: str
    protocol_id: str
    prompt_version_id: str
    model_config_ids_json: list[str]
    status: str
    expected_calls: int
    completed_calls: int
    failure_reason: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", "started_at", "completed_at")
    def suite_dates(self, value):
        return _iso(value)


class R20ProjectOut(BaseModel):
    id: str
    protocol_id: str
    name: str
    kind: str
    status: str
    model_config_ids_json: list[str]
    prompt_version_id: str
    prompt_version_name: str
    pilot_project_id: str | None = None
    manifest_json: dict
    progress: dict | None = None
    created_at: datetime | None = None
    frozen_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", "frozen_at", "started_at", "completed_at")
    def dates(self, value):
        return _iso(value)


class R20RunGroupOut(BaseModel):
    id: int
    project_id: str
    question_id: str
    condition: str
    status: str
    order_rank: int
    expected_calls: int
    progress: dict | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("started_at", "completed_at")
    def dates(self, value):
        return _iso(value)


class R20ReportOut(BaseModel):
    status: str
    project_id: str
    report: dict | None = None
    report_sha256: str | None = None
    reason: str | None = None


class R20CallPageOut(BaseModel):
    items: list[dict]
    total: int
    summary: dict
