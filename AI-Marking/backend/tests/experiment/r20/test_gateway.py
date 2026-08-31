from __future__ import annotations

from app.experiment.gateway import ChatGateway, ChatRequest, TransportResponse
from app.experiment.r20.protocol import ScoreOutput
from app.experiment.types import ModelConfig


class InvalidThenValid:
    def __init__(self):
        self.payloads = []

    async def send(self, payload):
        self.payloads.append(payload)
        raw = (
            "not-json" if len(self.payloads) == 1 else '{"score":1,"feedback":"Valid."}'
        )
        return TransportResponse(
            raw, "test-model", 1, 1, 2, f"request-{len(self.payloads)}"
        )


async def test_validation_retry_hash_matches_actual_changed_payload():
    transport = InvalidThenValid()
    attempts = []
    request = ChatRequest(
        model=ModelConfig(
            provider="openai_compatible",
            base_url="https://example.invalid/v1",
            requested_model="test-model",
            expected_returned_model="test-model",
            api_key_env="",
            temperature=0,
            timeout_seconds=1,
            input_cost_fen_per_million=0,
            output_cost_fen_per_million=0,
        ),
        messages=(
            {"role": "system", "content": "score"},
            {"role": "user", "content": "{}"},
        ),
        output_schema=ScoreOutput,
        schema_prefix="r20",
    )
    result = await ChatGateway(transport, sleep=lambda _delay: _noop()).complete(
        request, on_attempt=attempts.append
    )
    assert len(attempts) == 2
    assert attempts[0].request_hash != attempts[1].request_hash
    assert result.request_hash == attempts[1].request_hash
    assert "Validation error" in transport.payloads[1]["messages"][0]["content"]


def _request(response_format: str) -> ChatRequest:
    return ChatRequest(
        model=ModelConfig(
            provider="openai_compatible",
            base_url="https://example.invalid/v1",
            requested_model="test-model",
            expected_returned_model="test-model",
            api_key_env="",
            temperature=0,
            response_format=response_format,
            timeout_seconds=1,
            input_cost_fen_per_million=0,
            output_cost_fen_per_million=0,
        ),
        messages=(
            {"role": "system", "content": 'Return only JSON: {"score": 0.0}'},
            {"role": "user", "content": "{}"},
        ),
        output_schema=ScoreOutput,
        schema_prefix="r20",
    )


def test_payload_response_format_follows_config():
    schema_payload = _request("json_schema").payload()
    assert schema_payload["response_format"]["type"] == "json_schema"
    assert schema_payload["response_format"]["json_schema"]["strict"] is True

    object_payload = _request("json_object").payload()
    assert object_payload["response_format"] == {"type": "json_object"}


async def _noop():
    return None
