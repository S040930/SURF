"""Registry of dataset adapters and research templates.

The unified core stays template-agnostic: the service asks the registry for
the adapter that audits/materializes a dataset and the template that owns the
sampling plan, runner requirements, and analysis.  Templates register at
import time from their own modules; nothing in this package imports a
concrete template, so adding one never touches the kernel.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.experiment.core.contracts import ScoringContract
from app.experiment.core.sampling import Selection


class RegistryError(KeyError):
    """Raised for unknown datasets/templates or duplicate registrations."""


@dataclass(frozen=True, slots=True)
class AuditItem:
    """One auditable input: hashes, labels, and word count only — never text."""

    key: str  # input_sha256
    prompt_sha256: str
    prompt_norm: str
    labels_x2: dict[str, int]
    word_count: int
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DatasetAudit:
    """Pass-1 result: a public audit report plus the metadata table."""

    dataset_key: str
    report: dict[str, Any]  # JSON-safe, contains no restricted text
    items: tuple[AuditItem, ...]


@dataclass(frozen=True, slots=True)
class MaterializedInput:
    """Pass-2 result: the full restricted content of one selected input."""

    key: str
    prompt: str
    essay: str
    labels_x2: dict[str, int]
    word_count: int
    provenance: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class DatasetAdapter(Protocol):
    """Streams a restricted dataset root into auditable, rebuildable inputs."""

    key: str
    name: str
    access_level: str
    license_note: str

    def audit(self, root: Path) -> DatasetAudit:
        """Verify file pins and build the metadata table (streaming pass 1)."""
        ...

    def materialize(
        self, root: Path, audit: DatasetAudit, keys: Collection[str]
    ) -> list[MaterializedInput]:
        """Re-read selected rows, re-verifying every hash (streaming pass 2)."""
        ...

    def contract(self) -> ScoringContract:
        """The scoring contract derived from the dataset definition."""
        ...


@dataclass(frozen=True, slots=True)
class GroupSpec:
    """One run group: a template-defined execution bucket, in frozen order."""

    group_key: str
    run_index: int


@dataclass(frozen=True, slots=True)
class SamplingPlan:
    """A template's complete, rebuildable sampling decision for one project.

    ``slots`` is empty for evaluation-level templates (one analysis row per
    call).  Observation-slot templates (whose unit of analysis is the dataset
    observation, with a many-to-one observation→evaluation mapping) supply one
    :class:`SlotSpec` per observation; ``retest_keys`` must then contain every
    input key that owns at least one rerun slot.
    """

    selections: tuple[Selection, ...]
    retest_keys: frozenset[str] = frozenset()
    slots: tuple["SlotSpec", ...] = ()


@dataclass(frozen=True, slots=True)
class SlotSpec:
    """One dataset observation slot attached to an input."""

    input_key: str
    slot_key: str
    channel: str
    label_x2: int
    rerun: bool = False
    provenance: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ResearchTemplate(Protocol):
    """A registered research design on top of one dataset adapter."""

    template_id: str
    name: str
    dataset_key: str
    runner_count: int
    require_runner_alignment: bool
    seed: str
    # Observation-slot templates analyse dataset observations (many slots can
    # share one evaluation); evaluation-level templates analyse calls directly.
    uses_observation_slots: bool

    def sampling_plan(
        self,
        *,
        kind: str,
        audit: DatasetAudit,
        excluded_keys: Collection[str] = (),
    ) -> SamplingPlan:
        """The template's fixed, rebuildable sampling plan for one project."""
        ...

    def run_group_specs(self, *, kind: str) -> list[GroupSpec]:
        """Run groups in frozen execution order for one binding."""
        ...

    def build_report(
        self,
        *,
        project: dict[str, Any],
        rows: Sequence[dict[str, Any]],
        retest_rows: Sequence[dict[str, Any]],
        attempts: Sequence[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        """Compute the analysis report and figure list from succeeded rows.

        ``rows`` are the primary (run_index 0) succeeded calls and
        ``retest_rows`` the independent retest calls; both carry labels,
        predictions, weights, and hashes but never restricted text.
        """
        ...


_DATASETS: dict[str, DatasetAdapter] = {}
_TEMPLATES: dict[str, ResearchTemplate] = {}


def register_dataset(adapter: DatasetAdapter) -> None:
    if adapter.key in _DATASETS:
        raise RegistryError(f"dataset already registered: {adapter.key}")
    _DATASETS[adapter.key] = adapter


def register_template(template: ResearchTemplate) -> None:
    if template.template_id in _TEMPLATES:
        raise RegistryError(f"template already registered: {template.template_id}")
    if template.dataset_key not in _DATASETS:
        raise RegistryError(
            f"template {template.template_id} references unregistered dataset "
            f"{template.dataset_key}"
        )
    _TEMPLATES[template.template_id] = template


def get_dataset(key: str) -> DatasetAdapter:
    if key not in _DATASETS:
        raise RegistryError(f"unknown dataset: {key}")
    return _DATASETS[key]


def get_template(template_id: str) -> ResearchTemplate:
    if template_id not in _TEMPLATES:
        raise RegistryError(f"unknown template: {template_id}")
    return _TEMPLATES[template_id]


def list_datasets() -> list[DatasetAdapter]:
    return [_DATASETS[key] for key in sorted(_DATASETS)]


def list_templates() -> list[ResearchTemplate]:
    return [_TEMPLATES[key] for key in sorted(_TEMPLATES)]


def load_plugins(registrars: Sequence[Callable[[], None]]) -> None:
    """Run plugin registrars exactly once each (idempotent re-imports)."""
    for registrar in registrars:
        registrar()


__all__ = [
    "AuditItem",
    "DatasetAdapter",
    "DatasetAudit",
    "GroupSpec",
    "MaterializedInput",
    "RegistryError",
    "ResearchTemplate",
    "SamplingPlan",
    "SlotSpec",
    "get_dataset",
    "get_template",
    "list_datasets",
    "list_templates",
    "load_plugins",
    "register_dataset",
    "register_template",
]
