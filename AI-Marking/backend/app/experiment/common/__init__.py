"""Shared execution primitives for every experiment protocol.

The unified experiment core and the SAF memory-study worker both issue model
calls through the same isolated, account-backed Codex CLI runner and measure
text with the same frozen tokenizer.
"""

from app.experiment.common.codex_runner import (
    PROMPT_ENVELOPE_VERSION,
    CodexExecRunner,
    CodexResult,
    RunnerDriftError,
    RunnerExecutionError,
    RunnerInterruptedError,
    RunnerUnavailableError,
)
from app.experiment.common.output_schema import (
    SchemaNotExpressibleError,
    assert_strict_output_schema,
    strictify_output_schema,
)
from app.experiment.common.tokenization import (
    TOKENIZER_ENCODING_SHA256,
    TOKENIZER_NAME,
    TOKENIZER_VERSION,
    token_count,
    token_ids,
    tokenizer,
)

__all__ = [
    "PROMPT_ENVELOPE_VERSION",
    "TOKENIZER_ENCODING_SHA256",
    "TOKENIZER_NAME",
    "TOKENIZER_VERSION",
    "CodexExecRunner",
    "CodexResult",
    "RunnerDriftError",
    "RunnerExecutionError",
    "RunnerInterruptedError",
    "RunnerUnavailableError",
    "SchemaNotExpressibleError",
    "assert_strict_output_schema",
    "strictify_output_schema",
    "token_count",
    "token_ids",
    "tokenizer",
]
