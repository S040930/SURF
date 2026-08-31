"""Isolated, account-backed Codex CLI runner for r21.

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

from app.experiment.r21.protocol import PROMPT_ENVELOPE_VERSION

MAX_CAPTURE_BYTES = 64 * 1024
MAX_RESULT_BYTES = 256 * 1024


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


def _limited(value: bytes, limit: int = MAX_CAPTURE_BYTES) -> str:
    if len(value) > limit:
        value = value[:limit] + b"\n...[truncated]"
    return value.decode("utf-8", errors="replace")


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

    def runtime_fingerprint(self) -> dict[str, Any]:
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
        return {
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
        current = self.runtime_fingerprint()
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
        schema: type[BaseModel],
        runtime: dict[str, Any],
    ) -> CodexResult:
        """Execute one call and validate only its final JSON response."""
        # r21 uses the strict schema at the CLI boundary; r22 uses the
        # permissive transport schema so complete candidates reach the
        # recovery layer.  Both are emitted as JSON Schema with
        # additionalProperties: false, which the API requires.
        cli_schema = schema
        prompt = self._prompt(messages)
        with tempfile.TemporaryDirectory(prefix="ai-marking-r21-") as workspace:
            workdir = Path(workspace)
            schema_path = workdir / "output-schema.json"
            result_path = workdir / "result.json"
            schema_path.write_text(
                json.dumps(
                    cli_schema.model_json_schema(), ensure_ascii=False, sort_keys=True
                ),
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
                raise RunnerExecutionError(
                    "codex exec timed out",
                    stderr=_limited(stderr_bytes or exc.stderr or b""),
                ) from exc
            except OSError as exc:
                raise RunnerUnavailableError(f"cannot start codex exec: {exc}") from exc
            finally:
                self._active_process = None
            latency_ms = int((time.monotonic() - started) * 1000)
            stderr = _limited(stderr_bytes)
            if self._interrupted:
                raise RunnerInterruptedError(
                    "MCP runner disconnected during codex exec",
                    stderr=stderr,
                    exit_code=process.returncode,
                )
            if process.returncode != 0:
                raise RunnerExecutionError(
                    f"codex exec exited with {process.returncode}",
                    stderr=stderr,
                    exit_code=process.returncode,
                )
            try:
                raw = result_path.read_bytes()
            except FileNotFoundError as exc:
                raise RunnerExecutionError(
                    "codex exec did not write a structured result",
                    stderr=stderr,
                    exit_code=process.returncode,
                ) from exc
            if len(raw) > MAX_RESULT_BYTES:
                raise RunnerExecutionError(
                    "codex structured result exceeded size limit",
                    stderr=stderr,
                    exit_code=process.returncode,
                )
            try:
                parsed = cli_schema.model_validate_json(raw)
            except Exception as exc:
                raise RunnerExecutionError(
                    f"codex structured result failed validation: {exc}",
                    stderr=stderr,
                    exit_code=process.returncode,
                ) from exc
            return CodexResult(
                value=parsed.model_dump(mode="json"),
                latency_ms=latency_ms,
                exit_code=process.returncode,
                stderr_excerpt=stderr,
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
