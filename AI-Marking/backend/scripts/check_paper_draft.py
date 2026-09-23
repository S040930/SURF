"""Evidence-bound checks for a DREsS_New manuscript draft.

This checker deliberately separates mechanical traceability from semantic
review. A passing mechanical check proves only that declared numeric and
citation bindings replay against the current files; it does not certify the
interpretation or readiness for submission.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DIAG_DIR = ROOT / "outputs" / "exp_dress_new_example_diagnostic_0913"

# Every reported result belongs in the mapping file, rather than this list.
# Headings and bibliography metadata are excluded before scanning.  The only
# residual structural values here are citation years; reported percentages,
# counts, list indices, and statistical values must each be declared.
STRUCTURAL_TOKENS = {"2021", "2023", "2024", "2025", "2026", "2027"}
NUMBER_RE = re.compile(r"(?<![\w.\-])([+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?)(?![\w])")
CITATION_RE = re.compile(r"\[@([^\]]+)\]")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_value(value: Any, path: list[str]) -> Any:
    for part in path:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise KeyError(".".join(path))
    return value


def expected_display(value: Any, display: dict[str, Any]) -> str:
    multiplier = float(display.get("multiplier", 1))
    digits = int(display.get("decimal_places", 0))
    suffix = str(display.get("suffix", ""))
    rendered = f"{float(value) * multiplier:.{digits}f}"
    if display.get("trim_trailing_zeros"):
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered + suffix


def body_lines(draft: str) -> list[str]:
    """Keep appendices before the bibliography, excluding bibliography metadata."""
    return draft.split("## 参考文献", 1)[0].splitlines()


def numeric_tokens(text: str) -> list[str]:
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\[@[^\]]+\]", " ", text)
    text = re.sub(r"\b(?:gpt-5\.6-(?:luna|terra)|codex-cli\s+\d+\.\d+\.\d+)\b", " ", text, flags=re.I)
    text = re.sub(r"\b\d+\.\d+\.\d+\b", " ", text)  # semantic-version metadata
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", text)
    # Normalise a thousands separator before extracting a numeric token so
    # 1,584 is checked as one experimental count, rather than 1 and 584.
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
    return [match.group(1) for match in NUMBER_RE.finditer(text.replace("−", "-"))]


def fact_index(facts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in facts.get("facts", [])}


def evidence_index(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in evidence.get("entries", [])}


def citation_keys(text: str) -> list[str]:
    keys: list[str] = []
    for match in CITATION_RE.finditer(text):
        for key in match.group(1).split(";"):
            key = key.strip().lstrip("@").strip()
            if re.fullmatch(r"[A-Za-z0-9]+", key):
                keys.append(key)
    return keys


def check_number_mapping(
    draft: str, facts: dict[str, Any], mapping: dict[str, Any], literature: dict[str, Any]
) -> dict[str, Any]:
    lines = body_lines(draft)
    facts_by_id = fact_index(facts)
    literature_by_id = evidence_index(literature)
    mapped_locations: set[tuple[int, str]] = set()
    mapping_counts: Counter[tuple[int, str]] = Counter()
    issues: list[dict[str, Any]] = []
    checked = 0
    categories: Counter[str] = Counter()

    for entry in mapping.get("entries", []):
        entry_id = entry.get("id", "<missing id>")
        category = entry.get("category")
        line_no = entry.get("line")
        token = str(entry.get("reported", ""))
        categories[str(category)] += 1
        if category not in {"experimental", "external", "structural"}:
            issues.append({"id": entry_id, "error": "invalid category"})
            continue
        if not isinstance(line_no, int) or not 1 <= line_no <= len(lines):
            issues.append({"id": entry_id, "error": "line outside manuscript body"})
            continue
        line = lines[line_no - 1]
        if token not in numeric_tokens(line):
            issues.append({"id": entry_id, "error": "reported token absent from declared line", "line": line_no})
            continue
        mapped_locations.add((line_no, token))
        mapping_counts[(line_no, token)] += 1
        checked += 1

        if category == "experimental":
            fact_id = entry.get("fact_id")
            if fact_id not in facts_by_id:
                issues.append({"id": entry_id, "error": "unknown fact_id", "fact_id": fact_id})
                continue
            fact = facts_by_id[fact_id]
            source = fact.get("source")
            source_sha = fact.get("source_sha256")
            if not source or not source_sha:
                issues.append({"id": entry_id, "error": "fact lacks source path or source hash"})
                continue
            source_path = ROOT / str(source)
            if not source_path.exists():
                issues.append({"id": entry_id, "error": "fact source is unavailable", "source": source})
                continue
            if sha256_file(source_path) != source_sha:
                issues.append({"id": entry_id, "error": "fact source hash changed", "source": source})
                continue
            try:
                value = find_value(fact["value"], entry.get("value_path", []))
                if isinstance(value, str):
                    expected = str(entry.get("expected_literal", ""))
                    if not expected or expected not in value:
                        raise ValueError("string fact lacks the declared expected_literal")
                else:
                    expected = expected_display(value, entry.get("display", {}))
            except (KeyError, TypeError, ValueError) as exc:
                issues.append({"id": entry_id, "error": f"cannot resolve fact: {exc}"})
                continue
            suffix = str(entry.get("display", {}).get("suffix", ""))
            expected_token = expected[:-len(suffix)] if suffix else expected
            # The regex deliberately extracts a bare numeric token.  Require
            # a declared unit/suffix in the source line separately so 10.83%
            # cannot pass when the mapping says 10.83 个百分点.
            if suffix and f"{token}{suffix}" not in line:
                issues.append({"id": entry_id, "error": "reported unit/suffix differs from fact", "expected_suffix": suffix})
            if token != expected_token:
                issues.append({"id": entry_id, "error": "reported value differs from fact", "expected": expected, "actual": token})
        elif category == "external":
            evidence_id = entry.get("evidence_id")
            evidence = literature_by_id.get(evidence_id)
            if not evidence:
                issues.append({"id": entry_id, "error": "unknown literature evidence", "evidence_id": evidence_id})
                continue
            if evidence.get("reading_level") == "abstract" and entry.get("absence_claim"):
                issues.append({"id": entry_id, "error": "abstract-only evidence cannot support an absence claim"})
            if entry.get("claim_scope") not in set(evidence.get("allowed_claim_scopes", [])):
                issues.append({"id": entry_id, "error": "claim scope is not allowed by evidence", "scope": entry.get("claim_scope")})

    scanned_tokens = []
    for line_no, line in enumerate(lines, start=1):
        if line.lstrip().startswith("#"):
            continue
        scanned_tokens.extend((line_no, token) for token in numeric_tokens(line))
    scanned_counts: Counter[tuple[int, str]] = Counter(scanned_tokens)
    exempt = [(line, token) for line, token in scanned_tokens if token in STRUCTURAL_TOKENS]
    unmapped: list[dict[str, Any]] = []
    for location, occurrence_count in scanned_counts.items():
        line, token = location
        if token in STRUCTURAL_TOKENS:
            continue
        for occurrence in range(mapping_counts[location] + 1, occurrence_count + 1):
            unmapped.append({"line": line, "token": token, "occurrence": occurrence})
    excess_mappings = [
        {"line": line, "token": token, "declared": count, "scanned": scanned_counts[(line, token)]}
        for (line, token), count in mapping_counts.items() if count > scanned_counts[(line, token)]
    ]
    if excess_mappings:
        issues.extend({"id": "<coverage>", "error": "more mappings than numeric occurrences", **item} for item in excess_mappings)
    return {
        "scanned_numeric_tokens": len(scanned_tokens),
        "mapped_occurrences": sum(min(count, scanned_counts[location]) for location, count in mapping_counts.items()),
        "mapping_entries_checked": checked,
        "exempt_structural_tokens": len(exempt),
        "unmapped_occurrences": unmapped,
        "unmapped_count": len(unmapped),
        "inconsistent_mappings": issues,
        "inconsistent_count": len(issues),
        "mapping_categories": dict(categories),
        "mechanical_pass": bool(checked) and not unmapped and not issues,
    }


def check_citations(draft: str) -> dict[str, Any]:
    before_refs, separator, refs = draft.partition("## 参考文献")
    in_text = set(citation_keys(before_refs))
    in_refs = citation_keys(refs) if separator else []
    ref_counts = Counter(in_refs)
    ref_keys = set(ref_counts)
    duplicates = sorted(key for key, count in ref_counts.items() if count > 1)
    return {
        "in_text_citations": sorted(in_text),
        "reference_entries": sorted(ref_keys),
        "uncited_references": sorted(ref_keys - in_text),
        "missing_reference_entries": sorted(in_text - ref_keys),
        "duplicate_reference_entries": duplicates,
        "mechanical_pass": bool(separator) and not (ref_keys - in_text) and not (in_text - ref_keys) and not duplicates,
    }


def check_privacy(texts: dict[str, str]) -> dict[str, Any]:
    def normalize(text: str) -> str:
        return "".join(ch for ch in text if not ch.isspace() and ch not in '\"\\')

    probes: list[str] = []
    for path in sorted((DIAG_DIR / "private_requests").glob("*.json")):
        entry = load_json(path)
        user = entry["messages"][1]["content"]
        for section in ("<prompt>", "<essay>"):
            try:
                content = user.split(section, 1)[1].split(f"</{section[1:-1]}>", 1)[0]
            except IndexError:
                continue
            normalized = normalize(content)
            if len(normalized) >= 60:
                probes.append(normalized[len(normalized) // 2: len(normalized) // 2 + 60])
    leaks = []
    for name, text in texts.items():
        flat = normalize(text)
        if any(probe in flat for probe in probes):
            leaks.append(name)
    return {
        "method": "320 private-request middle-substring probes; bounded scan, not proof of universal non-disclosure",
        "probes": len(probes), "files_scanned": list(texts), "leaks": leaks,
        "mechanical_pass": not leaks,
    }


def semantic_status(review: dict[str, Any] | None, draft_path: Path) -> dict[str, Any]:
    if review is None:
        return {"status": "not_recorded", "complete": False, "reason": "no human semantic-review receipt supplied"}
    expected = review.get("draft_sha256")
    complete = review.get("status") == "completed" and expected == sha256_file(draft_path) and bool(review.get("reviewer"))
    return {
        "status": "completed" if complete else "invalid", "complete": complete,
        "reviewer": review.get("reviewer"), "draft_sha256_matches": expected == sha256_file(draft_path),
        "scope": review.get("scope"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", type=Path, required=True)
    parser.add_argument("--html", type=Path)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--literature-evidence", type=Path, required=True)
    parser.add_argument("--semantic-review", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    draft = args.draft.read_text(encoding="utf-8")
    texts = {"draft": draft}
    if args.html and args.html.exists():
        texts["html"] = args.html.read_text(encoding="utf-8")
    facts, mapping, literature = load_json(args.facts), load_json(args.mapping), load_json(args.literature_evidence)
    review = load_json(args.semantic_review) if args.semantic_review and args.semantic_review.exists() else None
    numbers = check_number_mapping(draft, facts, mapping, literature)
    citations, privacy = check_citations(draft), check_privacy(texts)
    semantic = semantic_status(review, args.draft)
    mechanical_pass = numbers["mechanical_pass"] and citations["mechanical_pass"] and privacy["mechanical_pass"]
    report = {
        "check_version": "paper_draft_check_v3",
        "draft_sha256": sha256_file(args.draft), "facts_sha256": sha256_file(args.facts),
        "mapping_sha256": sha256_file(args.mapping), "literature_evidence_sha256": sha256_file(args.literature_evidence),
        "numbers": numbers, "citations": citations, "privacy": privacy, "semantic_review": semantic,
        "mechanical_pass": mechanical_pass,
        "overall_verdict": "complete" if mechanical_pass and semantic["complete"] else ("mechanical_pass_only" if mechanical_pass else "mechanical_fail"),
        "scope_note": "Complete means declared mappings and a named semantic-review receipt match current bytes; it does not certify submission readiness or scientific validity.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2)[:6000])
    sys.exit(0 if mechanical_pass else 1)


if __name__ == "__main__":
    main()
