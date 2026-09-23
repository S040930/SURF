"""Unit coverage for the evidence-bound manuscript checker."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_paper_draft as checker  # noqa: E402


def _facts() -> dict:
    source = Path(checker.__file__)
    digest = checker.sha256_file(source)
    return {"facts": [
        {"id": "qwk", "value": 0.27, "source": str(source.relative_to(checker.ROOT)), "source_sha256": digest},
        {"id": "delta", "value": -0.1083333333, "source": str(source.relative_to(checker.ROOT)), "source_sha256": digest},
    ]}


def _literature() -> dict:
    return {"entries": [{"id": "source", "reading_level": "full_text", "allowed_claim_scopes": ["design"]}]}


def _map(entries: list[dict]) -> dict:
    return {"entries": entries}


def test_number_mapping_accepts_explicit_fact_binding() -> None:
    draft = "结果为 0.27。\n\n## 参考文献\n[@x] X\n"
    result = checker.check_number_mapping(draft, _facts(), _map([{
        "id": "qwk", "category": "experimental", "line": 1, "reported": "0.27",
        "fact_id": "qwk", "display": {"decimal_places": 2},
    }]), _literature())
    assert result["mechanical_pass"]
    assert result["mapped_occurrences"] == 1


def test_number_mapping_rejects_swapped_model_value() -> None:
    draft = "结果为 0.27。\n"
    result = checker.check_number_mapping(draft, _facts(), _map([{
        "id": "wrong", "category": "experimental", "line": 1, "reported": "0.27",
        "fact_id": "delta", "display": {"decimal_places": 2},
    }]), _literature())
    assert not result["mechanical_pass"]
    assert result["inconsistent_count"] == 1


def test_number_mapping_rejects_unmapped_appendix_value() -> None:
    draft = "正文为 0.27。\n\n## 附录\n调用数为 40。\n"
    result = checker.check_number_mapping(draft, _facts(), _map([{
        "id": "qwk", "category": "experimental", "line": 1, "reported": "0.27",
        "fact_id": "qwk", "display": {"decimal_places": 2},
    }]), _literature())
    assert not result["mechanical_pass"]
    assert {item["token"] for item in result["unmapped_occurrences"]} == {"40"}


def test_number_mapping_rejects_20_as_40_and_sign_reversal() -> None:
    draft = "每条件有 40 次，差值为 0.11。\n"
    facts = _facts()
    facts["facts"].extend([
        {"id": "calls", "value": 20, "source": facts["facts"][0]["source"], "source_sha256": facts["facts"][0]["source_sha256"]},
    ])
    result = checker.check_number_mapping(draft, facts, _map([
        {"id": "calls", "category": "experimental", "line": 1, "reported": "40", "fact_id": "calls", "display": {"decimal_places": 0}},
        {"id": "sign", "category": "experimental", "line": 1, "reported": "0.11", "fact_id": "delta", "display": {"decimal_places": 2}},
    ]), _literature())
    assert result["inconsistent_count"] == 2


def test_number_mapping_rejects_percent_vs_percentage_point_confusion() -> None:
    draft = "变化为 -10.83%。\n"
    result = checker.check_number_mapping(draft, _facts(), _map([{
        "id": "unit", "category": "experimental", "line": 1, "reported": "-10.83",
        "fact_id": "delta", "display": {"multiplier": 100, "decimal_places": 2, "suffix": "个百分点"},
    }]), _literature())
    # The numeric token can match, but the required unit must be adjacent.
    assert not result["mechanical_pass"]


def test_number_mapping_rejects_missing_or_changed_fact_source() -> None:
    facts = _facts()
    facts["facts"][0].pop("source_sha256")
    result = checker.check_number_mapping("结果为 0.27。\n", facts, _map([{
        "id": "qwk", "category": "experimental", "line": 1, "reported": "0.27",
        "fact_id": "qwk", "display": {"decimal_places": 2},
    }]), _literature())
    assert any("source" in item["error"] for item in result["inconsistent_mappings"])
    changed = _facts()
    changed["facts"][0]["source_sha256"] = "0" * 64
    result = checker.check_number_mapping("结果为 0.27。\n", changed, _map([{
        "id": "qwk", "category": "experimental", "line": 1, "reported": "0.27",
        "fact_id": "qwk", "display": {"decimal_places": 2},
    }]), _literature())
    assert any("hash changed" in item["error"] for item in result["inconsistent_mappings"])


def test_abstract_only_source_cannot_support_absence_claim() -> None:
    draft = "文献包含 67 篇。\n"
    literature = {"entries": [{"id": "abstract", "reading_level": "abstract", "allowed_claim_scopes": ["design"]}]}
    result = checker.check_number_mapping(draft, _facts(), _map([{
        "id": "external", "category": "external", "line": 1, "reported": "67",
        "evidence_id": "abstract", "claim_scope": "design", "absence_claim": True,
    }]), literature)
    assert result["inconsistent_count"] == 1


def test_citation_check_does_not_count_reference_entry_as_citation() -> None:
    result = checker.check_citations("正文。\n\n## 参考文献\n[@x] X\n")
    assert result["uncited_references"] == ["x"]
    assert not result["mechanical_pass"]


def test_citation_check_handles_compound_citation_and_duplicate_reference() -> None:
    good = "正文 [@x; @y]。\n\n## 参考文献\n[@x] X\n[@y] Y\n"
    assert checker.check_citations(good)["mechanical_pass"]
    duplicated = good + "[@y] duplicate\n"
    assert checker.check_citations(duplicated)["duplicate_reference_entries"] == ["y"]
