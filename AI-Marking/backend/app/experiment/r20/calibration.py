"""Deterministic development-corpus calibration for r20 token limits."""

from __future__ import annotations

import hashlib
import json
import re
from fractions import Fraction
from math import ceil
from pathlib import Path

from app.experiment.r20.dataset import FrozenDataset, build_frozen_dataset
from app.experiment.r20.tokenization import (
    TOKENIZER_ENCODING_SHA256,
    TOKENIZER_NAME,
    TOKENIZER_VERSION,
    token_count,
)

CALIBRATION_PERCENTILE = 0.95
LEGACY_LIMITS = {
    "answer_copy": 12,
    "core": 20,
    "support": 20,
    "guidance": 28,
    "feedback": 80,
    "visible_snapshot": 300,
    "stored_snapshot": 420,
}


def legacy_word_count(text: str) -> int:
    """The exact pre-token migration word counter, retained only for calibration."""
    return len(re.findall(r"\b[\w]+(?:['’-][\w]+)*\b", text, flags=re.UNICODE))


def normative_template_texts(path: str | Path) -> list[str]:
    value = Path(path).read_text(encoding="utf-8")
    return re.findall(r"```text\n(.*?)\n```", value, flags=re.DOTALL)


def calibration_texts(frozen: FrozenDataset, template_path: str | Path) -> list[str]:
    texts = list(normative_template_texts(template_path))
    for row in frozen.records:
        if row.role != "development" or row.excluded_reason is not None:
            continue
        texts.extend(
            (
                row.question_text,
                row.reference_answer,
                row.student_answer,
                row.teacher_feedback,
            )
        )
    return sorted({text for text in texts if legacy_word_count(text) > 0})


def calibrate(frozen: FrozenDataset, template_path: str | Path) -> dict:
    texts = calibration_texts(frozen, template_path)
    ratios = sorted(
        Fraction(token_count(text), legacy_word_count(text)) for text in texts
    )
    if not ratios:
        raise ValueError("token calibration corpus is empty")
    rank = ceil(CALIBRATION_PERCENTILE * len(ratios)) - 1
    ratio = ratios[rank]
    corpus = "\n\0\n".join(texts).encode()
    limits = {name: ceil(value * ratio) for name, value in LEGACY_LIMITS.items()}
    return {
        "method": "nearest-rank p95 of token_count/legacy_word_count",
        "percentile": CALIBRATION_PERCENTILE,
        "corpus_text_count": len(texts),
        "corpus_sha256": hashlib.sha256(corpus).hexdigest(),
        "tokenizer": {
            "name": TOKENIZER_NAME,
            "tiktoken_version": TOKENIZER_VERSION,
            "encoding_sha256": TOKENIZER_ENCODING_SHA256,
        },
        "ratio": {"numerator": ratio.numerator, "denominator": ratio.denominator},
        "limits": limits,
    }


def calibration_from_archive(archive: str | Path, template_path: str | Path) -> dict:
    return calibrate(build_frozen_dataset(archive), template_path)


def canonical_json(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
