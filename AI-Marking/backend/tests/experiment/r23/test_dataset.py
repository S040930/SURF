import csv
from collections import Counter
from pathlib import Path

from app.experiment.r23.dataset import (
    DatasetSpec,
    audit_dataset,
    read_metadata,
    select_metadata,
)


def test_tsv_parser_preserves_embedded_essay_newlines(tmp_path: Path):
    path = tmp_path / "tiny.tsv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["id", "prompt", "essay", "content"])
        for index, label_x2 in enumerate(range(2, 11), start=1):
            writer.writerow([index, "Prompt", "First line\nSecond line", label_x2 / 2])
    spec = DatasetSpec("content", path.name, "content", 9, 1, "not-enforced")
    audit = audit_dataset(path, spec, enforce_hash=False)
    assert audit["rows"] == 9
    assert audit["null_counts"] == {}


def test_production_gate_pairing_and_sampling_are_exact_and_disjoint():
    root = Path("/Users/mac/Desktop/SURF/DREsS")
    organization = read_metadata(root, "organization")
    grouped = Counter(row.derived_base_id for row in organization)
    assert len(grouped) == 1_727
    assert set(grouped.values()) == {18}
    assert len({row.derived_base_id for row in organization if row.collision}) == 122

    pilot, _ = select_metadata(root, "pilot_run")
    formal, reruns = select_metadata(root, "formal")
    assert Counter(row.dimension for row in pilot) == {
        "content": 45,
        "language": 45,
        "organization": 85,
    }
    assert Counter(row.dimension for row in formal) == {
        "content": 540,
        "language": 747,
        "organization": 1_020,
    }
    assert len(reruns) == 228
    assert not {row.source_id_hash for row in pilot} & {
        row.source_id_hash for row in formal
    }
    formal_again, reruns_again = select_metadata(root, "formal")
    assert [row.source_id_hash for row in formal] == [
        row.source_id_hash for row in formal_again
    ]
    assert reruns == reruns_again
