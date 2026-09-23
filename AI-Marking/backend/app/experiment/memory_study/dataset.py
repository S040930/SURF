"""Independent SAF 2.0 cleaning and sampling for the memory study.

The implementation deliberately reads the pinned archive directly.  It does
not call the legacy r20 dataset builder or reuse its role map, split map,
trajectories, or hashes.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from random import Random
from typing import Iterable

from app.experiment.memory_study import PROTOCOL_ID, V3_R2_PROTOCOL_ID
from app.experiment.memory_study.protocol import (
    ARCHIVE_SHA256,
    DEVELOPMENT_QUESTION_COUNT,
    FORMAL_QUESTION_COUNT,
    FORMAL_TEST_COUNT,
    FORMAL_TRAIN_COUNT,
    LEGACY_ORDER_VARIANTS,
    ORDER_SHUFFLE_SEED,
    PILOT_QUESTION_COUNT,
    PILOT_TEST_COUNT,
    PILOT_TRAIN_COUNT,
    SAF_COMMIT,
    SELECTION_SEED,
    V3_R2_FORMAL_QUESTIONS,
    StudyKind,
    counts_for_kind,
    order_variants_for_kind,
    scoring_context_for_protocol,
)


@dataclass(frozen=True, slots=True)
class StudyRecord:
    answer_id: str
    group_id: str
    question_id: str
    question_text: str
    reference_answer: str
    student_answer: str
    teacher_score: float
    teacher_feedback: str
    verification_feedback: str
    source_split: str
    source_path: str
    source_position: int
    excluded_reason: str | None = None
    selection_kind: str | None = None


@dataclass(frozen=True, slots=True)
class FrozenStudyDataset:
    records: tuple[StudyRecord, ...]
    selected_questions: tuple[str, ...]
    roles: dict[str, str]
    orders: dict[str, dict[str, list[str]]]
    max_scores: dict[str, float]
    score_ceilings: dict[str, float]
    score_floors: dict[str, float]
    manifest: dict

    def records_for(self, question_id: str, selection_kind: str) -> list[StudyRecord]:
        return [
            row
            for row in self.records
            if row.question_id == question_id
            and row.selection_kind == selection_kind
            and row.excluded_reason is None
        ]

    def by_answer_id(self) -> dict[str, StudyRecord]:
        return {row.answer_id: row for row in self.records}


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = "".join(
        " " if unicodedata.category(char).startswith("P") else char for char in value
    )
    return re.sub(r"\s+", " ", value).strip()


def _xml_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def _parse_xml(raw: bytes, source_split: str, source_path: str) -> list[StudyRecord]:
    root = ET.fromstring(raw)
    question_id = root.attrib["id"]
    question_text = _xml_text(root.find("questionText"))
    references = list(root.find("referenceAnswers") or ())
    if len(references) != 1:
        raise ValueError(
            f"memory study requires one reference answer for {question_id}"
        )
    reference_answer = _xml_text(references[0])
    answers = root.find("studentAnswers")
    return [
        StudyRecord(
            answer_id=answer.attrib["id"],
            group_id=answer.attrib["id"].rsplit(".", 1)[-1],
            question_id=question_id,
            question_text=question_text,
            reference_answer=reference_answer,
            student_answer=_xml_text(answer.find("response")),
            teacher_score=float(_xml_text(answer.find("score"))),
            teacher_feedback=_xml_text(answer.find("response_feedback")),
            verification_feedback=_xml_text(answer.find("verification_feedback")),
            source_split=source_split,
            source_path=source_path,
            source_position=index,
        )
        for index, answer in enumerate(answers or ())
    ]


def _read_archive(
    path: Path,
) -> tuple[list[StudyRecord], dict[str, str], dict[str, int]]:
    rows: list[StudyRecord] = []
    file_hashes: dict[str, str] = {}
    raw_counts: dict[str, int] = defaultdict(int)
    with zipfile.ZipFile(path) as archive:
        prefixes = {
            "training": "SAF2_0/training/",
            "unseen_answers": "SAF2_0/unseen_answers/",
            "unseen_questions": "SAF2_0/unseen_questions/",
        }
        names = sorted(archive.namelist())
        for split, prefix in prefixes.items():
            for name in names:
                if not name.startswith(prefix) or not name.endswith(".xml"):
                    continue
                content = archive.read(name)
                file_hashes[name] = _sha(content)
                parsed = _parse_xml(content, split, name)
                rows.extend(parsed)
                raw_counts[split] += len(parsed)
    return rows, file_hashes, dict(raw_counts)


def _fingerprint(value: str) -> tuple[str, tuple[str, ...], frozenset[tuple[str, ...]]]:
    normalized = normalize_text(value)
    tokens = tuple(normalized.split())
    grams = frozenset(
        tuple(tokens[index : index + 5]) for index in range(len(tokens) - 4)
    )
    return normalized, tokens, grams


def _near_duplicate(left: tuple, right: tuple) -> bool:
    left_text, left_tokens, left_grams = left
    right_text, right_tokens, right_grams = right
    if left_text == right_text:
        return True
    if len(left_tokens) < 20 or len(right_tokens) < 20:
        return False
    if (
        min(len(left_tokens), len(right_tokens))
        / max(len(left_tokens), len(right_tokens))
        < 0.90
    ):
        return False
    jaccard = len(left_grams & right_grams) / max(1, len(left_grams | right_grams))
    return (
        jaccard >= 0.90 and SequenceMatcher(None, left_text, right_text).ratio() >= 0.95
    )


def _identity_fingerprint(value: str) -> str:
    """Conservative identity-leakage signal used only for train/test exclusion."""
    lowered = normalize_text(value)
    lowered = re.sub(r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", "<email>", lowered)
    lowered = re.sub(r"\b\d{7,}\b", "<number>", lowered)
    return lowered


def _clean(rows: list[StudyRecord]) -> tuple[list[StudyRecord], list[dict]]:
    excluded: dict[int, str] = {}
    audit: list[dict] = []
    by_question: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        by_question[row.question_id].append(index)

    for question_id, indexes in by_question.items():
        fingerprints = {
            index: _fingerprint(rows[index].student_answer) for index in indexes
        }
        identity = {
            index: _identity_fingerprint(rows[index].student_answer)
            for index in indexes
        }
        by_answer: dict[str, list[int]] = defaultdict(list)
        by_group: dict[str, list[int]] = defaultdict(list)
        by_identity: dict[str, list[int]] = defaultdict(list)
        for index in indexes:
            row = rows[index]
            by_answer[fingerprints[index][0]].append(index)
            by_group[row.group_id].append(index)
            by_identity[identity[index]].append(index)

        components: list[list[int]] = []
        seen: set[int] = set()
        for group in (*by_answer.values(), *by_group.values(), *by_identity.values()):
            if len(group) > 1:
                components.append(group)
                seen.update(group)
        for offset, left in enumerate(indexes):
            for right in indexes[offset + 1 :]:
                if right in seen and left in seen:
                    continue
                if _near_duplicate(fingerprints[left], fingerprints[right]):
                    components.append([left, right])

        # Collapse overlapping duplicate components without relying on the r20
        # union-find implementation; this is an intentionally independent pass.
        parent = {index: index for index in indexes}

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(left: int, right: int) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        for component in components:
            for index in component[1:]:
                union(component[0], index)
        grouped: dict[int, list[int]] = defaultdict(list)
        for index in indexes:
            grouped[find(index)].append(index)

        for component in grouped.values():
            if len(component) <= 1:
                continue
            splits = {rows[index].source_split for index in component}
            reason = (
                "cross_split_duplicate_or_identity_link"
                if {"training", "unseen_answers"}.issubset(splits)
                else "duplicate_or_near_duplicate"
            )
            if (
                reason.startswith("cross_split")
                or len({rows[index].teacher_score for index in component}) > 1
            ):
                targets = component
            else:
                by_split: dict[str, list[int]] = defaultdict(list)
                for index in component:
                    by_split[rows[index].source_split].append(index)
                targets = []
                for split_indexes in by_split.values():
                    ordered = sorted(
                        split_indexes,
                        key=lambda index: (
                            rows[index].source_path,
                            rows[index].source_position,
                            rows[index].answer_id,
                        ),
                    )
                    targets.extend(ordered[1:])
            for index in targets:
                excluded[index] = reason
            audit.append(
                {
                    "question_id": question_id,
                    "reason": reason,
                    "answer_ids": sorted(rows[index].answer_id for index in targets),
                    "splits": sorted(splits),
                }
            )

    return [
        replace(row, excluded_reason=excluded.get(index))
        for index, row in enumerate(rows)
    ], audit


def _hash_order(rows: Iterable[StudyRecord], seed: str) -> list[StudyRecord]:
    return sorted(
        rows,
        key=lambda row: hashlib.sha256(f"{seed}:{row.answer_id}".encode()).hexdigest(),
    )


def _source_order(rows: Iterable[StudyRecord]) -> list[StudyRecord]:
    """Return rows in the order in which they occur in the pinned archive."""
    return sorted(
        rows,
        key=lambda row: (row.source_path, row.source_position, row.answer_id),
    )


def _question_pools(
    rows: Iterable[StudyRecord],
) -> dict[str, dict[str, list[StudyRecord]]]:
    result: dict[str, dict[str, list[StudyRecord]]] = defaultdict(
        lambda: {"training": [], "unseen_answers": []}
    )
    for row in rows:
        if row.excluded_reason is None and row.source_split in {
            "training",
            "unseen_answers",
        }:
            result[row.question_id][row.source_split].append(row)
    return result


def _select_roles(
    pools: dict[str, dict[str, list[StudyRecord]]],
    *,
    fixed_formal_questions: tuple[str, ...] | None = None,
) -> dict[str, str]:
    eligible_formal = sorted(
        question
        for question, pool in pools.items()
        if len(pool["training"]) >= FORMAL_TRAIN_COUNT
        and len(pool["unseen_answers"]) >= FORMAL_TEST_COUNT
    )
    eligible_small = sorted(
        question
        for question, pool in pools.items()
        if len(pool["training"]) >= PILOT_TRAIN_COUNT
        and len(pool["unseen_answers"]) >= PILOT_TEST_COUNT
    )
    if fixed_formal_questions is None:
        formal = sorted(
            eligible_formal,
            key=lambda question: hashlib.sha256(
                f"{SELECTION_SEED}:formal:{question}".encode()
            ).hexdigest(),
        )[:FORMAL_QUESTION_COUNT]
    else:
        formal = list(fixed_formal_questions)
        missing = [question for question in formal if question not in pools]
        if missing:
            raise ValueError(
                "fixed formal SAF questions are missing after cleaning: "
                + ", ".join(missing)
            )
        ineligible = [
            question
            for question in formal
            if question not in eligible_formal
        ]
        if ineligible:
            raise ValueError(
                "fixed formal SAF questions cannot satisfy the 40/10 split: "
                + ", ".join(ineligible)
            )
    remaining = [question for question in eligible_small if question not in formal]
    remaining.sort(
        key=lambda question: hashlib.sha256(
            f"{SELECTION_SEED}:small:{question}".encode()
        ).hexdigest()
    )
    if (
        len(formal) < FORMAL_QUESTION_COUNT
        or len(remaining) < PILOT_QUESTION_COUNT + DEVELOPMENT_QUESTION_COUNT
    ):
        raise ValueError(
            "SAF 2.0 does not contain enough mutually exclusive clean questions "
            "for six formal, two development, and two pilot questions"
        )
    roles = {question: "formal" for question in formal}
    roles.update(
        {question: "development" for question in remaining[:DEVELOPMENT_QUESTION_COUNT]}
    )
    roles.update(
        {
            question: "pilot"
            for question in remaining[
                DEVELOPMENT_QUESTION_COUNT : DEVELOPMENT_QUESTION_COUNT
                + PILOT_QUESTION_COUNT
            ]
        }
    )
    return roles


def build_dataset(
    archive_path: str | Path,
    *,
    kind: str = StudyKind.formal,
    strict_archive_hash: bool = True,
    seed: str = SELECTION_SEED,
    protocol_id: str = PROTOCOL_ID,
) -> FrozenStudyDataset:
    """Build a new role map and sample plan from the official SAF archive."""
    path = Path(archive_path)
    raw = path.read_bytes()
    archive_sha256 = _sha(raw)
    if strict_archive_hash and archive_sha256 != ARCHIVE_SHA256:
        raise ValueError(
            "SAF 2.0 archive SHA-256 differs from the memory-study manifest"
        )
    rows, file_hashes, raw_counts = _read_archive(path)
    # The registered score grid is a property of the question's full historical
    # labels (training + unseen in the same question), computed from the raw
    # archive BEFORE cleaning so deduplication/cross-split exclusions cannot
    # shift the grid.  Grids are not uniformly 0-based (some start at 0.25),
    # so the range is recorded as (floor, ceiling).
    raw_grid: dict[str, tuple[float, float]] = {}
    raw_score_grids: dict[str, list[float]] = {}
    by_question_scores: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_question_scores[row.question_id].append(row.teacher_score)
    for question, scores in by_question_scores.items():
        raw_grid[question] = (min(scores), max(scores))
        raw_score_grids[question] = sorted(set(scores))
    clean_rows, exclusions = _clean(rows)
    pools = _question_pools(clean_rows)
    roles = _select_roles(
        pools,
        fixed_formal_questions=(
            V3_R2_FORMAL_QUESTIONS if protocol_id == V3_R2_PROTOCOL_ID else None
        ),
    )
    kind_questions = {
        "formal": [question for question, role in roles.items() if role == "formal"],
        "development": [
            question for question, role in roles.items() if role == "development"
        ],
        "pilot": [question for question, role in roles.items() if role == "pilot"],
    }
    if kind not in kind_questions:
        raise ValueError(f"unknown memory-study kind: {kind}")
    counts = counts_for_kind(kind)
    eligible_kind_questions = (
        [question for question in V3_R2_FORMAL_QUESTIONS if question in pools]
        if protocol_id == V3_R2_PROTOCOL_ID
        else kind_questions[kind]
    )
    selected = sorted(
        eligible_kind_questions,
        key=lambda question: hashlib.sha256(
            f"{seed}:{kind}:{question}".encode()
        ).hexdigest(),
    )[: counts["questions"]]
    orders: dict[str, dict[str, list[str]]] = {}
    max_scores: dict[str, float] = {}
    score_ceilings: dict[str, float] = {}
    score_floors: dict[str, float] = {}
    order_variants = order_variants_for_kind(kind, protocol_id)
    r2_order_algorithm = protocol_id == V3_R2_PROTOCOL_ID
    persisted_order_variants = (
        order_variants if r2_order_algorithm else LEGACY_ORDER_VARIANTS
    )
    selected_ids: set[str] = set()
    selected_records: set[tuple[str, str]] = set()
    for question in selected:
        pool = pools[question]
        sampled_training = _hash_order(
            pool["training"], f"{seed}:{kind}:training:{question}"
        )[
            : counts["training_per_question"]
        ]
        sampled_test = _hash_order(
            pool["unseen_answers"], f"{seed}:{kind}:test:{question}"
        )[
            : counts["test_per_question"]
        ]
        if (
            len(sampled_training) != counts["training_per_question"]
            or len(sampled_test) != counts["test_per_question"]
        ):
            raise ValueError(f"{question} cannot satisfy the fixed {kind} sample size")
        selected_ids.update(row.answer_id for row in sampled_training + sampled_test)
        selected_records.update(
            (row.answer_id, "training") for row in sampled_training
        )
        selected_records.update((row.answer_id, "test") for row in sampled_test)

        if r2_order_algorithm:
            training = _source_order(sampled_training)
            test = _source_order(sampled_test)
            shuffled_training = list(training)
            if "shuffled" in order_variants:
                Random(ORDER_SHUFFLE_SEED).shuffle(shuffled_training)
        else:
            training = sampled_training
            test = sampled_test
        orders[question] = {
            "training": [row.answer_id for row in training],
            "test": [row.answer_id for row in test],
        }
        if r2_order_algorithm:
            orders[question]["original"] = [row.answer_id for row in training]
            if "shuffled" in order_variants:
                orders[question]["shuffled"] = [
                    row.answer_id for row in shuffled_training
                ]
        else:
            for order_variant in persisted_order_variants:
                orders[question][order_variant] = [
                    row.answer_id
                    for row in _hash_order(
                        training, f"{seed}:{kind}:{order_variant}:{question}"
                    )
                ]
        # The training-observed ceiling remains a transparency/diagnostic value.
        max_scores[question] = max(row.teacher_score for row in training)
        # The registered normalization ceiling is the question's full scoring
        # grid (raw archive: training + unseen answers of that question),
        # fixed at dataset build and independent of cleaning/sampling.
        score_floor, score_ceiling = raw_grid[question]
        score_ceilings[question] = score_ceiling
        score_floors[question] = score_floor

    role_rows = []
    for row in clean_rows:
        if row.answer_id not in selected_ids:
            continue
        selection_kind = (
            "training" if (row.answer_id, "training") in selected_records else "test"
        )
        role_rows.append(replace(row, selection_kind=selection_kind))

    clean_counts = defaultdict(int)
    for row in clean_rows:
        if row.excluded_reason is None:
            clean_counts[row.source_split] += 1
    question_hashes = {
        question: hashlib.sha256(
            json.dumps(
                {
                    "question_id": question,
                    "question_text": next(
                        row.question_text
                        for row in clean_rows
                        if row.question_id == question
                    ),
                    "reference_answer": next(
                        row.reference_answer
                        for row in clean_rows
                        if row.question_id == question
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        for question in selected
    }
    question_selection = (
        "v3_r2_fixed_formal_questions"
        if protocol_id == V3_R2_PROTOCOL_ID
        else "v3_hash_selection"
    )
    order_algorithm = (
        (
            "dataset_source_order_plus_random.Random(seed=42)_per_question"
            if "shuffled" in order_variants
            else "dataset_source_order"
        )
        if r2_order_algorithm
        else "sha256 per order variant"
    )
    order_manifest = {
        "variants": list(order_variants),
        "algorithm": order_algorithm,
    }
    if r2_order_algorithm:
        order_manifest["shuffle_seed"] = ORDER_SHUFFLE_SEED
    protocol_fingerprint_payload = {
        "protocol_id": protocol_id,
        "question_selection": question_selection,
        "selected_questions": selected,
        "sample_counts": counts,
        "score_contract": {
            "continuous": True,
            "reject_out_of_bounds": True,
            "rounding": False,
            "clipping": False,
            "score_floors": score_floors,
            "score_ceilings": score_ceilings,
        },
        "scoring_instrument_sha256": scoring_context_for_protocol(protocol_id)[
            "instrument_sha256"
        ],
    }
    if r2_order_algorithm:
        protocol_fingerprint_payload["order"] = order_manifest
    else:
        # Keep the legacy fingerprint shape stable for historical V3 projects.
        protocol_fingerprint_payload["order_variants"] = list(
            LEGACY_ORDER_VARIANTS
        )
    protocol_fingerprint = _sha(
        json.dumps(
            protocol_fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    manifest_core = {
        "protocol": protocol_id,
        "seed": seed,
        "kind": kind,
        "saf_commit": SAF_COMMIT,
        "archive_sha256": archive_sha256,
        "raw_counts": dict(sorted(raw_counts.items())),
        "clean_counts": dict(sorted(clean_counts.items())),
        "roles": dict(sorted(roles.items())),
        "selected_questions": selected,
        "orders": orders,
        "question_selection": question_selection,
        "question_hashes": question_hashes,
        "protocol_fingerprint": protocol_fingerprint,
        "order_algorithm": order_algorithm,
        **(
            {
                "order_variants": list(order_variants),
                "order_shuffle_seed": ORDER_SHUFFLE_SEED,
            }
            if r2_order_algorithm
            else {}
        ),
        "max_scores_from_training": max_scores,
        "score_ceilings": score_ceilings,
        "score_floors": score_floors,
        "score_grids": {
            question: raw_score_grids[question] for question in selected
        },
        "score_range_rule": (
            "该题全部历史评分（原始归档：训练+unseen）的最小/最大值，"
            "清洗前确定、评分开始前冻结。多数网格 0 起，部分从 0.25 起；"
            "模型 prompt、评分门禁与报告归一化使用同一上限，floor 供网格透明对照。"
        ),
        "exclusions": exclusions,
        "cleaning": {
            "normalization": "NFKC+casefold+punctuation-to-space+whitespace-collapse",
            "near_duplicate": "sequence>=0.95 and 5gram-jaccard>=0.90 for >=20 tokens",
            "cross_split_policy": "exclude entire duplicate or identity-linked component",
            "source_boundary": "training vs same-question unseen_answers",
        },
        "source_file_sha256": file_hashes,
        "selection_counts": {
            "questions": len(selected),
            "training_per_question": counts["training_per_question"],
            "test_per_question": counts["test_per_question"],
        },
    }
    manifest_hash = _sha(
        json.dumps(manifest_core, ensure_ascii=False, sort_keys=True).encode()
    )
    manifest = {**manifest_core, "manifest_sha256": manifest_hash}
    return FrozenStudyDataset(
        records=tuple(role_rows),
        selected_questions=tuple(selected),
        roles={question: roles[question] for question in selected},
        orders=orders,
        max_scores=max_scores,
        score_ceilings=score_ceilings,
        score_floors=score_floors,
        manifest=manifest,
    )


def audit_summary(dataset: FrozenStudyDataset) -> dict:
    """Return an API-safe audit summary without student answers."""
    by_question: dict[str, dict[str, int]] = defaultdict(
        lambda: {"training": 0, "test": 0}
    )
    for row in dataset.records:
        if row.selection_kind in {"training", "test"}:
            by_question[row.question_id][row.selection_kind] += 1
    return {
        "protocol": dataset.manifest["protocol"],
        "kind": dataset.manifest["kind"],
        "archive_sha256": dataset.manifest["archive_sha256"],
        "manifest_sha256": dataset.manifest["manifest_sha256"],
        "protocol_fingerprint": dataset.manifest["protocol_fingerprint"],
        "selected_questions": list(dataset.selected_questions),
        "question_hashes": dataset.manifest["question_hashes"],
        "roles": dataset.roles,
        "counts": {
            question: values for question, values in sorted(by_question.items())
        },
        "clean_counts": dataset.manifest["clean_counts"],
        "excluded_components": len(dataset.manifest["exclusions"]),
        "max_scores": dataset.max_scores,
        "score_ceilings": dataset.score_ceilings,
        "score_floors": dataset.score_floors,
    }


def public_record(row: StudyRecord) -> dict:
    """Serialize a row for restricted local persistence, not public export."""
    return asdict(row)


__all__ = [
    "FrozenStudyDataset",
    "StudyRecord",
    "audit_summary",
    "build_dataset",
    "normalize_text",
    "public_record",
]
