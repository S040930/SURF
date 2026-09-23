"""The Codex CLI boundary: what actually reaches ``--output-schema``.

These tests run a stub executable in place of ``codex``, so the schema under
test is the one the runner wrote to disk and handed to the process -- not an
intermediate value.  That distinction is the whole point: the pilot run failed
because a document that looked fine in Python was rejected by the API after the
CLI had rewritten nothing, and no test ever looked at the file.
"""

import json
import os
from pathlib import Path

import pytest

from app.experiment.common.codex_runner import CodexExecRunner, RunnerExecutionError
from app.experiment.common.output_schema import (
    SchemaNotExpressibleError,
    assert_strict_output_schema,
)
from app.experiment.memory_study.protocol import ScoreOutput

RUNTIME = {
    "model": "gpt-5.6-luna",
    "reasoning_effort": "medium",
    "timeout_seconds": 30,
}

# Copies the schema it was handed to $SCHEMA_COPY, appends "$TOUCHED" when
# invoked, optionally fails with $STDERR_TEXT on stderr, and otherwise writes a
# valid structured result -- enough of the real CLI's contract to test the
# runner's two boundaries (what it sends, what it reports back).
STUB_CLI = """#!/bin/sh
schema=""
result=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output-schema) schema="$2"; shift 2 ;;
    --output-last-message) result="$2"; shift 2 ;;
    *) shift ;;
  esac
done
if [ -n "$TOUCHED" ]; then echo invoked >> "$TOUCHED"; fi
if [ -n "$SCHEMA_COPY" ]; then cp "$schema" "$SCHEMA_COPY"; fi
if [ -n "$STDERR_TEXT" ]; then printf '%s\\n' "$STDERR_TEXT" >&2; exit 1; fi
if [ -n "$RESULT_JSON" ]; then printf '%s' "$RESULT_JSON" > "$result"; else printf '%s' '{"memory": []}' > "$result"; fi
exit 0
"""

# The A-MEM analysis shape, verbatim: properties but no required and no
# additionalProperties.  This is what the API rejected during the pilot run.
ILLEGAL_FRAMEWORK_SCHEMA = {
    "type": "object",
    "properties": {
        "keywords": {"type": "array", "items": {"type": "string"}},
        "context": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}


@pytest.fixture
def stub_cli(tmp_path: Path, monkeypatch) -> Path:
    executable = tmp_path / "stub-codex"
    executable.write_text(STUB_CLI, encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("TOUCHED", str(tmp_path / "invoked.log"))
    return executable


def _touched(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_illegal_framework_schema_is_repaired_before_the_cli_sees_it(
    stub_cli: Path, tmp_path: Path, monkeypatch
):
    """The regression that cost the pilot run: an illegal document reaching the CLI."""
    copy = tmp_path / "schema.json"
    monkeypatch.setenv("SCHEMA_COPY", str(copy))

    result = CodexExecRunner(executable=str(stub_cli)).run(
        messages=({"role": "user", "content": "extract"},),
        runtime=RUNTIME,
        schema_json=ILLEGAL_FRAMEWORK_SCHEMA,
    )

    written = json.loads(copy.read_text(encoding="utf-8"))
    assert written != ILLEGAL_FRAMEWORK_SCHEMA, "the runner sent the illegal document"
    assert_strict_output_schema(written)
    assert written["required"] == ["keywords", "context", "tags"]
    assert result.value == {"memory": []}


def test_pydantic_schema_still_reaches_the_cli_unchanged(
    stub_cli: Path, tmp_path: Path, monkeypatch
):
    """The scoring path passes a Pydantic model and must not be disturbed."""
    copy = tmp_path / "schema.json"
    monkeypatch.setenv("SCHEMA_COPY", str(copy))
    monkeypatch.setenv(
        "RESULT_JSON", json.dumps({"score": 82, "feedback": "clear thesis"})
    )

    result = CodexExecRunner(executable=str(stub_cli)).run(
        messages=({"role": "user", "content": "score"},),
        runtime=RUNTIME,
        schema=ScoreOutput,
    )

    written = json.loads(copy.read_text(encoding="utf-8"))
    assert written == ScoreOutput.model_json_schema()
    # Without `schema_json` the result is validated against the Pydantic model.
    assert result.value == {"score": 82, "feedback": "clear thesis"}


def test_stderr_reaches_the_error_message(stub_cli: Path, monkeypatch):
    """`str(exc)` is what lands in the attempt row, so the cause must be in it."""
    monkeypatch.setenv(
        "STDERR_TEXT",
        '{"error":{"code":"invalid_json_schema","message":"additionalProperties '
        'is required to be supplied and to be false"}}',
    )

    with pytest.raises(RunnerExecutionError) as excinfo:
        CodexExecRunner(executable=str(stub_cli)).run(
            messages=({"role": "user", "content": "extract"},),
            runtime=RUNTIME,
            schema_json=ILLEGAL_FRAMEWORK_SCHEMA,
        )

    message = str(excinfo.value)
    assert "exited with 1" in message
    assert "invalid_json_schema" in message
    assert "additionalProperties" in message
    assert excinfo.value.exit_code == 1
    assert "invalid_json_schema" in excinfo.value.stderr


def test_inexpressible_schema_fails_before_the_cli_starts(
    stub_cli: Path, monkeypatch
):
    """A rejected schema is permanent, so it must not cost a model call."""
    touched = Path(os.environ["TOUCHED"])
    with pytest.raises(SchemaNotExpressibleError):
        CodexExecRunner(executable=str(stub_cli)).run(
            messages=({"role": "user", "content": "extract"},),
            runtime=RUNTIME,
            schema_json={"type": "object", "additionalProperties": True},
        )
    assert _touched(touched) == []


def test_a_run_needs_one_of_the_two_schema_sources(stub_cli: Path):
    with pytest.raises(ValueError, match="output schema"):
        CodexExecRunner(executable=str(stub_cli)).run(
            messages=({"role": "user", "content": "extract"},),
            runtime=RUNTIME,
        )


def test_diagnostic_keeps_the_tail_where_codex_reports_the_real_error():
    """codex v0.154+ opens stderr with a banner and echoes the whole prompt.

    The head is therefore noise; the actual failure line only survives if the
    excerpt keeps the tail.
    """
    from app.experiment.common.codex_runner import _diagnostic

    banner = "Reading additional input from stdin... OpenAI Codex v0.154.0 user " + "prompt text " * 150
    real_error = "stream error: unexpected status 500 from upstream"
    result = _diagnostic(banner + " " + real_error)
    assert result.startswith("...")
    assert result.endswith(real_error)
