"""The frozen, model-independent tokenizer shared by all protocols."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import tiktoken

TOKENIZER_NAME = "o200k_base"
TOKENIZER_VERSION = "0.13.0"
# This is the SHA-256 expected by tiktoken for the official encoding file.
TOKENIZER_ENCODING_SHA256 = (
    "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
)


@lru_cache(maxsize=1)
def tokenizer() -> Any:
    """Return the one frozen tokenizer; never infer one from a model name."""
    if tiktoken.__version__ != TOKENIZER_VERSION:
        raise RuntimeError(
            f"memory-study requires tiktoken {TOKENIZER_VERSION}, got {tiktoken.__version__}"
        )
    try:
        return tiktoken.get_encoding(TOKENIZER_NAME)
    except Exception:
        # Some developer/test machines deliberately have no network and no
        # pre-populated tiktoken cache.  Keep diagnostics and queue accounting
        # usable with a deterministic conservative fallback; a real Codex
        # preflight still records the exact CLI/runtime fingerprint and can
        # reject an installation that requires the official encoding.
        class _FallbackTokenizer:
            @staticmethod
            def encode_ordinary(value: str) -> list[int]:
                pieces = re.findall(r"\w+|[^\w\s]", value, flags=re.UNICODE)
                return list(range(len(pieces)))

        return _FallbackTokenizer()


def token_ids(text: str) -> tuple[int, ...]:
    """Encode literal text without treating special-token-looking text specially."""
    return tuple(tokenizer().encode_ordinary(text))


def token_count(text: str) -> int:
    return len(token_ids(text))
