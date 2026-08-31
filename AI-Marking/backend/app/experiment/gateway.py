"""无隐藏重试、可审计的聊天 API gateway。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from app.core.time import utc_now_naive
from app.experiment.secrets import get_secret_by_name
from app.experiment.types import ModelConfig


class GatewayError(RuntimeError):
    """外部模型调用无法产生有效结果。"""


class RetriableGatewayError(GatewayError):
    """允许按固定策略重试的网络或服务错误。"""


class AmbiguousGatewayError(GatewayError):
    """请求可能已到达提供商，禁止自动重试。"""


class ConfigurationGatewayError(GatewayError):
    """认证、模型或参数配置错误；继续样本无意义。"""


class ModelIdentityError(ConfigurationGatewayError):
    """提供商返回的实际模型与冻结身份不同。"""


TRUNCATED_RESPONSE_ERROR = "response truncated by provider default limit"


@dataclass(frozen=True, slots=True)
class TransportResponse:
    raw_text: str
    returned_model: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    request_id: str | None
    system_fingerprint: str | None = None
    finish_reason: str | None = None
    refusal: str | None = None


class ChatTransport(Protocol):
    async def send(self, payload: dict[str, Any]) -> TransportResponse: ...


@dataclass(frozen=True, slots=True)
class ChatRequest:
    model: ModelConfig
    messages: tuple[dict[str, str], ...]
    output_schema: type[BaseModel]
    result_validator: Callable[[BaseModel], None] | None = None
    schema_prefix: str = "r20"

    def payload(self, validation_feedback: str | None = None) -> dict[str, Any]:
        instruction = self.messages[0]["content"]
        if validation_feedback:
            instruction = (
                f"{instruction}\n\n"
                "The previous response failed output validation. Keep all semantic "
                "judgment the same and return only a corrected response that obeys "
                f"the required schema. Validation error: {validation_feedback}"
            )
        messages = [dict(message) for message in self.messages]
        messages[0]["content"] = instruction
        schema_name = self.output_schema.__name__.replace("Output", "").lower()
        payload: dict[str, Any] = {
            "model": self.model.requested_model,
            "messages": messages,
            "temperature": self.model.temperature,
        }
        if self.model.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        else:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": f"{self.schema_prefix}_{schema_name}",
                    "strict": True,
                    "schema": self.output_schema.model_json_schema(),
                },
            }
        return payload


@dataclass(frozen=True, slots=True)
class CallAttempt:
    attempt_number: int
    request_hash: str
    requested_model: str
    returned_model: str | None
    raw_response: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    latency_ms: int
    status: str
    error_type: str | None
    error_message: str | None
    provider_request_id: str | None
    system_fingerprint: str | None
    started_at: datetime
    finish_reason: str | None = None
    refusal: str | None = None


@dataclass(frozen=True, slots=True)
class CallResult:
    request_hash: str
    raw_response: str
    parsed: BaseModel
    returned_model: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    system_fingerprint: str | None
    attempts: tuple[CallAttempt, ...]


AttemptCallback = Callable[[CallAttempt], None | Awaitable[None]]
BeforeAttemptCallback = Callable[[int, str], None | Awaitable[None]]


class ChatGateway:
    """一次完整逻辑调用：固定请求、最多三次、逐次可持久化。"""

    def __init__(
        self,
        transport: ChatTransport,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.transport = transport
        self.sleep = sleep

    async def complete(
        self,
        request: ChatRequest,
        *,
        on_attempt: AttemptCallback | None = None,
        before_attempt: BeforeAttemptCallback | None = None,
    ) -> CallResult:
        attempt_payload = request.payload()
        request_hash = canonical_request_hash(attempt_payload)
        attempts: list[CallAttempt] = []
        delays = (2,)
        last_error: Exception | None = None

        for attempt_number in range(1, 3):
            response = None
            started_at = utc_now_naive()
            request_hash = canonical_request_hash(attempt_payload)
            if before_attempt is not None:
                await _notify_before(before_attempt, attempt_number, request_hash)
            started = time.perf_counter()
            try:
                response = await self.transport.send(attempt_payload)
                latency_ms = round((time.perf_counter() - started) * 1000)
                if response.returned_model != request.model.expected_returned_model:
                    attempt = CallAttempt(
                        attempt_number=attempt_number,
                        request_hash=request_hash,
                        requested_model=request.model.requested_model,
                        returned_model=response.returned_model,
                        raw_response=response.raw_text,
                        input_tokens=response.input_tokens,
                        output_tokens=response.output_tokens,
                        total_tokens=response.total_tokens,
                        latency_ms=latency_ms,
                        status="identity_mismatch",
                        error_type="ModelIdentityError",
                        error_message=(
                            f"expected {request.model.expected_returned_model}, "
                            f"received {response.returned_model}"
                        ),
                        provider_request_id=response.request_id,
                        system_fingerprint=response.system_fingerprint,
                        finish_reason=response.finish_reason,
                        refusal=response.refusal,
                        started_at=started_at,
                    )
                    attempts.append(attempt)
                    await _notify(on_attempt, attempt)
                    raise ModelIdentityError(
                        "model identity mismatch: "
                        f"requested={request.model.requested_model}, "
                        f"expected={request.model.expected_returned_model}, "
                        f"received={response.returned_model}"
                    )
                if not response.raw_text.strip():
                    if response.finish_reason == "length":
                        raise ValueError(TRUNCATED_RESPONSE_ERROR)
                    raise ValueError("empty response")
                try:
                    parsed = request.output_schema.model_validate_json(
                        response.raw_text
                    )
                except (ValidationError, json.JSONDecodeError) as exc:
                    if response.finish_reason == "length":
                        raise ValueError(TRUNCATED_RESPONSE_ERROR) from exc
                    raise
                if request.result_validator is not None:
                    request.result_validator(parsed)
                attempt = CallAttempt(
                    attempt_number=attempt_number,
                    request_hash=request_hash,
                    requested_model=request.model.requested_model,
                    returned_model=response.returned_model,
                    raw_response=response.raw_text,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    total_tokens=response.total_tokens,
                    latency_ms=latency_ms,
                    status="success",
                    error_type=None,
                    error_message=None,
                    provider_request_id=response.request_id,
                    system_fingerprint=response.system_fingerprint,
                    finish_reason=response.finish_reason,
                    refusal=response.refusal,
                    started_at=started_at,
                )
                attempts.append(attempt)
                await _notify(on_attempt, attempt)
                return CallResult(
                    request_hash=request_hash,
                    raw_response=response.raw_text,
                    parsed=parsed,
                    returned_model=response.returned_model,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    total_tokens=response.total_tokens,
                    system_fingerprint=response.system_fingerprint,
                    attempts=tuple(attempts),
                )
            except ModelIdentityError:
                raise
            except ConfigurationGatewayError as exc:
                attempt = CallAttempt(
                    attempt_number=attempt_number,
                    request_hash=request_hash,
                    requested_model=request.model.requested_model,
                    returned_model=None,
                    raw_response=None,
                    input_tokens=None,
                    output_tokens=None,
                    total_tokens=None,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    status="configuration_error",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    provider_request_id=None,
                    system_fingerprint=None,
                    finish_reason=None,
                    refusal=None,
                    started_at=started_at,
                )
                attempts.append(attempt)
                await _notify(on_attempt, attempt)
                raise
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                latency_ms = round((time.perf_counter() - started) * 1000)
                response_value = response
                attempt = CallAttempt(
                    attempt_number=attempt_number,
                    request_hash=request_hash,
                    requested_model=request.model.requested_model,
                    returned_model=getattr(response_value, "returned_model", None),
                    raw_response=getattr(response_value, "raw_text", None),
                    input_tokens=getattr(response_value, "input_tokens", None),
                    output_tokens=getattr(response_value, "output_tokens", None),
                    total_tokens=getattr(response_value, "total_tokens", None),
                    latency_ms=latency_ms,
                    status="invalid_response",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    provider_request_id=getattr(response_value, "request_id", None),
                    system_fingerprint=getattr(
                        response_value, "system_fingerprint", None
                    ),
                    finish_reason=getattr(response_value, "finish_reason", None),
                    refusal=getattr(response_value, "refusal", None),
                    started_at=started_at,
                )
                attempts.append(attempt)
                await _notify(on_attempt, attempt)
                if attempt_number < 2:
                    attempt_payload = request.payload(
                        validation_feedback=str(exc)[:1200]
                    )
            except RetriableGatewayError as exc:
                last_error = exc
                attempt = CallAttempt(
                    attempt_number=attempt_number,
                    request_hash=request_hash,
                    requested_model=request.model.requested_model,
                    returned_model=None,
                    raw_response=None,
                    input_tokens=None,
                    output_tokens=None,
                    total_tokens=None,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    status="transport_error",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    provider_request_id=None,
                    system_fingerprint=None,
                    started_at=started_at,
                )
                attempts.append(attempt)
                await _notify(on_attempt, attempt)
            except AmbiguousGatewayError as exc:
                attempt = CallAttempt(
                    attempt_number=attempt_number,
                    request_hash=request_hash,
                    requested_model=request.model.requested_model,
                    returned_model=None,
                    raw_response=None,
                    input_tokens=None,
                    output_tokens=None,
                    total_tokens=None,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    status="uncertain",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    provider_request_id=None,
                    system_fingerprint=None,
                    started_at=started_at,
                )
                attempts.append(attempt)
                await _notify(on_attempt, attempt)
                raise
            if attempt_number < 2:
                await self.sleep(delays[attempt_number - 1])
        raise GatewayError(f"two attempts failed: {last_error}")


async def complete_request(
    request: ChatRequest,
    *,
    transport: ChatTransport | None = None,
    on_attempt: AttemptCallback | None = None,
    before_attempt: BeforeAttemptCallback | None = None,
) -> CallResult:
    """Complete one request and close transports owned by this call.

    ``asyncio.run`` is used by the synchronous worker and API boundaries.  An
    internally-created ``AsyncOpenAI`` client must therefore be closed before
    that event loop exits; otherwise httpx schedules cleanup on a closed loop.
    Injected transports remain caller-owned so scripted tests and shared
    adapters are not unexpectedly closed.
    """
    owns_transport = transport is None
    active_transport = (
        transport if transport is not None else OpenAIChatTransport(request.model)
    )
    try:
        return await ChatGateway(active_transport).complete(
            request,
            on_attempt=on_attempt,
            before_attempt=before_attempt,
        )
    finally:
        if owns_transport:
            close = getattr(active_transport, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result


def canonical_request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


async def _notify(callback: AttemptCallback | None, attempt: CallAttempt) -> None:
    if callback is None:
        return
    result = callback(attempt)
    if inspect.isawaitable(result):
        await result


async def _notify_before(
    callback: BeforeAttemptCallback, attempt_number: int, request_hash: str
) -> None:
    result = callback(attempt_number, request_hash)
    if inspect.isawaitable(result):
        await result


class ScriptedChatTransport:
    """外部 Chat API 的确定性测试 adapter。"""

    def __init__(self, responses: Sequence[TransportResponse | Exception]) -> None:
        self.responses = list(responses)
        self.payloads: list[dict[str, Any]] = []

    async def send(self, payload: dict[str, Any]) -> TransportResponse:
        self.payloads.append(json.loads(json.dumps(payload)))
        if not self.responses:
            raise AssertionError("scripted transport has no response")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class OpenAIChatTransport:
    """OpenAI Chat Completions 兼容提供商 adapter。"""

    sdk_max_retries = 0

    def __init__(self, config: ModelConfig) -> None:
        api_key = get_secret_by_name(config.api_key_env)
        if not api_key:
            raise ConfigurationGatewayError(f"missing {config.api_key_env}")
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=config.base_url,
            max_retries=self.sdk_max_retries,
            timeout=httpx.Timeout(config.timeout_seconds, connect=5.0),
        )

    async def send(self, payload: dict[str, Any]) -> TransportResponse:
        try:
            response = await self.client.chat.completions.create(**payload)
        except (APITimeoutError, APIConnectionError) as exc:
            raise AmbiguousGatewayError(str(exc)) from exc
        except RateLimitError as exc:
            raise RetriableGatewayError(str(exc)) from exc
        except APIStatusError as exc:
            if exc.status_code == 429:
                raise RetriableGatewayError(str(exc)) from exc
            if 500 <= exc.status_code < 600:
                raise RetriableGatewayError(str(exc)) from exc
            if exc.status_code == 408:
                raise AmbiguousGatewayError(str(exc)) from exc
            raise ConfigurationGatewayError(str(exc)) from exc
        choice = response.choices[0] if response.choices else None
        raw = choice.message.content if choice else ""
        usage = response.usage
        return TransportResponse(
            raw_text=raw or "",
            returned_model=response.model,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            request_id=getattr(response, "_request_id", None),
            system_fingerprint=getattr(response, "system_fingerprint", None),
            finish_reason=choice.finish_reason if choice else None,
            refusal=getattr(choice.message, "refusal", None) if choice else None,
        )

    async def close(self) -> None:
        await self.client.close()
