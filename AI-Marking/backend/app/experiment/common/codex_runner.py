"""Isolated, account-backed Codex CLI runner shared by all protocols.

The runner deliberately has no API-key or HTTP client dependency.  Every
logical call gets a new temporary working directory and a fresh ``codex exec``
session, so neither repository files nor a previous model turn can influence a
measurement.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.experiment.common.output_schema import strictify_output_schema

PROMPT_ENVELOPE_VERSION = "saf-memory-study-codex-exec-v2"

MAX_CAPTURE_BYTES = 64 * 1024
MAX_RESULT_BYTES = 256 * 1024
MAX_DIAGNOSTIC_CHARS = 800


class RunnerUnavailableError(RuntimeError):
    """Codex CLI is absent, not logged in, or cannot be started."""


class RunnerDriftError(RuntimeError):
    """The CLI executable changed after the runner was frozen."""


class RunnerExecutionError(RuntimeError):
    """A single ephemeral Codex session did not produce a valid result."""

    def __init__(self, message: str, *, stderr: str = "", exit_code: int | None = None):
        super().__init__(message)
        self.stderr = stderr
        self.exit_code = exit_code


class RunnerInterruptedError(RunnerExecutionError):
    """The MCP host disconnected while an ephemeral session was in flight."""


@dataclass(frozen=True, slots=True)
class CodexResult:
    value: dict[str, Any]
    latency_ms: int
    exit_code: int
    stderr_excerpt: str
    thread_id: str | None = None
    # The exact structured response bytes are retained for restricted local
    # measurement artifacts. Existing production consumers continue using
    # ``value`` and do not persist this field.
    raw_json: str = ""


def _limited(value: bytes, limit: int = MAX_CAPTURE_BYTES) -> str:
    if len(value) > limit:
        value = value[:limit] + b"\n...[truncated]"
    return value.decode("utf-8", errors="replace")


def _diagnostic(stderr: str, limit: int = MAX_DIAGNOSTIC_CHARS) -> str:
    """One-line stderr excerpt suitable for an error message.

    The CLI reports the real reason a session failed -- an invalid schema, a
    refused request, an auth problem -- on stderr, and nothing else in the stack
    preserves it.  Error messages therefore carry this excerpt so the cause
    survives into ``MSCall.failure_summary``, the attempt row, and the logs
    instead of being reduced to "exited with 1".
    """
    text = " ".join(stderr.split())
    if not text:
        return "<stderr empty>"
    if len(text) > limit:
        # Keep the tail: codex v0.154+ opens stderr with a banner and echoes
        # the entire stdin prompt, so the head is noise and the actual
        # failure line ("stream error", usage limit, auth) is at the end.
        return "..." + text[-limit:]
    return text


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CodexExecRunner:
    """Run one structured task through the locally authenticated Codex CLI."""

    def __init__(self, executable: str | None = None):
        resolved = executable or shutil.which("codex")
        if not resolved:
            raise RunnerUnavailableError("codex executable was not found on PATH")
        self.executable = Path(resolved).resolve()
        if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
            raise RunnerUnavailableError("codex executable is not runnable")
        self._active_process: subprocess.Popen[bytes] | None = None
        self._interrupted = False
        self._fingerprint_cache: dict[str, Any] | None = None

    def terminate_active(self, *, interrupted: bool = True) -> None:
        """Interrupt the one in-flight session when its MCP host disconnects."""
        process = self._active_process
        if process is None or process.poll() is not None:
            return
        self._interrupted = interrupted
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def runtime_fingerprint(self, *, refresh: bool = False) -> dict[str, Any]:
        # Framework invocations can make several requests per logical write;
        # re-running ``codex --version`` for every ledger row adds a needless
        # subprocess to the hot path.  Explicit drift checks request a fresh
        # value, while ordinary ledger capture reuses this immutable process
        # identity.
        if self._fingerprint_cache is not None and not refresh:
            return dict(self._fingerprint_cache)
        try:
            completed = subprocess.run(
                [str(self.executable), "--version"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RunnerUnavailableError(f"cannot inspect codex CLI: {exc}") from exc
        if completed.returncode != 0:
            raise RunnerUnavailableError(
                "codex --version failed: " + _limited(completed.stderr).strip()
            )
        fingerprint = {
            "executable_path": str(self.executable),
            "executable_sha256": _sha256_file(self.executable),
            "cli_version": _limited(completed.stdout).strip(),
            "sandbox": "read-only",
            "ephemeral": True,
            "ignore_user_config": True,
            "ignore_rules": True,
            "output_schema": True,
            "prompt_envelope_version": PROMPT_ENVELOPE_VERSION,
        }
        self._fingerprint_cache = dict(fingerprint)
        return fingerprint

    def frozen_runtime(self, config: dict[str, Any]) -> dict[str, Any]:
        runtime = self.runtime_fingerprint()
        runtime.update(
            {
                "model": config["model"],
                "reasoning_effort": config["reasoning_effort"],
                "timeout_seconds": config["timeout_seconds"],
            }
        )
        if "speed_mode" in config:
            speed_mode = str(config["speed_mode"])
            if speed_mode not in {"standard", "fast"}:
                raise ValueError(f"unsupported speed mode: {speed_mode}")
            runtime["speed_mode"] = speed_mode
            runtime["service_tier"] = "fast" if speed_mode == "fast" else "default"
        return runtime

    def assert_matches(self, frozen_runtime: dict[str, Any]) -> None:
        current = self.runtime_fingerprint(refresh=True)
        for key in ("executable_path", "executable_sha256", "cli_version"):
            if current[key] != frozen_runtime.get(key):
                raise RunnerDriftError(
                    f"Codex runner drift in {key}: expected {frozen_runtime.get(key)!r}, "
                    f"found {current[key]!r}"
                )

    def run(
        self,
        *,
        messages: tuple[dict[str, str], ...],
        runtime: dict[str, Any],
        schema: type[BaseModel] | None = None,
        schema_json: dict[str, Any] | None = None,
    ) -> CodexResult:
        """Execute one call and validate only its final JSON response.

        The output schema may arrive either as a Pydantic model or as a raw JSON
        Schema document (a framework supplies the latter, since its own call
        sites already speak JSON Schema).  Either way the document is rewritten
        into the strict subset the API requires *here*, at the CLI boundary,
        rather than trusting every caller to have done it: the previous
        assumption that "the schema reaching this point is already compliant"
        held for in-repo Pydantic models and silently failed for framework
        schemas, which cost an entire pilot run.
        """
        if schema is None and schema_json is None:
            raise ValueError("codex runner needs a pydantic or JSON output schema")
        prompt = self._prompt(messages)
        with tempfile.TemporaryDirectory(prefix="ai-marking-memory-study-") as workspace:
            workdir = Path(workspace)
            schema_path = workdir / "output-schema.json"
            result_path = workdir / "result.json"
            output_schema = strictify_output_schema(
                schema_json if schema_json is not None else schema.model_json_schema()
            )
            schema_path.write_text(
                json.dumps(output_schema, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            command = [
                str(self.executable),
                "exec",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--ignore-rules",
                "--color",
                "never",
                "--model",
                str(runtime["model"]),
                "--config",
                f'model_reasoning_effort="{runtime["reasoning_effort"]}"',
            ]
            if "service_tier" in runtime:
                command.extend(
                    ["--config", f'service_tier="{runtime["service_tier"]}"']
                )
                if runtime["service_tier"] == "fast":
                    command.extend(["--config", "features.fast_mode=true"])
            # Proxy/VPN blips drop the SSE stream mid-call; without explicit
            # budgets the whole exec aborts on the first drop and the call is
            # charged a failed attempt.  Retry the request 6x, reconnect the
            # stream 10x, and tolerate 5 idle minutes before declaring the
            # stream dead.
            command.extend(
                [
                    "--config",
                    "request_max_retries=6",
                    "--config",
                    "stream_max_retries=10",
                    "--config",
                    "stream_idle_timeout_ms=300000",
                ]
            )
            command.extend(
                [
                    "--output-schema",
                    str(schema_path),
                    "--output-last-message",
                    str(result_path),
                    "--cd",
                    str(workdir),
                    prompt,
                ]
            )
            started = time.monotonic()
            self._interrupted = False
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=workdir,
                )
                self._active_process = process
                stdout, stderr_bytes = process.communicate(
                    timeout=int(runtime["timeout_seconds"])
                )
            except subprocess.TimeoutExpired as exc:
                self.terminate_active(interrupted=False)
                stdout, stderr_bytes = process.communicate()
                stderr = _limited(stderr_bytes or exc.stderr or b"")
                raise RunnerExecutionError(
                    f"codex exec timed out after {runtime['timeout_seconds']}s; "
                    f"stderr: {_diagnostic(stderr)}",
                    stderr=stderr,
                ) from exc
            except OSError as exc:
                raise RunnerUnavailableError(f"cannot start codex exec: {exc}") from exc
            finally:
                self._active_process = None
            latency_ms = int((time.monotonic() - started) * 1000)
            stderr = _limited(stderr_bytes)
            if self._interrupted:
                raise RunnerInterruptedError(
                    f"MCP runner disconnected during codex exec; "
                    f"stderr: {_diagnostic(stderr)}",
                    stderr=stderr,
                    exit_code=process.returncode,
                )
            if process.returncode != 0:
                raise RunnerExecutionError(
                    f"codex exec exited with {process.returncode}; "
                    f"stderr: {_diagnostic(stderr)}",
                    stderr=stderr,
                    exit_code=process.returncode,
                )
            try:
                raw = result_path.read_bytes()
            except FileNotFoundError as exc:
                raise RunnerExecutionError(
                    f"codex exec did not write a structured result; "
                    f"stderr: {_diagnostic(stderr)}",
                    stderr=stderr,
                    exit_code=process.returncode,
                ) from exc
            if len(raw) > MAX_RESULT_BYTES:
                raise RunnerExecutionError(
                    f"codex structured result exceeded {MAX_RESULT_BYTES} bytes "
                    f"({len(raw)}); stderr: {_diagnostic(stderr)}",
                    stderr=stderr,
                    exit_code=process.returncode,
                )
            try:
                if schema_json is None:
                    assert schema is not None  # guaranteed by the guard above
                    parsed_value = schema.model_validate_json(raw).model_dump(
                        mode="json"
                    )
                else:
                    parsed_value = json.loads(raw)
                    if not isinstance(parsed_value, dict):
                        raise ValueError("framework response must be a JSON object")
            except Exception as exc:
                raise RunnerExecutionError(
                    f"codex structured result failed validation: {exc}; "
                    f"stderr: {_diagnostic(stderr)}",
                    stderr=stderr,
                    exit_code=process.returncode,
                ) from exc
            return CodexResult(
                value=parsed_value,
                latency_ms=latency_ms,
                exit_code=process.returncode,
                stderr_excerpt=stderr,
                raw_json=raw.decode("utf-8", errors="replace"),
            )

    @staticmethod
    def _prompt(messages: tuple[dict[str, str], ...]) -> str:
        system = next(
            (item["content"] for item in messages if item["role"] == "system"), ""
        )
        user = next(
            (item["content"] for item in messages if item["role"] == "user"), "{}"
        )
        return (
            "You are an isolated measurement runner. Follow the system instruction "
            "and input exactly. Do not inspect files, use tools, explain your reasoning, "
            "or add prose. Your final response must satisfy the supplied JSON Schema.\n\n"
            f"SYSTEM INSTRUCTION:\n{system}\n\nINPUT JSON:\n{user}"
        )
