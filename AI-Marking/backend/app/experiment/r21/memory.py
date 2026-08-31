"""r21 memory validation with Codex-calibrated, frozen token limits."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from app.experiment.r20.tokenization import token_count, token_ids
from app.experiment.r21.protocol import (
    ANSWER_COPY_TOKENS,
    MAX_CORE_TOKENS,
    MAX_GUIDANCE_TOKENS,
    MAX_STORED_TOKENS,
    MAX_SUPPORT_TOKENS,
    MAX_VISIBLE_TOKENS,
    ARMUpdateOutput,
    CRMUpdateOutput,
)

_GRADE_POLICY = re.compile(
    r"\b(?:full|partial|no)\s+credits?\b|"
    r"\b(?:high|low|passing|failing)\s+grades?\b|"
    r"\bpass(?:\s+or\s+|\s*/\s*)fail\b|"
    r"\b(?:maximum|minimum|max|min)\s+(?:scores?|grades?|points?|marks?|credits?)\b|"
    r"\bautomatic(?:ally)?\s+(?:correct|incorrect|pass|fail)\b|"
    r"\b(?:award|deduct)(?:ed|ing|ion|ions)?\b[^.\n]{0,32}"
    r"\b(?:scores?|points?|marks?|credits?)\b|"
    r"\b\d+(?:\.\d+)?\s*(?:/|out\s+of)\s*\d+(?:\.\d+)?\s*"
    r"(?:scores?|grades?|points?|marks?|credits?)\b|"
    r"\b\d+(?:\.\d+)?\s*(?:scores?|grades?|points?|marks?|credits?|%|percent(?:age)?)\b|"
    r"\b(?:scores?|grades?|points?|marks?|credits?)\s*"
    r"(?:of\s*)?(?:=|:|is\s+|at\s+)?\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)?\b",
    re.IGNORECASE,
)
_CRM_BROAD_DIMENSION = re.compile(
    r"\b(?:rubrics?|criteri(?:on|a)|broad(?:\s+\w+){0,2}\s+dimensions?|"
    r"assessment(?:\s+\w+){0,2}\s+dimensions?|"
    r"overall(?:\s+\w+){0,2}\s+(?:quality|correctness|performance)|"
    r"holistic\s+(?:quality|judg(?:e)?ment|assessment))\b",
    re.IGNORECASE,
)
_ARM_CONDITIONAL_RULE = re.compile(
    r"\b(?:if|when|whenever)\b|"
    r"\bfuture\s+(?:answers?|responses?)\b|"
    r"\bstudents?(?:'s)?\s+(?:answers?|responses?|wording|behaviou?r)\b|"
    r"\bresponse\s+behaviou?r\b",
    re.IGNORECASE,
)
_CONTENT_STOPWORDS = {
    "a",
    "an",
    "and",
    "answer",
    "are",
    "as",
    "at",
    "be",
    "because",
    "by",
    "correct",
    "criterion",
    "evidence",
    "for",
    "from",
    "future",
    "has",
    "have",
    "if",
    "in",
    "incorrect",
    "is",
    "it",
    "missing",
    "no",
    "not",
    "of",
    "or",
    "partial",
    "partially",
    "present",
    "response",
    "student",
    "sufficient",
    "that",
    "the",
    "this",
    "to",
    "with",
}


def _normal(text: str) -> list[str]:
    return re.findall(r"[\w]+", unicodedata.normalize("NFKC", text).casefold())


def _content_terms(question: str, reference_answer: str) -> set[str]:
    return {
        token
        for token in _normal(f"{question} {reference_answer}")
        if len(token) >= 3 and token not in _CONTENT_STOPWORDS
    }


def _contains_content(text: str, content_terms: set[str]) -> bool:
    """Match content words while tolerating ordinary English inflections.

    The question corpus contains singular nouns such as ``packet`` while
    otherwise valid rubric text commonly uses ``packets``.  Exact token
    intersection treated those forms as unrelated and rejected legitimate
    memory updates.  Keep the gate lexical and conservative: only compare a
    token with its simple plural/singular form, never arbitrary synonyms.
    """

    def forms(token: str) -> set[str]:
        values = {token}
        if len(token) > 3 and token.endswith("s"):
            values.add(token[:-1])
        if len(token) > 4 and token.endswith("ed"):
            stem = token[:-2]
            values.add(stem[:-1] if len(stem) > 3 and stem[-1] == stem[-2] else stem)
        if len(token) > 5 and token.endswith("ing"):
            stem = token[:-3]
            values.add(stem[:-1] if len(stem) > 3 and stem[-1] == stem[-2] else stem)
        elif len(token) > 3 and token.endswith("y"):
            values.add(token[:-1] + "ies")
        elif len(token) > 3 and token.endswith("ies"):
            values.add(token[:-3] + "y")
        return values

    text_forms = {form for token in _normal(text) for form in forms(token)}
    term_forms = {form for token in content_terms for form in forms(token)}
    return bool(text_forms & term_forms)


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _string_values(item)]
    return []


def _contains_answer_copy(text: str, answer: str) -> bool:
    needles, haystack = token_ids(" ".join(_normal(text))), token_ids(
        " ".join(_normal(answer))
    )
    if len(needles) < ANSWER_COPY_TOKENS or len(haystack) < ANSWER_COPY_TOKENS:
        return False
    answer_grams = {
        haystack[index : index + ANSWER_COPY_TOKENS]
        for index in range(len(haystack) - ANSWER_COPY_TOKENS + 1)
    }
    return any(
        needles[index : index + ANSWER_COPY_TOKENS] in answer_grams
        for index in range(len(needles) - ANSWER_COPY_TOKENS + 1)
    )


def visible_token_count(condition: str, items: list[dict]) -> int:
    keys = (
        ("condition", "effect", "importance", "guidance")
        if condition == "crm"
        else ("criterion", "importance", "anchors")
    )
    return sum(
        token_count(value)
        for item in items
        for key in keys
        for value in _string_values(item[key])
    )


def stored_token_count(items: list[dict]) -> int:
    return sum(token_count(value) for item in items for value in _string_values(item))


def _validate_items(
    condition: str,
    items: list[dict],
    answer: str,
    question: str,
    reference_answer: str,
) -> dict[str, int]:
    core = "condition" if condition == "crm" else "criterion"
    content_terms = _content_terms(question, reference_answer)
    if not content_terms:
        raise ValueError("question and reference answer contain no content terms")

    seen: set[str] = set()
    for item_index, item in enumerate(items, start=1):
        if token_count(item[core]) > MAX_CORE_TOKENS:
            raise ValueError(f"{core} exceeds {MAX_CORE_TOKENS} tokens")
        if condition == "crm":
            if not item["condition"].casefold().startswith("if a future answer"):
                raise ValueError('condition must begin with "If a future answer"')
            if _CRM_BROAD_DIMENSION.search(item["condition"]):
                raise ValueError("CRM condition must be an atomic local feature")
            detail_tokens = token_count(item["effect"]) + token_count(item["guidance"])
            if detail_tokens > MAX_GUIDANCE_TOKENS:
                raise ValueError(
                    f"effect and guidance exceed {MAX_GUIDANCE_TOKENS} tokens"
                )
            semantic_values = [item["condition"]]
        else:
            anchors = item["anchors"]
            if _ARM_CONDITIONAL_RULE.search(item["criterion"]):
                raise ValueError("ARM criterion must not be an IF rule")
            detail_tokens = sum(token_count(value) for value in anchors.values())
            if detail_tokens > MAX_GUIDANCE_TOKENS:
                raise ValueError(
                    f"ARM item {item_index} anchors use {detail_tokens} tokens; "
                    f"limit is {MAX_GUIDANCE_TOKENS}. Keep each anchor concise."
                )
            semantic_values = [item["criterion"], *anchors.values()]
        if any(
            not _contains_content(value, content_terms) for value in semantic_values
        ):
            raise ValueError("memory core and anchors must contain question content")
        if token_count(item["support"]) > MAX_SUPPORT_TOKENS:
            raise ValueError(f"support exceeds {MAX_SUPPORT_TOKENS} tokens")

        normalized = " ".join(_normal(item[core]))
        if normalized in seen:
            raise ValueError("memory contains a duplicate core item")
        seen.add(normalized)
        if any(
            _GRADE_POLICY.search(value) or _contains_answer_copy(value, answer)
            for value in _string_values(item)
        ):
            raise ValueError("memory contains a grade policy or answer copy")

    visible, stored = visible_token_count(condition, items), stored_token_count(items)
    if visible > MAX_VISIBLE_TOKENS:
        raise ValueError(f"visible memory exceeds {MAX_VISIBLE_TOKENS} tokens")
    if stored > MAX_STORED_TOKENS:
        raise ValueError(f"stored memory exceeds {MAX_STORED_TOKENS} tokens")
    return {
        "visible_token_count": visible,
        "stored_token_count": stored,
        "item_count": len(items),
    }


def parse_memory_update(
    condition: str,
    value: dict,
    answer: str,
    question: str,
    reference_answer: str,
) -> tuple[list[dict], dict[str, int]]:
    if condition == "crm":
        items = [
            item.model_dump(mode="json")
            for item in CRMUpdateOutput.model_validate(value).rules
        ]
    elif condition == "arm":
        items = [
            item.model_dump(mode="json")
            for item in ARMUpdateOutput.model_validate(value).rubric
        ]
    else:
        raise ValueError(f"unknown r21 memory condition: {condition}")
    return items, _validate_items(condition, items, answer, question, reference_answer)


def render_memory(condition: str, items: list[dict]) -> str:
    if not items:
        return "No learned memory is available for this question."
    keys = (
        ("condition", "effect", "importance", "guidance")
        if condition == "crm"
        else ("criterion", "importance", "anchors")
    )
    if condition not in {"crm", "arm"}:
        raise ValueError(f"memory cannot be rendered for condition {condition}")
    return json.dumps(
        [{key: item[key] for key in keys} for item in items],
        ensure_ascii=False,
        sort_keys=True,
    )
