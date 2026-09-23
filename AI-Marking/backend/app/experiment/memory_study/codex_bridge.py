"""Route official memory-framework LLM requests through the Codex runner.

Mem0 calls ``generate_response`` while the academic A-MEM implementation calls
``get_completion``.  The bridge deliberately implements both spellings and
keeps the invocation ledger separate from the parent ``MSCall`` attempt: one
official ``add``/``add_note`` operation can legitimately make several model
requests (extraction followed by evolution).
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.time import utc_now_naive
from app.experiment.common.codex_runner import CodexResult
from app.experiment.common.tokenization import token_count
from app.experiment.memory_study.memory import _redact_text
from app.models.memory_study import MSCall, MSFrameworkInvocation


class FrameworkBridgeError(RuntimeError):
    """An official framework request could not be completed."""


# The pinned framework revisions never hand us a usable schema for their
# extraction step: mem0 passes ``{"type": "json_object"}`` and documents the
# response shape in its own prompt instead (see
# ``mem0/memory/main.py::ADDITIVE_EXTRACTION_PROMPT``, "# OUTPUT FORMAT").  The
# API cannot infer a shape from ``json_object``, so the bridge has to supply one.
#
# This mirrors that documented contract field for field.  It is deliberately
# narrow, because the two obvious shortcuts both lose data:
#
# * the previous fallback was ``{"type": "object", "additionalProperties": true}``
#   -- rejected outright by the API, so the fallback never once worked;
# * dropping only ``additionalProperties`` leaves a property-less object, which
#   the strict subset resolves to "the empty object".  The model would return
#   ``{}``, mem0 would read an empty ``memory`` list, and the write would be
#   recorded as a *successful* ingest of nothing.
#
# ``linked_memory_ids`` is documented as optional ("Omit or pass []"), but the
# strict subset requires every declared property to be listed in ``required``;
# ``[]`` is the framework's own spelling of "none", so the key is required and
# the empty array carries the optional case.
_MEM0_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "memory": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": "string"},
                    "attributed_to": {"type": "string"},
                    "linked_memory_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["id", "text", "attributed_to", "linked_memory_ids"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["memory"],
    "additionalProperties": False,
}


class RunnerLike(Protocol):
    def run(self, **kwargs: Any) -> CodexResult: ...


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _token_count(value: str) -> int | None:
    try:
        return token_count(value)
    except Exception:
        # The invocation still belongs in the audit ledger when the local
        # tokenizer resource is unavailable.  The frozen run gate will catch
        # that environment issue separately rather than losing the failure.
        return None


def _response_schema(response_format: dict[str, Any] | None) -> dict[str, Any]:
    """Return the JSON Schema to hand to the CLI for one framework request.

    The document is *not* strictified here: the CLI boundary in
    ``CodexExecRunner.run`` owns that, so every schema source is covered by the
    same contract.  What this function owns is the meaning -- the two shape
    defects fixed here were semantic, not syntactic.
    """
    if isinstance(response_format, dict):
        if response_format.get("type") == "json_schema":
            nested = response_format.get("json_schema")
            schema = nested.get("schema") if isinstance(nested, dict) else None
            if isinstance(schema, dict):
                return schema
        # ``json_object`` is what mem0's extraction step sends, and it carries
        # no field information at all, so it lands on the fallback below.
    # A missing ``response_format`` -- or one this bridge does not recognise --
    # must still produce a *usable* shape.  Declaring "some object" is not
    # possible in the API's strict subset, so the fallback states the only
    # contract any pinned consumer actually reads.
    return copy.deepcopy(_MEM0_EXTRACTION_SCHEMA)


def _message_tuple(
    messages: Any, *, prompt: str | None = None
) -> tuple[dict[str, str], ...]:
    if messages is None:
        values: list[dict[str, str]] = []
    elif isinstance(messages, (list, tuple)):
        values = [
            {
                "role": str(item.get("role", "user")),
                "content": str(item.get("content", "")),
            }
            for item in messages
            if isinstance(item, dict)
        ]
    else:
        values = [{"role": "user", "content": str(messages)}]
    if prompt is not None:
        values.append({"role": "user", "content": prompt})
    if not any(item["role"] == "system" for item in values):
        values.insert(
            0,
            {
                "role": "system",
                "content": "Return only the JSON object required by the supplied schema.",
            },
        )
    return tuple(values)


@dataclass(slots=True)
class FrameworkInvocationRecorder:
    """Persist one row per internal request while sharing the parent Session.

    The ledger is audit evidence, so its durability depends on the caller: the
    rows are flushed onto the parent transaction and become permanent when that
    transaction commits.  ``MemoryStudyWorker._execute_call`` therefore no
    longer rolls the transaction back on a framework failure -- doing so erased
    exactly the rows that explain the failure, silenced the ``audit_project``
    check ``no_failed_framework_invocations``, and left ``ms_framework_invocations``
    empty for a run in which every single write had failed.
    """

    db: Session
    call: MSCall
    framework: str
    _sequence: int = 0
    failed: bool = False
    failed_phase: str | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        latest = self.db.scalar(
            select(func.max(MSFrameworkInvocation.sequence_number)).where(
                MSFrameworkInvocation.call_id == self.call.id,
                MSFrameworkInvocation.attempt_number == self.call.attempt_count,
            )
        )
        self._sequence = int(latest or 0)

    def _fingerprint(self, runner: Any) -> dict[str, Any]:
        fingerprint = getattr(runner, "runtime_fingerprint", None)
        if not callable(fingerprint):
            return {}
        try:
            return dict(fingerprint())
        except Exception as exc:
            return {"fingerprint_error": _redact_text(exc, 500)}

    def invoke(
        self,
        *,
        runner: RunnerLike,
        messages: tuple[dict[str, str], ...],
        runtime: dict[str, Any],
        schema_json: dict[str, Any],
        phase: str,
    ) -> CodexResult:
        self._sequence += 1
        request = {
            "messages": list(messages),
            "schema": schema_json,
        }
        started_at = utc_now_naive()
        started = time.monotonic()
        invocation = MSFrameworkInvocation(
            call_id=self.call.id,
            attempt_number=self.call.attempt_count,
            sequence_number=self._sequence,
            framework=self.framework,
            phase=phase,
            transport="codex_exec",
            requested_model=str(runtime["model"]),
            reasoning_effort=str(runtime.get("reasoning_effort", "")),
            cli_fingerprint_json=self._fingerprint(runner),
            request_sha256=_sha(request),
            status="running",
            started_at=started_at,
        )
        self.db.add(invocation)
        self.db.flush()
        try:
            run = getattr(runner, "run")
            parameters = inspect.signature(run).parameters
            supports_schema_json = "schema_json" in parameters or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
            if not supports_schema_json:
                # Substituting a generic container here would silently send the
                # model a different response shape than the framework asked for,
                # which is indistinguishable from a measurement defect.  Refuse
                # instead of guessing.
                raise FrameworkBridgeError(
                    "Codex runner cannot accept a framework-supplied JSON schema"
                )
            result = run(
                messages=messages, runtime=runtime, schema_json=schema_json
            )
            if not isinstance(result, CodexResult):
                # Fake runners used by tests may return a structurally similar
                # object; accepting it keeps the bridge transport-focused.
                if not hasattr(result, "value"):
                    raise FrameworkBridgeError(
                        "Codex runner returned no structured value"
                    )
            raw = getattr(result, "raw_json", "") or _json(getattr(result, "value", {}))
            output_tokens = _token_count(raw)
            invocation.response_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            invocation.input_tokens = _token_count(_json(request))
            invocation.output_tokens = output_tokens
            invocation.total_tokens = (
                invocation.input_tokens + output_tokens
                if invocation.input_tokens is not None and output_tokens is not None
                else None
            )
            invocation.latency_ms = int((time.monotonic() - started) * 1000)
            invocation.status = "succeeded"
            invocation.completed_at = utc_now_naive()
            self.db.flush()
            return result
        except Exception as exc:
            self.failed = True
            self.failed_phase = phase
            self.last_error = _redact_text(exc, 500)
            invocation.status = "failed"
            invocation.error_type = type(exc).__name__
            invocation.error_message = _redact_text(exc, 2_000)
            invocation.latency_ms = int((time.monotonic() - started) * 1000)
            invocation.completed_at = utc_now_naive()
            # A database-level failure is the one case where the session is
            # unusable; flushing then would replace the real error with a
            # secondary one and hide the cause entirely.
            if self.db.is_active:
                self.db.flush()
            raise


class CodexFrameworkLLMBridge:
    """Adapter exposing both official framework LLM method names."""

    def __init__(
        self,
        *,
        runner: RunnerLike,
        runtime: dict[str, Any],
        recorder: FrameworkInvocationRecorder,
    ) -> None:
        self.runner = runner
        self.runtime = dict(runtime)
        self.recorder = recorder

    def _complete(
        self,
        *,
        messages: tuple[dict[str, str], ...],
        response_format: dict[str, Any] | None,
        phase: str,
    ) -> str:
        result = self.recorder.invoke(
            runner=self.runner,
            messages=messages,
            runtime=self.runtime,
            schema_json=_response_schema(response_format),
            phase=phase,
        )
        value = getattr(result, "value", None)
        if not isinstance(value, dict):
            raise FrameworkBridgeError("Codex framework response is not a JSON object")
        return _json(value)

    # Mem0's LLM interface.
    def generate_response(
        self,
        *,
        messages: Any,
        response_format: dict[str, Any] | None = None,
        **_: Any,
    ) -> str:
        return self._complete(
            messages=_message_tuple(messages),
            response_format=response_format,
            phase="mem0.generate_response",
        )

    # A-MEM's academic LLM interface.
    def get_completion(
        self,
        prompt: str,
        response_format: dict[str, Any] | None = None,
        **_: Any,
    ) -> str:
        return self._complete(
            messages=_message_tuple(None, prompt=prompt),
            response_format=response_format,
            phase="amem.get_completion",
        )


__all__ = [
    "CodexFrameworkLLMBridge",
    "FrameworkBridgeError",
    "FrameworkInvocationRecorder",
]
