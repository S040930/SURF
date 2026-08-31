"""Strict types shared by the single-model experiment platform."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelConfig(StrictModel):
    """The sole, frozen OpenAI-compatible grading model service."""

    provider: Literal["openai_compatible"] = "openai_compatible"
    base_url: str = Field(pattern=r"^https://")
    requested_model: str = Field(min_length=1)
    expected_returned_model: str = Field(min_length=1)
    api_key_env: str = ""
    temperature: Literal[0] = 0
    response_format: Literal["json_schema", "json_object"] = "json_schema"
    timeout_seconds: float = Field(gt=0, le=600)
    input_cost_fen_per_million: int = Field(ge=0)
    output_cost_fen_per_million: int = Field(ge=0)
