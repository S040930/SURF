"""r22 adapter over the isolated Codex CLI runner."""

from __future__ import annotations

from app.experiment.r21.codex_runner import CodexExecRunner
from app.experiment.r22 import COMPRESSION_VERSION
from app.experiment.r22.protocol import PROMPT_ENVELOPE_VERSION


class R22CodexExecRunner(CodexExecRunner):
    """Use the r21 isolation guarantees with a distinct r22 envelope."""

    def runtime_fingerprint(self):
        runtime = super().runtime_fingerprint()
        runtime["prompt_envelope_version"] = PROMPT_ENVELOPE_VERSION
        runtime["compression_version"] = COMPRESSION_VERSION
        return runtime

    def run(self, *, messages, schema, runtime):
        """Run with the supplied transport schema.

        The r22 transport schemas already accept a structurally complete but
        token-overlong candidate (see :func:`inspect_candidate`), so the schema
        is passed through as-is.  Swapping in a permissive ``extra="allow"``
        envelope here made the API reject the request because OpenAI requires
        ``additionalProperties: false``.
        """
        return super().run(
            messages=messages,
            schema=schema,
            runtime=runtime,
        )


__all__ = ["R22CodexExecRunner"]
