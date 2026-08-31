"""r22 memory validation adapter.

The semantic rules remain the frozen r21 rules; r22 only changes how a
complete, overlong candidate is captured before those rules run.
"""

from app.experiment.r21 import memory as _frozen_memory
from app.experiment.r21.memory import (
    parse_memory_update,
    render_memory,
    stored_token_count,
    visible_token_count,
)


def validate_memory_semantics(
    condition: str,
    value: dict,
    answer: str,
    question: str,
    reference_answer: str,
) -> None:
    """Validate non-token memory rules before deciding to compress.

    The implementation deliberately reuses r21's frozen lexical rules while
    omitting only its token-budget checks.  This prevents a mixed semantic and
    token error from being misclassified as compression-recoverable.
    """
    items = value.get("rules" if condition == "crm" else "rubric", [])
    content_terms = _frozen_memory._content_terms(question, reference_answer)
    if not content_terms:
        raise ValueError("question and reference answer contain no content terms")
    core = "condition" if condition == "crm" else "criterion"
    seen: set[str] = set()
    for item in items:
        if condition == "crm":
            if not item["condition"].casefold().startswith("if a future answer"):
                raise ValueError('condition must begin with "If a future answer"')
            if _frozen_memory._CRM_BROAD_DIMENSION.search(item["condition"]):
                raise ValueError("CRM condition must be an atomic local feature")
            semantic_values = [item["condition"]]
        elif condition == "arm":
            if _frozen_memory._ARM_CONDITIONAL_RULE.search(item["criterion"]):
                raise ValueError("ARM criterion must not be an IF rule")
            semantic_values = [item["criterion"], *item["anchors"].values()]
        else:
            raise ValueError(f"unknown r22 memory condition: {condition}")
        if any(
            not _frozen_memory._contains_content(text, content_terms)
            for text in semantic_values
        ):
            raise ValueError("memory core and anchors must contain question content")
        normalized = " ".join(_frozen_memory._normal(item[core]))
        if normalized in seen:
            raise ValueError("memory contains a duplicate core item")
        seen.add(normalized)
        if any(
            _frozen_memory._GRADE_POLICY.search(text)
            or _frozen_memory._contains_answer_copy(text, answer)
            for text in _frozen_memory._string_values(item)
        ):
            raise ValueError("memory contains a grade policy or answer copy")

__all__ = [
    "parse_memory_update",
    "render_memory",
    "stored_token_count",
    "validate_memory_semantics",
    "visible_token_count",
]
