"""Unit tests for the unified core kernel (no database)."""

import pytest

from app.experiment.core.contracts import (
    Channel,
    ContractError,
    ScoringContract,
    contract_from_payload,
)
from app.experiment.r23.protocol import scoring_messages as r23_scoring_messages

CHANNELS = (
    Channel(key="content", label="Content"),
    Channel(key="organization", label="Organization"),
    Channel(key="language", label="Language"),
)


def test_grid_covers_half_points_and_rejects_illegal_values():
    contract = ScoringContract(channels=CHANNELS, grid_min_x2=1, grid_max_x2=10)
    assert contract.score_values() == (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0)
    assert contract.is_on_grid(1)
    assert not contract.is_on_grid(0)
    with pytest.raises(ContractError):
        ScoringContract(channels=CHANNELS, grid_min_x2=10, grid_max_x2=10)
    with pytest.raises(ContractError):
        ScoringContract(channels=CHANNELS, grid_min_x2=0, grid_max_x2=10)
    with pytest.raises(ContractError):
        ScoringContract(channels=(), grid_min_x2=1, grid_max_x2=10)
    with pytest.raises(ContractError):
        ScoringContract(
            channels=(
                Channel(key="content", label="Content"),
                Channel(key="content", label="dup"),
            ),
            grid_min_x2=1,
            grid_max_x2=10,
        )


def test_dynamic_schema_has_exactly_the_contract_channels():
    contract = ScoringContract(channels=CHANNELS, grid_min_x2=1, grid_max_x2=10)
    schema = contract.output_json_schema()
    assert sorted(schema["properties"]) == ["content", "language", "organization"]
    assert schema["properties"]["content"]["enum"] == [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
    assert contract.score_model.model_config.get("extra") == "forbid"


def test_validate_scores_maps_to_x2_and_rejects_off_grid_and_extra_fields():
    contract = ScoringContract(channels=CHANNELS, grid_min_x2=1, grid_max_x2=10)
    assert contract.validate_scores(
        {"content": 0.5, "organization": 4, "language": 3.5}
    ) == {"content": 1, "organization": 8, "language": 7}
    with pytest.raises(ContractError):
        contract.validate_scores(
            {"content": 0.75, "organization": 4, "language": 3.5}
        )
    with pytest.raises(ContractError):
        contract.validate_scores(
            {"content": 0.5, "organization": 4, "language": 3.5, "total": 8}
        )
    with pytest.raises(ContractError):
        contract.validate_scores({"content": 0.5, "organization": 4})


def test_case_shaped_contract_reproduces_the_frozen_r23_envelope_verbatim():
    case_contract = ScoringContract(channels=CHANNELS, grid_min_x2=2, grid_max_x2=10)
    assert case_contract.scoring_messages(
        rubric="R", prompt="P", essay="E"
    ) == r23_scoring_messages(rubric="R", prompt="P", essay="E")


def test_payload_round_trip_preserves_the_contract():
    contract = ScoringContract(channels=CHANNELS, grid_min_x2=1, grid_max_x2=10)
    rebuilt = contract_from_payload(contract.payload())
    assert rebuilt.payload() == contract.payload()
    assert rebuilt.schema_sha256() == contract.schema_sha256()
    assert rebuilt.validate_scores({"content": 2.5, "organization": 5.0, "language": 1.0}) == {
        "content": 5,
        "organization": 10,
        "language": 2,
    }
