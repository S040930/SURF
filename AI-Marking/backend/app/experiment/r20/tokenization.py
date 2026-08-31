"""The frozen, model-independent tokenizer used by the r20 protocol."""

from __future__ import annotations

from functools import lru_cache

import tiktoken

TOKENIZER_NAME = "o200k_base"
TOKENIZER_VERSION = "0.13.0"
# This is the SHA-256 expected by tiktoken for the official encoding file.
TOKENIZER_ENCODING_SHA256 = (
    "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
)


@lru_cache(maxsize=1)
def tokenizer() -> tiktoken.Encoding:
    """Return the one frozen tokenizer; never infer one from a model name."""
    if tiktoken.__version__ != TOKENIZER_VERSION:
        raise RuntimeError(
            f"r20 requires tiktoken {TOKENIZER_VERSION}, got {tiktoken.__version__}"
        )
    return tiktoken.get_encoding(TOKENIZER_NAME)


def token_ids(text: str) -> tuple[int, ...]:
    """Encode literal text without treating special-token-looking text specially."""
    return tuple(tokenizer().encode_ordinary(text))


def token_count(text: str) -> int:
    return len(token_ids(text))
