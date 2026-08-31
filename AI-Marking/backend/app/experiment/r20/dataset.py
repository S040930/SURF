"""Streaming, split-aware SAF 2.0 freeze builder for r20.

The builder reads only the pinned official SAF 2.0 archive.  It reconstructs
stable answer/group identifiers from XML, derives the Hugging Face validation
boundary with the published seed, applies leakage exclusions jointly, and then
allocates deterministic question roles and memory trajectories.
"""

from __future__ import annotations

import csv
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

from app.experiment.r20 import PROTOCOL_ID
from app.experiment.r20.protocol import (
    FORMAL_SCHEDULE,
    PILOT_SCHEDULE,
    PROBE_TEST_COUNT,
    TRAJECTORIES,
)

SAF_COMMIT = "09949e912d266777a752ac7d51cfcddb4b3f6978"
SAF_ARCHIVE_SHA256 = "c0841b36acdbe4adff9d96ede366fbe52de5671d859642783266204b4c9f5ec0"
ROLE_SEED = "saf-r20-role-v1"
HF_SPLIT_SEED = 1
HF_SPLIT_MAP_SHA256 = "16dd229ec06d43436a00e2de27d373d3b0780045ab36c39885c19d45df91c68f"
EXPECTED_RAW_COUNTS = {
    "original_training": 2127,
    "train": 1700,
    "validation": 427,
    "test_unseen_answers": 375,
    "test_unseen_questions": 479,
}
FORMAL_QUESTIONS = (
    "1.6",
    "2.4",
    "5.11",
    "6.3",
    "4.3",
    "4.1_LM_v1.0",
    "6.3_IPP",
    "8.1_MM",
)
PILOT_QUESTIONS = ("5.7", "4.13")
DEVELOPMENT_QUESTIONS = ("5.12", "4.3_LM")
ROLE_QUESTIONS = {
    **{question: "formal" for question in FORMAL_QUESTIONS},
    **{question: "pilot_run" for question in PILOT_QUESTIONS},
    **{question: "development" for question in DEVELOPMENT_QUESTIONS},
}


@dataclass(frozen=True, slots=True)
class SAFRecord:
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
    role: str | None = None
    excluded_reason: str | None = None


@dataclass(frozen=True, slots=True)
class FrozenDataset:
    records: tuple[SAFRecord, ...]
    roles: dict[str, str]
    trajectories: dict[str, dict[str, list[str]]]
    test_endpoints: dict[str, list[str]]
    probes: dict[str, list[str]]
    max_scores: dict[str, float]
    manifest: dict

    def records_for(self, question_id: str, source_split: str) -> list[SAFRecord]:
        return [
            row
            for row in self.records
            if row.question_id == question_id
            and row.source_split == source_split
            and row.excluded_reason is None
        ]


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_answer(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = "".join(
        " " if unicodedata.category(ch).startswith("P") else ch for ch in value
    )
    return re.sub(r"\s+", " ", value).strip()


def _xml_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def near_duplicate(left: str, right: str) -> bool:
    return _near_duplicate_fingerprints(_fingerprint(left), _fingerprint(right))


def _fingerprint(value: str) -> tuple[str, tuple[str, ...], frozenset[tuple[str, ...]]]:
    normalized = normalize_answer(value)
    tokens = tuple(normalized.split())
    grams = frozenset(tuple(tokens[i : i + 5]) for i in range(len(tokens) - 4))
    return normalized, tokens, grams


def _near_duplicate_fingerprints(left, right) -> bool:
    a, ta, ga = left
    b, tb, gb = right
    if a == b:
        return True
    if len(ta) < 20 or len(tb) < 20:
        return False
    if min(len(ta), len(tb)) / max(len(ta), len(tb)) < 0.90:
        return False
    jaccard = len(ga & gb) / max(1, len(ga | gb))
    if jaccard < 0.90:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.95 and jaccard >= 0.90


def _parse_xml(raw: bytes, source_split: str, source_path: str) -> list[SAFRecord]:
    root = ET.fromstring(raw)
    question_id = root.attrib["id"]
    question_text = _xml_text(root.find("questionText"))
    reference_node = root.find("referenceAnswers")
    references = list(reference_node) if reference_node is not None else []
    if len(references) != 1:
        raise ValueError(f"r20 requires one reference answer for {question_id}")
    reference = _xml_text(references[0])
    rows = []
    answers_node = root.find("studentAnswers")
    for index, answer in enumerate(answers_node if answers_node is not None else ()):
        answer_id = answer.attrib["id"]
        rows.append(
            SAFRecord(
                answer_id=answer_id,
                group_id=answer_id.rsplit(".", 1)[-1],
                question_id=question_id,
                question_text=question_text,
                reference_answer=reference,
                student_answer=_xml_text(answer.find("response")),
                teacher_score=float(_xml_text(answer.find("score"))),
                teacher_feedback=_xml_text(answer.find("response_feedback")),
                verification_feedback=_xml_text(answer.find("verification_feedback")),
                source_split=source_split,
                source_path=source_path,
                source_position=index,
            )
        )
    return rows


def _archive_rows(raw: bytes) -> tuple[list[SAFRecord], dict[str, str]]:
    split_dirs = {
        "original_training": "SAF2_0/training/",
        "test_unseen_answers": "SAF2_0/unseen_answers/",
        "test_unseen_questions": "SAF2_0/unseen_questions/",
    }
    rows: list[SAFRecord] = []
    file_hashes: dict[str, str] = {}
    with zipfile.ZipFile(PathArchive(raw)) as archive:
        names = sorted(archive.namelist())
        for source_split, prefix in split_dirs.items():
            for name in names:
                if name.startswith(prefix) and name.endswith(".xml"):
                    content = archive.read(name)
                    file_hashes[name] = _sha(content)
                    rows.extend(_parse_xml(content, source_split, name))
    counts = defaultdict(int)
    for row in rows:
        counts[row.source_split] += 1
    for split, expected in (
        ("original_training", EXPECTED_RAW_COUNTS["original_training"]),
        ("test_unseen_answers", EXPECTED_RAW_COUNTS["test_unseen_answers"]),
        ("test_unseen_questions", EXPECTED_RAW_COUNTS["test_unseen_questions"]),
    ):
        if counts[split] != expected:
            raise ValueError(f"SAF {split} count {counts[split]} != {expected}")
    return rows, file_hashes


class PathArchive:
    """Minimal seekable bytes wrapper accepted by ZipFile without extra copies."""

    def __init__(self, value: bytes):
        import io

        self._stream = io.BytesIO(value)

    def read(self, *args):
        return self._stream.read(*args)

    def seek(self, *args):
        return self._stream.seek(*args)

    def tell(self):
        return self._stream.tell()

    def seekable(self):
        return True


def _official_train_validation(
    rows: list[SAFRecord], split_map_path: str | Path
) -> list[SAFRecord]:
    raw = Path(split_map_path).read_bytes()
    if _sha(raw) != HF_SPLIT_MAP_SHA256:
        raise ValueError("SAF Hugging Face split map SHA-256 differs from manifest")
    split_map = {
        row["answer_id"]: row["source_split"]
        for row in csv.DictReader(raw.decode("utf-8").splitlines())
    }
    if len(split_map) != 2083 or set(split_map.values()) != {"train", "validation"}:
        raise ValueError("SAF Hugging Face split map shape differs from manifest")
    split_rows = [
        (
            replace(
                row,
                source_split=split_map.get(row.answer_id, "ambiguous_hf_split"),
                excluded_reason=(
                    None if row.answer_id in split_map else "ambiguous_hf_tuple_mapping"
                ),
            )
            if row.source_split == "original_training"
            else row
        )
        for row in rows
    ]
    counts = defaultdict(int)
    for row in split_rows:
        counts[row.source_split] += 1
    if (
        counts["train"] != 1665
        or counts["validation"] != 418
        or counts["ambiguous_hf_split"] != 44
    ):
        raise ValueError("official SAF train/validation reconstruction failed")
    return split_rows


def _components(rows: list[SAFRecord]) -> list[list[int]]:
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        a, b = find(a), find(b)
        if a != b:
            parent[b] = a

    by_question = defaultdict(list)
    for index, row in enumerate(rows):
        if row.source_split != "test_unseen_questions":
            by_question[row.question_id].append(index)
    for indexes in by_question.values():
        fingerprints = {
            index: _fingerprint(rows[index].student_answer) for index in indexes
        }
        by_normalized = defaultdict(list)
        by_group = defaultdict(list)
        for index in indexes:
            row = rows[index]
            by_normalized[normalize_answer(row.student_answer)].append(index)
            by_group[row.group_id].append(index)
        for group in (*by_normalized.values(), *by_group.values()):
            for index in group[1:]:
                union(group[0], index)
        # Near duplicates are bounded per question and keep the pipeline below 1 GB.
        for offset, left in enumerate(indexes):
            for right in indexes[offset + 1 :]:
                if find(left) != find(right) and _near_duplicate_fingerprints(
                    fingerprints[left], fingerprints[right]
                ):
                    union(left, right)
    grouped = defaultdict(list)
    for index in range(len(rows)):
        grouped[find(index)].append(index)
    return list(grouped.values())


def _exclude(rows: list[SAFRecord]) -> tuple[list[SAFRecord], list[dict]]:
    excluded: dict[int, str] = {
        index: row.excluded_reason
        for index, row in enumerate(rows)
        if row.excluded_reason is not None
    }
    log: list[dict] = []
    for component in _components(rows):
        active = [
            i for i in component if rows[i].source_split != "test_unseen_questions"
        ]
        if not active:
            continue
        splits = {rows[i].source_split for i in active}
        crosses_test = "test_unseen_answers" in splits and bool(
            splits & {"train", "validation"}
        )
        scores = {rows[i].teacher_score for i in active}
        feedback = {normalize_answer(rows[i].teacher_feedback) for i in active}
        answers = {normalize_answer(rows[i].student_answer) for i in active}
        qgroups = {(rows[i].question_id, rows[i].group_id) for i in active}
        if crosses_test:
            reason = "cross_split_duplicate_or_question_group"
            targets = active
        elif len(scores) > 1 or (len(answers) == 1 and len(feedback) > 1):
            reason = "conflicting_duplicate_component"
            targets = active
        elif len(qgroups) < len(active) and len(answers) > 1:
            reason = "multiple_answers_for_question_group"
            targets = active
        elif len(answers) == 1 and len(active) > 1:
            # Same-label duplicates within one split keep the earliest source row.
            reason = "same_label_duplicate"
            targets = []
            by_split = defaultdict(list)
            for index in active:
                by_split[rows[index].source_split].append(index)
            for split_indexes in by_split.values():
                ordered = sorted(
                    split_indexes,
                    key=lambda i: (
                        rows[i].source_path,
                        rows[i].source_position,
                        rows[i].answer_id,
                    ),
                )
                targets.extend(ordered[1:])
            if not targets:
                continue
        else:
            continue
        for index in targets:
            excluded[index] = reason
        log.append(
            {
                "reason": reason,
                "answer_ids": sorted(rows[i].answer_id for i in targets),
                "splits": sorted(splits),
            }
        )
    clean = [
        replace(row, excluded_reason=excluded.get(index))
        for index, row in enumerate(rows)
    ]
    return clean, log


def _role_map(rows: list[SAFRecord]) -> dict[str, str]:
    seen = sorted(
        {
            row.question_id
            for row in rows
            if row.source_split in {"train", "validation", "test_unseen_answers"}
        }
    )
    unseen = {
        row.question_id for row in rows if row.source_split == "test_unseen_questions"
    }
    if len(seen) != 26 or len(unseen) != 5 or set(seen) & unseen:
        raise ValueError("SAF seen/unseen question boundary differs from manifest")
    if set(ROLE_QUESTIONS) - set(seen):
        missing = sorted(set(ROLE_QUESTIONS) - set(seen))
        raise ValueError(f"v4 selected question is not in the seen boundary: {missing}")
    roles = {question: ROLE_QUESTIONS.get(question, "excluded") for question in seen}
    for question in unseen:
        roles[question] = "sealed_unseen_question"
    return roles


def _hash_order(rows: list[SAFRecord], seed: str) -> list[SAFRecord]:
    return sorted(
        rows,
        key=lambda row: hashlib.sha256(f"{seed}:{row.answer_id}".encode()).hexdigest(),
    )


def build_frozen_dataset(
    path: str | Path,
    split_map_path: str | Path | None = None,
    *,
    pilot_schedule=None,
    manifest_protocol: str | None = None,
) -> FrozenDataset:
    raw = Path(path).read_bytes()
    archive_hash = _sha(raw)
    if archive_hash != SAF_ARCHIVE_SHA256:
        raise ValueError("SAF 2.0 archive SHA-256 does not match the r20 manifest")
    parsed, file_hashes = _archive_rows(raw)
    split_map_path = split_map_path or Path(path).with_name("saf_hf_split_map.csv")
    split_rows = _official_train_validation(parsed, split_map_path)
    clean, exclusions = _exclude(split_rows)
    roles = _role_map(clean)
    clean = [replace(row, role=roles[row.question_id]) for row in clean]
    trajectories: dict[str, dict[str, list[str]]] = {}
    test_endpoints: dict[str, list[str]] = {}
    probes: dict[str, list[str]] = {}
    max_scores: dict[str, float] = {}
    for question, role in roles.items():
        if role not in {"formal", "pilot_run"}:
            continue
        schedule = FORMAL_SCHEDULE if role == "formal" else (pilot_schedule or PILOT_SCHEDULE)
        memory_pool = [
            row
            for row in clean
            if row.question_id == question
            and row.source_split in {"train", "validation"}
            and row.excluded_reason is None
        ]
        test = [
            row
            for row in clean
            if row.question_id == question
            and row.source_split == "test_unseen_answers"
            and row.excluded_reason is None
        ]
        if len(memory_pool) < schedule.memory_count:
            raise ValueError(
                f"{question} has {len(memory_pool)} clean train+validation answers; "
                f"v4 requires {schedule.memory_count}"
            )
        if len(test) < schedule.test_count:
            raise ValueError(
                f"{question} has {len(test)} clean test endpoints; "
                f"v4 requires {schedule.test_count}"
            )
        selected_memory = _hash_order(memory_pool, f"r20-v4-memory-pool-{question}")[
            : schedule.memory_count
        ]
        selected_tests = _hash_order(test, f"r20-v4-test-pool-{question}")[
            : schedule.test_count
        ]
        trajectories[question] = {
            str(trajectory): [
                row.answer_id
                for row in _hash_order(
                    selected_memory, f"r20-v4-trajectory-{trajectory}-{question}"
                )
            ]
            for trajectory in TRAJECTORIES
        }
        test_endpoints[question] = [row.answer_id for row in selected_tests]
        probes[question] = [row.answer_id for row in selected_tests[:PROBE_TEST_COUNT]]
        # The scoring ceiling is learned from the selected memory pool only; no
        # endpoint label is consulted to construct a scoring request.
        max_scores[question] = max(row.teacher_score for row in selected_memory)
    counts = defaultdict(int)
    clean_counts = defaultdict(int)
    for row in clean:
        counts[row.source_split] += 1
        if row.excluded_reason is None:
            clean_counts[row.source_split] += 1
    manifest_core = {
        "protocol": manifest_protocol or PROTOCOL_ID,
        "saf_commit": SAF_COMMIT,
        "archive_sha256": archive_hash,
        "hf_split_map_sha256": HF_SPLIT_MAP_SHA256,
        "source_file_sha256": file_hashes,
        "raw_counts": {
            "train": EXPECTED_RAW_COUNTS["train"],
            "validation": EXPECTED_RAW_COUNTS["validation"],
            "test_unseen_answers": EXPECTED_RAW_COUNTS["test_unseen_answers"],
            "test_unseen_questions": EXPECTED_RAW_COUNTS["test_unseen_questions"],
        },
        "mapped_counts": dict(sorted(counts.items())),
        "clean_counts": dict(sorted(clean_counts.items())),
        "roles": dict(sorted(roles.items())),
        "trajectories": trajectories,
        "test_endpoints": test_endpoints,
        "probes": probes,
        "max_scores": max_scores,
        "exclusions": exclusions,
        "cleaning": {
            "normalization": "NFKC+casefold+punctuation-to-space+whitespace-collapse",
            "near_duplicate": "sequence>=0.95 and 5gram-jaccard>=0.90 for >=20 tokens",
            "cross_split_policy": "exclude_entire_component",
        },
    }
    manifest_hash = _sha(
        json.dumps(manifest_core, ensure_ascii=False, sort_keys=True).encode()
    )
    manifest = {**manifest_core, "manifest_sha256": manifest_hash}
    return FrozenDataset(
        records=tuple(clean),
        roles=roles,
        trajectories=trajectories,
        test_endpoints=test_endpoints,
        probes=probes,
        max_scores=max_scores,
        manifest=manifest,
    )


def public_record(row: SAFRecord) -> dict:
    """Serialize a row for persistence; never use this object as a scoring payload."""
    return asdict(row)
