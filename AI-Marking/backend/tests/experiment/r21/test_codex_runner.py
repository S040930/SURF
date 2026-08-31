import hashlib
import json
import threading
import time
from pathlib import Path

from app.experiment.r21.codex_runner import (
    CodexExecRunner,
    RunnerInterruptedError,
)
from app.experiment.r21.protocol import ScoreOutput


def _fake_codex(tmp_path: Path) -> Path:
    executable = tmp_path / "codex"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import sys
if '--version' in sys.argv:
    print('codex 99.0.0')
    raise SystemExit(0)
target = sys.argv[sys.argv.index('--output-last-message') + 1]
with open(target, 'w', encoding='utf-8') as handle:
    json.dump({'score': 1.0, 'feedback': 'valid'}, handle)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def test_codex_exec_uses_isolated_structured_arguments(tmp_path: Path):
    executable = _fake_codex(tmp_path)
    runner = CodexExecRunner(str(executable))
    runtime = runner.frozen_runtime(
        {"model": "frozen-model", "reasoning_effort": "medium", "timeout_seconds": 30}
    )
    result = runner.run(
        messages=(
            {"role": "system", "content": "score only"},
            {"role": "user", "content": "{}"},
        ),
        schema=ScoreOutput,
        runtime=runtime,
    )
    assert result.value == {"score": 1.0, "feedback": "valid"}
    assert runtime["sandbox"] == "read-only"
    assert runtime["ephemeral"] is True
    assert (
        runtime["executable_sha256"]
        == hashlib.sha256(executable.read_bytes()).hexdigest()
    )


def test_codex_exec_maps_fast_speed_mode_to_explicit_cli_config(tmp_path: Path):
    args_path = tmp_path / "args.json"
    executable = tmp_path / "codex"
    executable.write_text(
        f"""#!/usr/bin/env python3
import json
import sys
if '--version' in sys.argv:
    print('codex 99.0.0')
    raise SystemExit(0)
with open({str(args_path)!r}, 'w', encoding='utf-8') as handle:
    json.dump(sys.argv, handle)
target = sys.argv[sys.argv.index('--output-last-message') + 1]
with open(target, 'w', encoding='utf-8') as handle:
    json.dump({{'score': 1.0, 'feedback': 'valid'}}, handle)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    runner = CodexExecRunner(str(executable))
    runtime = runner.frozen_runtime(
        {
            "model": "frozen-model",
            "reasoning_effort": "medium",
            "speed_mode": "fast",
            "timeout_seconds": 30,
        }
    )

    runner.run(
        messages=({"role": "user", "content": "{}"},),
        schema=ScoreOutput,
        runtime=runtime,
    )

    arguments = json.loads(args_path.read_text(encoding="utf-8"))
    assert 'service_tier="fast"' in arguments
    assert "features.fast_mode=true" in arguments
    assert runtime["speed_mode"] == "fast"
    assert runtime["service_tier"] == "fast"


def test_disconnect_interrupts_active_codex_session(tmp_path: Path):
    executable = tmp_path / "codex"
    executable.write_text(
        """#!/usr/bin/env python3
import sys
import time
if '--version' in sys.argv:
    print('codex 99.0.0')
    raise SystemExit(0)
time.sleep(30)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    runner = CodexExecRunner(str(executable))
    runtime = runner.frozen_runtime(
        {"model": "frozen-model", "reasoning_effort": "medium", "timeout_seconds": 30}
    )
    errors: list[BaseException] = []

    def execute() -> None:
        try:
            runner.run(
                messages=({"role": "user", "content": "{}"},),
                schema=ScoreOutput,
                runtime=runtime,
            )
        except BaseException as exc:  # captured for the parent test thread
            errors.append(exc)

    thread = threading.Thread(target=execute)
    thread.start()
    deadline = time.monotonic() + 5
    while runner._active_process is None and time.monotonic() < deadline:
        time.sleep(0.01)
    runner.terminate_active()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], RunnerInterruptedError)
