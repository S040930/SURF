"""Streaming DREsS_CASE gate, pairing reconstruction, and fixed sampling.

The first pass retains hashes and short metadata only. A second pass materializes
prompt/essay text for selected rows, so the roughly 91 MB corpus is never loaded
into memory and restricted prose never enters a manifest or log.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from app.experiment.r23 import PROTOCOL_ID, SAMPLING_SEED
from app.experiment.r23.protocol import FORMAL_COUNTS, PILOT_COUNTS, RERUN_COUNTS

csv.field_size_limit(128 * 1024 * 1024)


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    dimension: str
    filename: str
    label_column: str
    expected_rows: int
    expected_per_level: int
    expected_sha256: str


DATASET_SPECS = (
    DatasetSpec(
        "content",
        "DREsS_CASE_content.tsv",
        "content",
        8_307,
        923,
        "cb3e082996955d256068068297d78bfaebd75f00569d6f6e68a3ad1f016d2feb",
    ),
    DatasetSpec(
        "organization",
        "DREsS_CASE_organization.tsv",
        "organization",
        31_086,
        3_454,
        "005c1ffcd47db62902ffdc520fb1f424a63a84e1ad9765b2ae6b3f37c781c69a",
    ),
    DatasetSpec(
        "language",
        "DREsS_CASE_language.tsv",
        "language",
        792,
        88,
        "5b81050c03a5ae678e4b3cd04563de481ae5aa4a233b817608a9dd0e89004406",
    ),
)
SPEC_BY_DIMENSION = {spec.dimension: spec for spec in DATASET_SPECS}
EXPECTED_COLUMNS = {
    spec.dimension: ("id", "prompt", "essay", spec.label_column)
    for spec in DATASET_SPECS
}
ORG_LEVEL_ROWS = 3_454
ORG_BASES = 1_727
TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


class DataGateError(RuntimeError):
    """The restricted source differs from the pre-registered protocol."""


@dataclass(slots=True)
class RowMeta:
    dimension: str
    source_row: int
    source_id_hash: str
    label_x2: int
    prompt_sha256: str
    input_sha256: str
    word_count: int
    derived_base_id: str | None = None
    corruption_repeat: int | None = None
    collision: bool = False


@dataclass(frozen=True, slots=True)
class SampleObservation:
    observation_key: str
    dimension: str
    source_row: int
    source_id_hash: str
    label_x2: int
    prompt_sha256: str
    input_sha256: str
    word_count: int
    derived_base_id: str | None
    corruption_repeat: int | None
    collision: bool
    rerun: bool
    prompt: str
    essay: str

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("prompt")
        value.pop("essay")
        return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def _label_x2(raw: str) -> int:
    try:
        value = float(raw)
    except ValueError as exc:
        raise DataGateError(f"invalid CASE label {raw!r}") from exc
    doubled = int(round(value * 2))
    if abs(value * 2 - doubled) > 1e-9 or doubled not in range(2, 11):
        raise DataGateError(f"CASE label is outside the nine-value grid: {raw!r}")
    return doubled


def _iter_rows(path: Path) -> Iterator[tuple[int, dict[str, str], tuple[str, ...]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = tuple(reader.fieldnames or ())
        for row_number, row in enumerate(reader, start=1):
            yield row_number, {key: value or "" for key, value in row.items()}, fields


def audit_dataset(
    path: Path, spec: DatasetSpec, *, enforce_hash: bool = True
) -> dict[str, Any]:
    if not path.is_file():
        raise DataGateError(f"missing restricted dataset: {path.name}")
    sha256 = _sha256_file(path)
    if enforce_hash and sha256 != spec.expected_sha256:
        raise DataGateError(
            f"SHA-256 mismatch for {path.name}: expected {spec.expected_sha256}, found {sha256}"
        )
    counts: Counter[int] = Counter()
    null_counts: Counter[str] = Counter()
    fields: tuple[str, ...] = ()
    rows = 0
    for rows, row, current_fields in _iter_rows(path):
        fields = current_fields
        if fields != EXPECTED_COLUMNS[spec.dimension]:
            raise DataGateError(
                f"field mismatch for {path.name}: expected {EXPECTED_COLUMNS[spec.dimension]}, found {fields}"
            )
        for key in fields:
            if row[key] == "":
                null_counts[key] += 1
        counts[_label_x2(row[spec.label_column])] += 1
    if rows != spec.expected_rows:
        raise DataGateError(
            f"row mismatch for {path.name}: expected {spec.expected_rows}, found {rows}"
        )
    expected_distribution = {value: spec.expected_per_level for value in range(2, 11)}
    if dict(sorted(counts.items())) != expected_distribution:
        raise DataGateError(f"label distribution mismatch for {path.name}")
    if null_counts:
        raise DataGateError(
            f"empty required fields in {path.name}: {dict(null_counts)}"
        )
    return {
        "dimension": spec.dimension,
        "filename": spec.filename,
        "sha256": sha256,
        "bytes": path.stat().st_size,
        "rows": rows,
        "fields": list(fields),
        "label_distribution_x2": {
            str(key): value for key, value in sorted(counts.items())
        },
        "null_counts": {},
        "status": "verified",
    }


def audit_data_root(root: Path, *, enforce_hash: bool = True) -> dict[str, Any]:
    datasets = [
        audit_dataset(root / spec.filename, spec, enforce_hash=enforce_hash)
        for spec in DATASET_SPECS
    ]
    readme = root / "README.docx"
    return {
        "protocol_id": PROTOCOL_ID,
        "ready": True,
        "root_display": root.name,
        "datasets": datasets,
        "license_readme_present": readme.is_file(),
        "operator_confirmation_required": True,
        "privacy": "restricted text stays in the local experiment database and runner input only",
    }


def _token_multiset_signature(prompt: str, essay: str) -> str:
    tokens = Counter(TOKEN_RE.findall(_normalize(essay)))
    return _canonical_sha(
        {
            "prompt": _normalize(prompt),
            "tokens": sorted(tokens.items()),
        }
    )


def read_metadata(root: Path, dimension: str) -> list[RowMeta]:
    """Read only hashes/lengths and validate the Organization pairing invariant."""
    spec = SPEC_BY_DIMENSION[dimension]
    rows: list[RowMeta] = []
    org_signatures: dict[str, set[str]] = defaultdict(set)
    org_inputs: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    score_five_inputs: dict[str, list[str]] = defaultdict(list)

    for source_row, row, fields in _iter_rows(root / spec.filename):
        if fields != EXPECTED_COLUMNS[dimension]:
            raise DataGateError(f"field mismatch while indexing {spec.filename}")
        prompt = _normalize(row["prompt"])
        essay = _normalize(row["essay"])
        label_x2 = _label_x2(row[spec.label_column])
        source_id_hash = _canonical_sha({"dimension": dimension, "id": row["id"]})
        prompt_sha = _canonical_sha(prompt)
        input_sha = _canonical_sha({"prompt": prompt, "essay": essay})
        meta = RowMeta(
            dimension=dimension,
            source_row=source_row,
            source_id_hash=source_id_hash,
            label_x2=label_x2,
            prompt_sha256=prompt_sha,
            input_sha256=input_sha,
            word_count=len(re.findall(r"\w+", essay, re.UNICODE)),
        )
        if dimension == "organization":
            level_index, within = divmod(source_row - 1, ORG_LEVEL_ROWS)
            repeat_index, base_index = divmod(within, ORG_BASES)
            expected_label = 2 + level_index
            if label_x2 != expected_label or repeat_index not in (0, 1):
                raise DataGateError(
                    "Organization row order does not match nine fixed level blocks"
                )
            base_id = f"org-{base_index + 1:04d}"
            meta.derived_base_id = base_id
            meta.corruption_repeat = repeat_index + 1
            org_signatures[base_id].add(_token_multiset_signature(prompt, essay))
            org_inputs[base_id][input_sha].add(label_x2)
            if label_x2 == 10:
                score_five_inputs[base_id].append(input_sha)
        rows.append(meta)

    if dimension == "organization":
        if len(org_signatures) != ORG_BASES:
            raise DataGateError(f"expected {ORG_BASES} Organization bases")
        if any(len(signatures) != 1 for signatures in org_signatures.values()):
            raise DataGateError("Organization token-multiset pairing validation failed")
        if any(
            len(values) != 2 or values[0] != values[1]
            for values in score_five_inputs.values()
        ):
            raise DataGateError("Organization score=5 duplicate originals do not agree")
        collision_bases = {
            base_id
            for base_id, inputs in org_inputs.items()
            if any(len(labels) > 1 for labels in inputs.values())
        }
        for meta in rows:
            meta.collision = bool(meta.derived_base_id in collision_bases)
    return rows


def _stable_key(namespace: str, value: str) -> str:
    return hashlib.sha256(
        f"{PROTOCOL_ID}|{SAMPLING_SEED}|{namespace}|{value}".encode("utf-8")
    ).hexdigest()


def _stratified_order(rows: Iterable[RowMeta], *, namespace: str) -> list[RowMeta]:
    strata: dict[str, list[RowMeta]] = defaultdict(list)
    for row in rows:
        strata[row.prompt_sha256].append(row)
    for prompt_sha, values in strata.items():
        values.sort(key=lambda item: _stable_key(namespace, item.source_id_hash))
    ordered_strata = sorted(strata, key=lambda value: _stable_key(namespace, value))
    result: list[RowMeta] = []
    offset = 0
    while True:
        added = False
        for stratum in ordered_strata:
            values = strata[stratum]
            if offset < len(values):
                result.append(values[offset])
                added = True
        if not added:
            return result
        offset += 1


def _select_by_level(
    rows: list[RowMeta],
    *,
    dimension: str,
    count: int,
    phase: str,
    excluded: set[str],
    allowed_prompts: set[str] | None = None,
) -> list[RowMeta]:
    selected: list[RowMeta] = []
    for label_x2 in range(2, 11):
        candidates = [
            row
            for row in rows
            if row.label_x2 == label_x2
            and row.source_id_hash not in excluded
            and (allowed_prompts is None or row.prompt_sha256 in allowed_prompts)
        ]
        ordered = _stratified_order(
            candidates, namespace=f"{phase}|{dimension}|{label_x2}"
        )
        if len(ordered) < count:
            raise DataGateError(
                f"insufficient {dimension} rows at label x2={label_x2}: {len(ordered)} < {count}"
            )
        selected.extend(ordered[:count])
    return selected


def _organization_bases(rows: list[RowMeta]) -> dict[str, list[RowMeta]]:
    grouped: dict[str, list[RowMeta]] = defaultdict(list)
    for row in rows:
        assert row.derived_base_id is not None
        grouped[row.derived_base_id].append(row)
    for values in grouped.values():
        values.sort(key=lambda row: (row.label_x2, row.corruption_repeat or 0))
    return grouped


def _select_org_bases(
    rows: list[RowMeta], *, count: int, phase: str, excluded: set[str]
) -> list[RowMeta]:
    grouped = _organization_bases(rows)
    bases = sorted(
        (base for base in grouped if base not in excluded),
        key=lambda base: _stable_key(f"{phase}|organization", base),
    )
    if len(bases) < count:
        raise DataGateError(f"insufficient Organization bases: {len(bases)} < {count}")
    selected: list[RowMeta] = []
    for base in bases[:count]:
        # Keep both independent corruptions at 1–4.5 and collapse duplicate originals at 5.
        selected.extend(
            row
            for row in grouped[base]
            if row.label_x2 < 10 or row.corruption_repeat == 1
        )
    return selected


def select_metadata(root: Path, kind: str) -> tuple[list[RowMeta], set[str]]:
    inventories = {
        dimension: read_metadata(root, dimension) for dimension in SPEC_BY_DIMENSION
    }
    content_prompts_by_level = {
        label: {
            row.prompt_sha256 for row in inventories["content"] if row.label_x2 == label
        }
        for label in range(2, 11)
    }
    common_content_prompts = set.intersection(*content_prompts_by_level.values())

    pilot_content = _select_by_level(
        inventories["content"],
        dimension="content",
        count=PILOT_COUNTS["content"],
        phase="pilot",
        excluded=set(),
        allowed_prompts=common_content_prompts,
    )
    pilot_language = _select_by_level(
        inventories["language"],
        dimension="language",
        count=PILOT_COUNTS["language"],
        phase="pilot",
        excluded=set(),
    )
    pilot_org = _select_org_bases(
        inventories["organization"],
        count=PILOT_COUNTS["organization"],
        phase="pilot",
        excluded=set(),
    )
    if kind == "pilot_run":
        return pilot_content + pilot_language + pilot_org, set()
    if kind != "formal":
        raise DataGateError(f"unknown r23 project kind: {kind}")

    excluded_rows = {row.source_id_hash for row in pilot_content + pilot_language}
    excluded_bases = {row.derived_base_id for row in pilot_org if row.derived_base_id}
    content = _select_by_level(
        inventories["content"],
        dimension="content",
        count=FORMAL_COUNTS["content"],
        phase="formal",
        excluded=excluded_rows,
        allowed_prompts=common_content_prompts,
    )
    language = _select_by_level(
        inventories["language"],
        dimension="language",
        count=FORMAL_COUNTS["language"],
        phase="formal",
        excluded=excluded_rows,
    )
    organization = _select_org_bases(
        inventories["organization"],
        count=FORMAL_COUNTS["organization"],
        phase="formal",
        excluded=excluded_bases,
    )
    selected = content + language + organization

    rerun_keys: set[str] = set()
    for dimension in ("content", "language"):
        dimension_rows = [row for row in selected if row.dimension == dimension]
        reruns = _select_by_level(
            dimension_rows,
            dimension=dimension,
            count=RERUN_COUNTS[dimension],
            phase="rerun",
            excluded=set(),
        )
        rerun_keys.update(row.source_id_hash for row in reruns)
    org_grouped = _organization_bases(organization)
    rerun_bases = sorted(
        org_grouped,
        key=lambda base: _stable_key("rerun|organization", base),
    )[: RERUN_COUNTS["organization"]]
    rerun_keys.update(
        row.source_id_hash for base in rerun_bases for row in org_grouped[base]
    )
    return selected, rerun_keys


def materialize_sample(root: Path, kind: str) -> list[SampleObservation]:
    selected, rerun_keys = select_metadata(root, kind)
    by_dimension_row = {(row.dimension, row.source_row): row for row in selected}
    bodies: dict[tuple[str, int], tuple[str, str]] = {}
    for dimension, spec in SPEC_BY_DIMENSION.items():
        wanted = {
            source_row
            for selected_dimension, source_row in by_dimension_row
            if selected_dimension == dimension
        }
        if not wanted:
            continue
        for source_row, row, _ in _iter_rows(root / spec.filename):
            if source_row in wanted:
                bodies[(dimension, source_row)] = (
                    _normalize(row["prompt"]),
                    _normalize(row["essay"]),
                )
            if len(bodies) == len(by_dimension_row):
                break
    if len(bodies) != len(by_dimension_row):
        raise DataGateError(
            "selected rows changed between metadata and materialization passes"
        )

    observations: list[SampleObservation] = []
    for row in selected:
        prompt, essay = bodies[(row.dimension, row.source_row)]
        if _canonical_sha({"prompt": prompt, "essay": essay}) != row.input_sha256:
            raise DataGateError("selected input hash changed during materialization")
        key = _canonical_sha(
            {
                "dimension": row.dimension,
                "source_id_hash": row.source_id_hash,
                "label_x2": row.label_x2,
            }
        )
        observations.append(
            SampleObservation(
                observation_key=key,
                dimension=row.dimension,
                source_row=row.source_row,
                source_id_hash=row.source_id_hash,
                label_x2=row.label_x2,
                prompt_sha256=row.prompt_sha256,
                input_sha256=row.input_sha256,
                word_count=row.word_count,
                derived_base_id=row.derived_base_id,
                corruption_repeat=row.corruption_repeat,
                collision=row.collision,
                rerun=row.source_id_hash in rerun_keys,
                prompt=prompt,
                essay=essay,
            )
        )
    return observations


def public_manifest(
    *, kind: str, data_status: dict[str, Any], observations: list[SampleObservation]
) -> dict[str, Any]:
    dimension_counts = Counter(row.dimension for row in observations)
    rerun_counts = Counter(row.dimension for row in observations if row.rerun)
    unique_primary = len({row.input_sha256 for row in observations})
    unique_rerun = len({row.input_sha256 for row in observations if row.rerun})
    return {
        "protocol_id": PROTOCOL_ID,
        "kind": kind,
        "sampling_seed": SAMPLING_SEED,
        "data_hashes": {
            item["dimension"]: item["sha256"] for item in data_status["datasets"]
        },
        "observation_counts": dict(sorted(dimension_counts.items())),
        "rerun_counts": dict(sorted(rerun_counts.items())),
        "observation_slots_per_model": len(observations) + sum(rerun_counts.values()),
        "unique_logical_calls_per_model": unique_primary + unique_rerun,
        "model_count": 1,
        "observations": [row.public_dict() for row in observations],
    }


__all__ = [
    "DATASET_SPECS",
    "DataGateError",
    "DatasetSpec",
    "RowMeta",
    "SampleObservation",
    "audit_data_root",
    "audit_dataset",
    "materialize_sample",
    "public_manifest",
    "read_metadata",
    "select_metadata",
]
