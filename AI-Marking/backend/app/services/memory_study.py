"""Domain service for the independent SAF memory-framework study."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now_naive
from app.experiment.common.codex_runner import (
    CodexExecRunner,
    RunnerExecutionError,
    RunnerInterruptedError,
    RunnerUnavailableError,
)
from app.experiment.memory_study.analysis import compute_report
from app.experiment.memory_study.dataset import (
    FrozenStudyDataset,
    StudyRecord,
    audit_summary,
    build_dataset,
)
from app.experiment.memory_study.manager import memory_study_worker_manager
from app.experiment.memory_study.memory import (
    CaseRetrievalAdapter,
    _redact_secrets,
    _redact_text,
    _sha256_path,
    case_payload,
    memory_hash,
)
from app.experiment.memory_study.protocol import (
    AMEM_COMMIT,
    ARCHIVE_SHA256,
    BASE_CONDITIONS,
    DEFAULT_PROTOCOL_ID,
    EMBEDDING_MODEL,
    INITIAL_TIMEOUT_SECONDS,
    MAX_CODEX_SUBPROCESSES,
    MEM0_COMMIT,
    MODEL_IDS,
    OPTIONAL_NO_FEEDBACK_CONDITIONS,
    PROTOCOL_ID,
    REASONING_EFFORT,
    SPEED_MODE,
    SUPPORTED_PROTOCOL_IDS,
    V3_R2_PROTOCOL_ID,
    PreflightOutput,
    StudyKind,
    StudyStatus,
    conditions_for_kind,
    deterministic_memory_conditions_for_protocol,
    expected_call_counts,
    framework_conditions_for_protocol,
    memory_profile_for_protocol,
    memory_write_conditions_for_protocol,
    order_variants_for_condition,
    order_variants_for_kind,
    repeats_for_kind,
    retrieval_top_k_for_protocol,
    scoring_context_for_protocol,
)
from app.experiment.memory_study.worker import (
    STALL_THRESHOLD_SECONDS,
    recompute_store_progress,
)
from app.models.memory_study import (
    MSAuditEvent,
    MSCall,
    MSCallAttempt,
    MSFrameworkInvocation,
    MSMemoryStore,
    MSQuestionRun,
    MSRecord,
    MSReport,
    MSRunnerConfig,
    MSSchedulerRuntime,
    MSSiteConfig,
    MSStudy,
)

RUNNER_SPEED_MODES = ("standard", "fast")
BLOCKED_EMBEDDING_REVISIONS = {"", "main", "latest", "unresolved"}
EMBEDDING_BACKENDS = ("openai",)
PREFLIGHT_FINGERPRINT_KEYS = (
    "executable_path",
    "executable_sha256",
    "cli_version",
    "sandbox",
    "ephemeral",
    "ignore_user_config",
    "ignore_rules",
    "output_schema",
    "prompt_envelope_version",
)
FROZEN_RUNTIME_KEYS = (
    "model",
    "reasoning_effort",
    "speed_mode",
    "timeout_seconds",
)


class MemoryStudyDomainError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


PREFLIGHT_PROBE = "memory-study-preflight-v1"


def _preflight_is_transient_message(message: str) -> bool:
    """Recognise transport failures without retaining the raw prompt/output."""
    return bool(re.search(r"\b5\d{2}\b", message)) or any(
        marker in message
        for marker in (
            "429",
            "rate_limit",
            "rate limit",
            "too many requests",
            "temporarily unavailable",
            "service unavailable",
            "connection reset",
            "connection refused",
            "connection aborted",
            "connection lost",
            "disconnected",
            "reset by peer",
            "network",
            "unreachable",
            "temporarily",
            "timed out",
            "timeout",
            "network error",
            "dns",
            "broken pipe",
            "eof",
        )
    )


def _preflight_failure_code(exc: Exception) -> str:
    """Map local preflight failures without leaking implementation details."""
    message = str(exc).lower()
    if any(
        marker in message
        for marker in (
            "auth",
            "login",
            "logged in",
            "unauthor",
            "credentials",
            "permission denied",
            "forbidden",
            "401",
            "403",
        )
    ):
        return "authentication_error"
    if "schema" in message:
        return "schema_error"
    if "model" in message and any(
        marker in message for marker in ("invalid", "unknown", "unsupported", "config")
    ):
        return "model_configuration_error"
    if isinstance(exc, RunnerUnavailableError):
        return "runner_unavailable"
    if isinstance(exc, RunnerInterruptedError):
        return "runner_interrupted"
    if _preflight_transient_exception(exc):
        return "runner_transient"
    if isinstance(exc, RunnerExecutionError):
        return "runner_execution_error"
    if isinstance(exc, ValueError):
        return "invalid_preflight_output"
    return "preflight_error"


def _preflight_transient_exception(exc: Exception) -> bool:
    """Inspect common HTTP-client status attributes without adding a dependency."""
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        try:
            status = int(candidate)
        except (TypeError, ValueError):
            continue
        if status in {408, 429} or 500 <= status <= 599:
            return True
    return isinstance(
        exc, (TimeoutError, ConnectionError)
    ) or _preflight_is_transient_message(str(exc).lower())


def _preflight_summary(exc: Exception) -> str:
    text = _redact_text(str(exc).strip(), 2_000)
    return text if text else exc.__class__.__name__


def _frozen_runtime(runner: object, runtime: dict) -> dict:
    """Return runner fingerprint plus the frozen model parameters.

    The production runner exposes ``frozen_runtime``.  The small fallback is
    useful for local harnesses and keeps the preflight contract about the
    evidence, not about one concrete runner implementation.
    """
    method = getattr(runner, "frozen_runtime", None)
    if callable(method):
        value = method(dict(runtime))
        if not isinstance(value, dict):
            raise RuntimeError("Codex runner returned an invalid frozen runtime")
        return dict(value)
    fingerprint = getattr(runner, "runtime_fingerprint", None)
    if not callable(fingerprint):
        raise RuntimeError("Codex runner cannot provide a runtime fingerprint")
    value = fingerprint()
    if not isinstance(value, dict):
        raise RuntimeError("Codex runner returned an invalid runtime fingerprint")
    combined = dict(runtime)
    combined.update(value)
    return combined


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _condition_spec(condition: str) -> tuple[str, str]:
    if condition == "no_memory":
        return "none", "none"
    if condition.startswith("retrieval"):
        return "retrieval", "full" if condition.endswith("full") else "no_feedback"
    if condition.startswith("mem0"):
        return "mem0", "full" if condition.endswith("full") else "no_feedback"
    if condition.startswith("amem"):
        return "amem", "full" if condition.endswith("full") else "no_feedback"
    raise ValueError(f"unknown memory-study condition: {condition}")


def _fixed_config(
    kind: str,
    speed_modes: dict[str, str] | None = None,
    feedback_waves: list[str] | tuple[str, ...] | None = None,
    protocol_id: str = DEFAULT_PROTOCOL_ID,
) -> dict:
    order_variants = order_variants_for_kind(kind, protocol_id)
    repeats = repeats_for_kind(kind, protocol_id)
    bindings = speed_modes or {}
    waves = tuple(dict.fromkeys(feedback_waves or ("full",)))
    if kind == StudyKind.formal:
        configured_conditions = BASE_CONDITIONS + tuple(
            condition
            for condition in OPTIONAL_NO_FEEDBACK_CONDITIONS
            if "no_feedback" in waves
        )
    else:
        configured_conditions = conditions_for_kind(kind, protocol_id)
    config = {
        "models": [
            {
                "model": model,
                "reasoning_effort": REASONING_EFFORT,
                "speed_mode": bindings.get(model, SPEED_MODE),
                "timeout_seconds": INITIAL_TIMEOUT_SECONDS,
            }
            for model in MODEL_IDS
        ],
        "conditions": list(configured_conditions),
        "optional_conditions": list(
            ("retrieval_no_feedback", "mem0_no_feedback", "amem_no_feedback")
            if kind == StudyKind.formal and "no_feedback" in waves
            else ()
        ),
        "order_variants": list(order_variants),
        "repeats": list(repeats),
        "retrieval_top_k": retrieval_top_k_for_protocol(protocol_id),
        "retrieval_threshold": 0,
        "memory_profile": memory_profile_for_protocol(protocol_id),
        "framework_conditions": list(framework_conditions_for_protocol(protocol_id)),
        "embedding_backend": "openai",
        "embedding_model": settings.MEMORY_STUDY_EMBEDDING_MODEL or EMBEDDING_MODEL,
        "embedding_revision": settings.MEMORY_STUDY_EMBEDDING_REVISION,
        "tokenizer": "o200k_base",
        "framework_contract": ["ingest", "snapshot", "restore", "retrieve", "inspect"],
        "official_frameworks": {
            "mem0": {
                "package": "mem0ai",
                "revision": MEM0_COMMIT,
                "entrypoint": "mem0.Memory.from_config",
            },
            "amem": {
                "package": "agentic-memory",
                "revision": AMEM_COMMIT,
                "entrypoint": "agentic_memory.memory_system.AgenticMemorySystem",
            },
        },
        "codex_subprocess_limit": MAX_CODEX_SUBPROCESSES,
        "archive_sha256": ARCHIVE_SHA256,
        "protocol": protocol_id,
        "artifact_root": settings.MEMORY_STUDY_ARTIFACT_ROOT,
        "scoring_context": scoring_context_for_protocol(protocol_id),
        # Explicitly recorded protocol omissions make accidental legacy
        # behavior visible in the snapshot.
        "checkpoints": [],
        "bootstrap": False,
        "training_scored": kind == StudyKind.formal,
        "training_memory_only": False,
        "shared_no_memory_baseline": False,
        "feedback_waves": list(waves),
    }
    return config


def _runner_snapshot(
    db: Session,
    config: dict,
    *,
    runner_factory=None,
) -> dict[str, dict]:
    """Resolve site runner rows once for an immutable runtime snapshot.

    Fingerprinting the CLI is intentionally best effort here: project creation
    must remain possible on a machine where the account-backed executable is
    not installed yet.  The freeze/preflight gate makes an absent fingerprint
    a launch blocker and fills it after the real probe succeeds.
    """
    stored = {
        (row.model, row.speed_mode): row for row in db.scalars(select(MSRunnerConfig))
    }
    fingerprint: dict[str, object] = {}
    try:
        runner = (runner_factory or CodexExecRunner)()
        frozen = getattr(runner, "runtime_fingerprint", None)
        if callable(frozen):
            value = frozen()
            if isinstance(value, dict):
                fingerprint = dict(value)
        else:
            frozen_runtime = getattr(runner, "frozen_runtime", None)
            if callable(frozen_runtime) and config.get("models"):
                value = frozen_runtime(
                    {
                        "model": str(config["models"][0]["model"]),
                        "reasoning_effort": str(
                            config["models"][0]["reasoning_effort"]
                        ),
                        "speed_mode": str(
                            config["models"][0].get("speed_mode", SPEED_MODE)
                        ),
                        "timeout_seconds": int(
                            config["models"][0].get(
                                "timeout_seconds", INITIAL_TIMEOUT_SECONDS
                            )
                        ),
                    }
                )
                if isinstance(value, dict):
                    fingerprint = {
                        key: value[key]
                        for key in (
                            "executable_path",
                            "executable_sha256",
                            "cli_version",
                            "sandbox",
                            "ephemeral",
                            "ignore_user_config",
                            "ignore_rules",
                            "output_schema",
                            "prompt_envelope_version",
                        )
                        if key in value
                    }
    except Exception:
        # The real structured preflight records the actionable failure; do not
        # make project creation depend on local CLI availability.
        fingerprint = {}
    snapshot: dict[str, dict] = {}
    for item in config.get("models", []):
        model = str(item["model"])
        speed_mode = str(item.get("speed_mode", SPEED_MODE))
        row = stored.get((model, speed_mode))
        runtime = {
            "model": model,
            "reasoning_effort": (
                row.reasoning_effort if row else item["reasoning_effort"]
            ),
            "speed_mode": speed_mode,
            "timeout_seconds": (
                row.timeout_seconds
                if row
                else int(item.get("timeout_seconds", INITIAL_TIMEOUT_SECONDS))
            ),
        }
        runtime.update(fingerprint)
        snapshot[model] = runtime
    return snapshot


def _embedding_spec(db: Session) -> dict:
    """Resolve the effective embedding spec; site config beats environment.

    The experiment protocol requires one OpenAI-compatible API embedding
    channel shared by ordinary retrieval, Mem0 and A-MEM.
    """
    row = db.get(MSSiteConfig, 1)
    if row is not None:
        return {
            "backend": row.embedding_backend or "openai",
            "model": row.embedding_model,
            "revision": row.embedding_revision,
            "api_base": row.embedding_api_base or "",
            "api_key": row.embedding_api_key or "",
            "dims": row.embedding_dims,
        }
    return {
        "backend": "openai",
        "model": settings.MEMORY_STUDY_EMBEDDING_MODEL or EMBEDDING_MODEL,
        "revision": settings.MEMORY_STUDY_EMBEDDING_REVISION,
        "api_base": settings.MEMORY_STUDY_EMBEDDING_API_BASE,
        "api_key": settings.MEMORY_STUDY_EMBEDDING_API_KEY,
        "dims": settings.MEMORY_STUDY_EMBEDDING_DIMS,
    }


def _record_hash(row: StudyRecord) -> str:
    return _sha(
        {
            "answer_id": row.answer_id,
            "question_id": row.question_id,
            "question": row.question_text,
            "reference_answer": row.reference_answer,
            "answer": row.student_answer,
            "teacher_score": row.teacher_score,
            "teacher_feedback": row.teacher_feedback,
        }
    )


class MemoryStudyService:
    def __init__(self, db: Session, worker_manager=None, runner_factory=None):
        self.db = db
        self.worker_manager = worker_manager or memory_study_worker_manager
        # Resolve at construction time so tests and deployments can replace
        # the runner without being trapped by a default argument bound during
        # module import.
        self.runner_factory = runner_factory or CodexExecRunner

    def build_dataset(
        self, kind: str, protocol_id: str = DEFAULT_PROTOCOL_ID
    ) -> FrozenStudyDataset:
        try:
            return build_dataset(
                settings.MEMORY_STUDY_ARCHIVE_PATH,
                kind=kind,
                protocol_id=protocol_id,
            )
        except (OSError, ValueError, KeyError) as exc:
            raise MemoryStudyDomainError("data_audit_failed", str(exc)) from exc

    def audit(
        self,
        kind: str = StudyKind.formal,
        protocol_id: str = DEFAULT_PROTOCOL_ID,
    ) -> dict:
        dataset = self.build_dataset(kind, protocol_id)
        summary = audit_summary(dataset)
        embedding = _embedding_spec(self.db)
        summary.update(
            {
                "ready": True,
                "embedding": {
                    "backend": embedding["backend"],
                    "model": embedding["model"],
                    "revision": embedding["revision"],
                    "revision_pinned": embedding["revision"]
                    not in BLOCKED_EMBEDDING_REVISIONS,
                },
                "source_archive": str(Path(settings.MEMORY_STUDY_ARCHIVE_PATH).name),
                "scoring_context": scoring_context_for_protocol(protocol_id),
            }
        )
        summary["ready"] = True
        return summary

    def list_projects(self) -> list[dict]:
        return [
            self._study_out(row)
            for row in self.db.scalars(
                select(MSStudy).order_by(MSStudy.created_at.desc())
            )
        ]

    def create_project(self, payload: dict) -> dict:
        kind = str(payload["kind"])
        protocol_id = str(payload.get("protocol_id") or DEFAULT_PROTOCOL_ID)
        if protocol_id not in SUPPORTED_PROTOCOL_IDS:
            raise MemoryStudyDomainError(
                "validation", f"unsupported memory-study protocol: {protocol_id}"
            )
        if not payload.get("data_processing_confirmed"):
            raise MemoryStudyDomainError(
                "validation", "data processing confirmation is required"
            )
        # Reject duplicate names up front so the database unique constraint
        # (`uq_ms_study_name`) surfaces as a 409 instead of a 500.
        name = str(payload["name"])
        if self.db.scalar(select(MSStudy).where(MSStudy.name == name)) is not None:
            raise MemoryStudyDomainError(
                "conflict", f"a project named {name!r} already exists"
            )
        self._require_run_gate(kind)
        dataset = self.build_dataset(kind, protocol_id)
        study_id = str(uuid4())
        speed_modes = {
            str(model): str(mode).strip()
            for model, mode in (payload.get("speed_modes") or {}).items()
        }
        unknown = set(speed_modes) - set(MODEL_IDS)
        if unknown:
            raise MemoryStudyDomainError(
                "validation",
                f"unknown model in speed_modes: {', '.join(sorted(unknown))}",
            )
        invalid_modes = {
            mode for mode in speed_modes.values() if mode not in RUNNER_SPEED_MODES
        }
        if invalid_modes:
            raise MemoryStudyDomainError(
                "validation",
                "speed_modes values must be one of 'standard', 'fast'",
            )
        requested_waves = tuple(
            dict.fromkeys(payload.get("feedback_waves") or ["full"])
        )
        if any(wave not in {"full", "no_feedback"} for wave in requested_waves):
            raise MemoryStudyDomainError(
                "validation", "feedback_waves must contain full and/or no_feedback"
            )
        if kind != StudyKind.formal and requested_waves != ("full",):
            raise MemoryStudyDomainError(
                "validation", "no_feedback wave is only available for formal studies"
            )
        config = _fixed_config(
            kind,
            speed_modes,
            requested_waves,
            protocol_id,
        )
        embedding = _embedding_spec(self.db)
        config["embedding_backend"] = embedding["backend"]
        config["embedding_model"] = embedding["model"]
        config["embedding_revision"] = embedding["revision"]
        config["embedding_api_base"] = embedding["api_base"]
        config["embedding_api_key"] = embedding["api_key"]
        config["embedding_dims"] = embedding["dims"]
        # Runner values are captured at project creation.  They are copied into
        # the frozen config and are never re-read from the site-level settings
        # while this study is running.
        config["runtime_snapshot"] = _runner_snapshot(
            self.db, config, runner_factory=self.runner_factory
        )
        active_orders = order_variants_for_kind(kind, protocol_id)
        active_repeats = repeats_for_kind(kind, protocol_id)
        expected = expected_call_counts(kind, requested_waves, protocol_id)
        active_conditions = tuple(
            config.get("conditions") or conditions_for_kind(kind, protocol_id)
        )
        active_framework_conditions = tuple(
            condition for condition in active_conditions if condition != "no_memory"
        )
        formal_study = kind == StudyKind.formal
        expected["materialized_memory_stores"] = (
            len(dataset.selected_questions)
            * len(MODEL_IDS)
            * len(active_orders)
            * len(active_framework_conditions)
        )
        study = MSStudy(
            id=study_id,
            protocol_id=protocol_id,
            name=name,
            kind=kind,
            status=StudyStatus.ready,
            data_processing_confirmed=True,
            data_manifest_json=dataset.manifest,
            config_json=config,
            expected_json=expected,
            progress_json={},
            preflight_json={},
            integrity_status="pending",
            integrity_json={},
            results_embargoed=True,
        )
        self.db.add(study)
        self.db.flush()
        for question_id in dataset.selected_questions:
            self.db.add(
                MSQuestionRun(
                    study_id=study_id,
                    question_id=question_id,
                    status="pending",
                )
            )
        self.db.flush()
        for row in dataset.records:
            self.db.add(
                MSRecord(
                    study_id=study_id,
                    answer_id=row.answer_id,
                    group_id=row.group_id,
                    question_id=row.question_id,
                    question_text=row.question_text,
                    reference_answer=row.reference_answer,
                    student_answer=row.student_answer,
                    teacher_score=row.teacher_score,
                    teacher_feedback=row.teacher_feedback,
                    verification_feedback=row.verification_feedback,
                    source_split=row.source_split,
                    selection_kind=row.selection_kind or "",
                    input_sha256=_record_hash(row),
                    source_path=row.source_path,
                    source_position=row.source_position,
                )
            )
        self.db.flush()
        record_rows = {
            row.answer_id: row
            for row in self.db.scalars(
                select(MSRecord).where(MSRecord.study_id == study_id)
            )
        }
        dataset_records = {row.answer_id: row for row in dataset.records}

        def stream_id(model: str, question_id: str, condition: str, order: str) -> str:
            return _sha(
                {
                    "study_id": study_id,
                    "model": model,
                    "question_id": question_id,
                    "condition": condition,
                    "order_variant": order,
                }
            )[:32]

        # The memory stores are independent per model, question, order, and
        # feedback mode.  Formal v3 starts every store empty and adds the
        # current training answer only after its model score succeeds.  Older
        # development/pilot projects keep their materialized retrieval
        # snapshot semantics for compatibility.
        for model in MODEL_IDS:
            for question_id in dataset.selected_questions:
                for order_variant in active_orders:
                    for condition in active_framework_conditions:
                        framework, feedback_mode = _condition_spec(condition)
                        store_stream_id = stream_id(
                            model, question_id, condition, order_variant
                        )
                        snapshot_path = (
                            Path(settings.MEMORY_STUDY_ARTIFACT_ROOT)
                            / study_id
                            / "memory"
                            / model
                            / question_id
                            / condition
                            / order_variant
                            / "snapshot.json"
                        )
                        is_deterministic = condition in deterministic_memory_conditions_for_protocol(
                            protocol_id
                        )
                        snapshot: dict | None = None
                        committed = 0
                        status = "pending"
                        if is_deterministic and not formal_study:
                            ordered_records = [
                                dataset_records[answer_id]
                                for answer_id in dataset.orders[question_id][
                                    order_variant
                                ]
                            ]
                            provider = SimpleNamespace(
                                model_name=config["embedding_model"],
                                revision=config["embedding_revision"],
                            )
                            deterministic = CaseRetrievalAdapter(
                                feedback_mode=feedback_mode,
                                provider=provider,
                                memory_profile=memory_profile_for_protocol(protocol_id),
                                items=[
                                    (
                                        record,
                                        case_payload(
                                            record,
                                            feedback_mode,
                                            profile=memory_profile_for_protocol(protocol_id),
                                        ),
                                    )
                                    for record in ordered_records
                                ],
                            )
                            snapshot = deterministic.snapshot()
                            snapshot["materialization"] = (
                                "deterministic_project_creation"
                            )
                            snapshot["stream_id"] = store_stream_id
                            committed = len(ordered_records)
                            status = "completed"
                        store = MSMemoryStore(
                            study_id=study_id,
                            model=model,
                            question_id=question_id,
                            framework=framework,
                            condition=condition,
                            feedback_mode=feedback_mode,
                            order_variant=order_variant,
                            status=status,
                            committed_count=committed,
                            history_count=committed,
                            snapshot_json=snapshot,
                            snapshot_sha256=memory_hash(snapshot) if snapshot else None,
                            snapshot_path=str(snapshot_path),
                            snapshot_stream_id=store_stream_id,
                            framework_version=(
                                "case-retrieval-v1" if is_deterministic else None
                            ),
                        )
                        self.db.add(store)
                        self.db.flush()
                        if snapshot is not None:
                            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                            snapshot_path.write_text(
                                json.dumps(
                                    snapshot, ensure_ascii=False, sort_keys=True
                                ),
                                encoding="utf-8",
                            )
                        for answer_id in dataset.orders[question_id][order_variant]:
                            training = record_rows.get(answer_id)
                            if (
                                training is None
                                or training.selection_kind != "training"
                            ):
                                raise MemoryStudyDomainError(
                                    "data_audit_failed",
                                    f"training record missing: {answer_id}",
                                )
                            if formal_study:
                                self.db.add(
                                    MSCall(
                                        study_id=study_id,
                                        memory_store_id=store.id,
                                        model=model,
                                        question_id=question_id,
                                        condition=condition,
                                        framework=framework,
                                        feedback_mode=feedback_mode,
                                        order_variant=order_variant,
                                        kind="score",
                                        answer_id=answer_id,
                                        repeat=0,
                                        status="pending",
                                        manual_score=training.teacher_score,
                                        max_score=dataset.score_ceilings[question_id],
                                    )
                                )
                            should_write_memory = (
                                condition != "no_memory"
                                if formal_study
                                else condition
                                in memory_write_conditions_for_protocol(protocol_id)
                            )
                            if should_write_memory:
                                self.db.add(
                                    MSCall(
                                        study_id=study_id,
                                        memory_store_id=store.id,
                                        model=model,
                                        question_id=question_id,
                                        condition=condition,
                                        framework=framework,
                                        feedback_mode=feedback_mode,
                                        order_variant=order_variant,
                                        kind="memory_write",
                                        answer_id=answer_id,
                                        repeat=0,
                                        status="pending",
                                        manual_score=(
                                            training.teacher_score
                                            if not formal_study
                                            else None
                                        ),
                                        max_score=dataset.score_ceilings[question_id],
                                    )
                                )
        if formal_study:
            # The no-memory baseline has no store, but it still receives the
            # same one-pass training score so train/test discrepancy reporting
            # is complete for every condition.
            for model in MODEL_IDS:
                for question_id in dataset.selected_questions:
                    for order_variant in order_variants_for_condition(
                        kind, protocol_id, "no_memory"
                    ):
                        for answer_id in dataset.orders[question_id][order_variant]:
                            training = record_rows[answer_id]
                            self.db.add(
                                MSCall(
                                    study_id=study_id,
                                    memory_store_id=None,
                                    model=model,
                                    question_id=question_id,
                                    condition="no_memory",
                                    framework="none",
                                    feedback_mode="full",
                                    order_variant=order_variant,
                                    kind="score",
                                    answer_id=answer_id,
                                    repeat=0,
                                    status="pending",
                                    manual_score=training.teacher_score,
                                    max_score=dataset.score_ceilings[question_id],
                                )
                            )
        for model in MODEL_IDS:
            for question_id in dataset.selected_questions:
                test_ids = dataset.orders[question_id]["test"]
                for condition in active_conditions:
                    condition_orders = order_variants_for_condition(
                        kind, protocol_id, condition
                    )
                    for order_variant in condition_orders:
                        framework, feedback_mode = _condition_spec(condition)
                        store = None
                        if condition != "no_memory":
                            store = self.db.scalar(
                                select(MSMemoryStore).where(
                                    MSMemoryStore.study_id == study_id,
                                    MSMemoryStore.model == model,
                                    MSMemoryStore.question_id == question_id,
                                    MSMemoryStore.condition == condition,
                                    MSMemoryStore.order_variant == order_variant,
                                )
                            )
                        for answer_id in test_ids:
                            test = record_rows[answer_id]
                            for repeat in active_repeats:
                                self.db.add(
                                    MSCall(
                                        study_id=study_id,
                                        memory_store_id=store.id if store else None,
                                        model=model,
                                        question_id=question_id,
                                        condition=condition,
                                        framework=framework,
                                        feedback_mode=feedback_mode,
                                        order_variant=order_variant,
                                        kind="score",
                                        answer_id=answer_id,
                                        repeat=repeat,
                                        status="pending",
                                        manual_score=test.teacher_score,
                                        max_score=dataset.score_ceilings[question_id],
                                    )
                                )
        self.db.add(
            MSAuditEvent(
                study_id=study_id,
                event_type="study_created",
                status="passed",
                detail_json={
                    "manifest_sha256": dataset.manifest["manifest_sha256"],
                    "record_count": len(dataset.records),
                    "expected": expected,
                },
            )
        )
        self.db.commit()
        self.refresh_progress(study_id, persist=True)
        return self.get_project(study_id)

    def _require_run_gate(self, kind: str) -> None:
        """Keep study creation independent; runtime gates remain mandatory."""
        # A pilot is useful benchmarking, but is not a prerequisite for the
        # six-question formal run. Freeze, fresh Luna preflight,
        # immutable runtime verification, and the final integrity audit still
        # protect execution and report release.
        return None

    def get_project(self, study_id: str) -> dict:
        return self._study_out(self._study(study_id))

    def freeze(self, study_id: str) -> dict:
        study = self._study(study_id)
        if study.status not in {StudyStatus.ready, StudyStatus.draft}:
            raise MemoryStudyDomainError("conflict", "only a ready study can be frozen")
        if not study.data_processing_confirmed:
            raise MemoryStudyDomainError(
                "validation", "data processing confirmation is required"
            )
        if study.config_json.get("embedding_backend") != "openai":
            raise MemoryStudyDomainError(
                "protocol_drift",
                "the memory study requires API embedding for every retrieval condition",
            )
        if study.config_json.get("embedding_revision") in {
            "",
            "main",
            "latest",
            "unresolved",
        }:
            raise MemoryStudyDomainError(
                "validation",
                "an immutable API embedding revision must be configured before freeze",
            )
        if study.config_json.get("token_limit") is not None or study.config_json.get(
            "checkpoints"
        ):
            raise MemoryStudyDomainError(
                "protocol_drift",
                "study configuration contains forbidden legacy controls",
            )
        expected_scoring_context = scoring_context_for_protocol(study.protocol_id)
        if (
            study.protocol_id == V3_R2_PROTOCOL_ID
            and study.config_json.get("scoring_context") != expected_scoring_context
        ):
            raise MemoryStudyDomainError(
                "protocol_drift",
                "scoring instrument declaration does not match the protocol",
            )
        # Older locally-created rows may not have a runtime snapshot.  Capture
        # it exactly once at freeze so subsequent site-config edits cannot
        # change an in-flight experiment.
        if not study.config_json.get("runtime_snapshot"):
            config = dict(study.config_json)
            config["runtime_snapshot"] = _runner_snapshot(
                self.db, config, runner_factory=self.runner_factory
            )
            study.config_json = config
        study.preflight_json = {}
        study.status = StudyStatus.frozen
        study.frozen_at = utc_now_naive()
        self.db.add(
            MSAuditEvent(
                study_id=study_id,
                event_type="config_frozen",
                status="passed",
                detail_json={"config_sha256": _sha(study.config_json)},
            )
        )
        self.db.commit()
        return self.get_project(study_id)

    def preflight(self, study_id: str) -> dict:
        """Run one small real structured-output call per frozen model.

        The calls are deliberately independent and execute in parallel, but
        all persistence happens on this request's Session after both workers
        return.  A failed preflight is evidence, not a 500: the project stays
        frozen and ``start`` refuses it until a later preflight passes.
        """
        study = self._study(study_id)
        if study.status not in {
            StudyStatus.frozen,
            StudyStatus.paused,
            StudyStatus.attention_required,
        }:
            raise MemoryStudyDomainError(
                "conflict", "study must be frozen or paused before preflight"
            )
        runtime_snapshot = study.config_json.get("runtime_snapshot")
        if not isinstance(runtime_snapshot, dict) or any(
            not isinstance(runtime_snapshot.get(model), dict) for model in MODEL_IDS
        ):
            raise MemoryStudyDomainError(
                "protocol_drift", "study has no complete immutable runner snapshot"
            )
        runtime_row = self.db.scalar(
            select(MSSchedulerRuntime)
            .where(MSSchedulerRuntime.id == 1)
            .with_for_update()
        )
        now = utc_now_naive()
        if (
            runtime_row is not None
            and runtime_row.lease_until is not None
            and runtime_row.lease_until >= now
            and runtime_row.status in {"online", "preflight"}
        ):
            raise MemoryStudyDomainError(
                "runtime_busy", "another memory-study worker or preflight is active"
            )

        preflight_owner = f"preflight-{uuid4().hex[:12]}"
        if runtime_row is None:
            runtime_row = MSSchedulerRuntime(id=1)
            self.db.add(runtime_row)
        runtime_row.owner_id = preflight_owner
        runtime_row.status = "preflight"
        # Keep the advertised runtime capacity equal to the worker's four
        # condition slots; ``active_subprocesses`` still reflects the one
        # Luna probe currently running.
        runtime_row.max_subprocesses = MAX_CODEX_SUBPROCESSES
        runtime_row.active_subprocesses = len(MODEL_IDS)
        runtime_row.slots_json = {
            f"preflight-{model}": {
                "model": model,
                "study_id": study.id,
                "state": "running",
                "phase": "preflight",
            }
            for model in MODEL_IDS
        }
        runtime_row.heartbeat_at = now
        runtime_row.lease_until = now + timedelta(seconds=600)

        initial_config_sha = _sha(study.config_json)
        config_sha = initial_config_sha
        study.preflight_json = {
            "status": "running",
            "config_sha256": config_sha,
            "started_at": now.isoformat(),
            "models": {},
        }
        self.db.add(
            MSAuditEvent(
                study_id=study.id,
                event_type="runtime_preflight_started",
                status="running",
                detail_json={"config_sha256": config_sha, "models": list(MODEL_IDS)},
            )
        )
        self.db.commit()

        def run_one(model: str) -> tuple[str, dict]:
            runtime = dict(runtime_snapshot[model])
            runtime.setdefault(
                "service_tier",
                "fast" if runtime.get("speed_mode") == "fast" else "default",
            )
            messages = (
                {
                    "role": "system",
                    "content": (
                        "Return only the JSON object required by the supplied schema. "
                        "Set ok to true and copy the input probe_token exactly into probe_token."
                    ),
                },
                {
                    "role": "user",
                    # The token is deliberately visible to the model so it can
                    # prove that this exact preflight request was understood.
                    # Audit records retain only the request hash below; the
                    # token is not persisted in the prompt/audit payload.
                    "content": json.dumps(
                        {
                            "model": model,
                            "probe_token": PREFLIGHT_PROBE,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            )
            started = time.monotonic()
            try:
                runner = self.runner_factory()
                frozen_runtime = _frozen_runtime(runner, runtime)
                fingerprint = {
                    key: frozen_runtime[key]
                    for key in PREFLIGHT_FINGERPRINT_KEYS
                    if key in frozen_runtime
                }
                missing_fingerprint = [
                    key
                    for key in PREFLIGHT_FINGERPRINT_KEYS
                    if not fingerprint.get(key)
                ]
                if missing_fingerprint:
                    raise RuntimeError(
                        "Codex preflight fingerprint is incomplete: "
                        + ", ".join(missing_fingerprint)
                    )
                for key in FROZEN_RUNTIME_KEYS:
                    if frozen_runtime.get(key) != runtime.get(key):
                        raise RuntimeError(f"Codex preflight runtime mismatch in {key}")
                result = runner.run(
                    messages=messages,
                    runtime=runtime,
                    schema=PreflightOutput,
                )
                value = getattr(result, "value", result)
                if isinstance(value, PreflightOutput):
                    value = value.model_dump(mode="json")
                if not isinstance(value, dict):
                    raise ValueError("preflight response was not a JSON object")
                # Fake/local runners may bypass ``CodexExecRunner``'s Pydantic
                # boundary.  Validate here as well so the preflight itself can
                # never certify an object that violates the strict schema.
                value = PreflightOutput.model_validate(value).model_dump(mode="json")
                if (
                    value.get("ok") is not True
                    or value.get("probe_token") != PREFLIGHT_PROBE
                ):
                    raise ValueError(
                        "preflight response did not confirm the probe token"
                    )
                raw_json = getattr(result, "raw_json", "") or value
                return model, {
                    "status": "passed",
                    "model": model,
                    "runtime": {
                        "model": runtime.get("model"),
                        "reasoning_effort": runtime.get("reasoning_effort"),
                        "speed_mode": runtime.get("speed_mode"),
                        "timeout_seconds": runtime.get("timeout_seconds"),
                    },
                    "fingerprint": fingerprint,
                    "request_sha256": _sha(messages),
                    "response_sha256": _sha(raw_json),
                    "response_excerpt": _redact_text(
                        (
                            raw_json
                            if isinstance(raw_json, str)
                            else json.dumps(raw_json, ensure_ascii=False)
                        ),
                        800,
                    ),
                    "latency_ms": getattr(
                        result, "latency_ms", int((time.monotonic() - started) * 1000)
                    ),
                }
            except Exception as exc:
                return model, {
                    "status": "failed",
                    "model": model,
                    "runtime": {
                        "model": runtime.get("model"),
                        "reasoning_effort": runtime.get("reasoning_effort"),
                        "speed_mode": runtime.get("speed_mode"),
                        "timeout_seconds": runtime.get("timeout_seconds"),
                    },
                    "request_sha256": _sha(messages),
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "failure_code": _preflight_failure_code(exc),
                    "failure_summary": _preflight_summary(exc),
                    "stderr_excerpt": (
                        _redact_text(getattr(exc, "stderr", ""), 800)
                        if getattr(exc, "stderr", "")
                        else None
                    ),
                }

        checks: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=len(MODEL_IDS)) as pool:
            futures = [pool.submit(run_one, model) for model in MODEL_IDS]
            for future in as_completed(futures):
                model, check = future.result()
                checks[model] = check
        passed = len(checks) == len(MODEL_IDS) and all(
            check.get("status") == "passed" for check in checks.values()
        )
        # Persist the observed executable identity into the frozen runtime
        # snapshot.  It is not a secret and lets a later start/resume compare
        # the exact CLI that was preflighted.  Only successful model probes can
        # contribute a fingerprint; a failed probe remains actionable evidence.
        frozen_config = dict(study.config_json)
        frozen_runtimes = {
            model: dict(value)
            for model, value in (runtime_snapshot or {}).items()
            if isinstance(value, dict)
        }
        for model, check in checks.items():
            if check.get("status") == "passed" and isinstance(
                check.get("fingerprint"), dict
            ):
                frozen_runtimes.setdefault(model, {}).update(check["fingerprint"])
        frozen_config["runtime_snapshot"] = frozen_runtimes
        study.config_json = frozen_config
        config_sha = _sha(study.config_json)
        completed_at = utc_now_naive().isoformat()
        study.preflight_json = {
            "status": "passed" if passed else "failed",
            "config_sha256": config_sha,
            "started_at": now.isoformat(),
            "completed_at": completed_at,
            "models": {model: checks.get(model) for model in MODEL_IDS},
        }
        failures = [
            f"{model}: {checks.get(model, {}).get('failure_summary', 'missing result')}"
            for model in MODEL_IDS
            if checks.get(model, {}).get("status") != "passed"
        ]
        if not passed:
            study.error_summary = "runtime preflight failed; " + "; ".join(failures)
        elif self._count_calls(study.id, status="failed"):
            # Re-running a preflight after a slot failure must not make the
            # underlying call failure disappear from the project banner.
            failed_count = self._count_calls(study.id, status="failed")
            study.error_summary = (
                f"{failed_count} call(s) failed; manual retry required"
            )
        else:
            study.error_summary = None
        self.db.add(
            MSAuditEvent(
                study_id=study.id,
                event_type="runtime_preflight",
                status="passed" if passed else "failed",
                detail_json={
                    "config_sha256": config_sha,
                    "initial_config_sha256": initial_config_sha,
                    "models": study.preflight_json["models"],
                },
            )
        )
        runtime_row = self.db.get(MSSchedulerRuntime, 1)
        if runtime_row is not None and runtime_row.owner_id == preflight_owner:
            runtime_row.status = "offline"
            runtime_row.active_subprocesses = 0
            runtime_row.slots_json = {}
            runtime_row.lease_until = None
            runtime_row.heartbeat_at = utc_now_naive()
        self.db.commit()
        return self.get_project(study_id)

    def _preflight_is_fresh(self, study: MSStudy) -> tuple[bool, str | None]:
        evidence = study.preflight_json or {}
        if evidence.get("status") != "passed":
            return False, "runtime preflight has not passed"
        if evidence.get("config_sha256") != _sha(study.config_json):
            return False, "runtime preflight is stale because the frozen config changed"
        models = evidence.get("models")
        if not isinstance(models, dict):
            return False, "runtime preflight evidence is incomplete"
        runtime_snapshot = study.config_json.get("runtime_snapshot", {})
        if not isinstance(runtime_snapshot, dict):
            return False, "runtime preflight runtime snapshot is incomplete"
        for model in MODEL_IDS:
            check = models.get(model)
            if not isinstance(check, dict) or check.get("status") != "passed":
                return False, f"runtime preflight did not pass for {model}"
            try:
                runner = self.runner_factory()
                runtime = dict(runtime_snapshot.get(model) or {})
                current = _frozen_runtime(runner, runtime)
            except Exception as exc:
                return (
                    False,
                    f"cannot validate current Codex runtime: {_preflight_summary(exc)}",
                )
            expected = check.get("fingerprint") or {}
            expected_runtime = check.get("runtime") or {}
            for key in FROZEN_RUNTIME_KEYS:
                if expected_runtime.get(key) != current.get(key):
                    return False, f"Codex runtime drift detected in {key} for {model}"
            for key in PREFLIGHT_FINGERPRINT_KEYS:
                if not expected.get(key):
                    return (
                        False,
                        f"runtime preflight fingerprint is incomplete for {model}",
                    )
                if current.get(key) != expected.get(key):
                    return False, f"Codex runtime drift detected in {key} for {model}"
                frozen_value = (runtime_snapshot.get(model) or {}).get(key)
                if frozen_value and current.get(key) != frozen_value:
                    return (
                        False,
                        f"frozen Codex runtime drift detected in {key} for {model}",
                    )
        return True, None

    def start(self, study_id: str) -> dict:
        study = self._study(study_id)
        if study.status != StudyStatus.frozen:
            raise MemoryStudyDomainError(
                "conflict",
                "only a frozen study can be started; use resume after a pause",
            )
        fresh, reason = self._preflight_is_fresh(study)
        if not fresh:
            self._mark_preflight_stale(study, reason)
            raise MemoryStudyDomainError(
                "conflict", reason or "runtime preflight required"
            )
        study.status = StudyStatus.running
        study.started_at = study.started_at or utc_now_naive()
        for shard in self.db.scalars(
            select(MSQuestionRun).where(
                MSQuestionRun.study_id == study_id,
                MSQuestionRun.status.in_(["pending", "paused"]),
            )
        ):
            shard.status = "running"
            shard.started_at = shard.started_at or utc_now_naive()
        self.db.commit()
        self.worker_manager.start(study_id)
        return self.get_project(study_id)

    def pause(self, study_id: str) -> dict:
        study = self._study(study_id)
        if study.status != StudyStatus.running:
            raise MemoryStudyDomainError(
                "conflict", "only a running study can be paused"
            )
        pauser = getattr(self.worker_manager, "pause", None)
        if callable(pauser):
            pauser(study_id)
        study.status = StudyStatus.paused
        self.db.commit()
        return self.get_project(study_id)

    def resume(self, study_id: str) -> dict:
        study = self._study(study_id)
        if study.status not in {StudyStatus.paused, StudyStatus.attention_required}:
            raise MemoryStudyDomainError(
                "conflict", "study is not paused or awaiting attention"
            )
        serial_issue = self._strict_serial_issue(study_id)
        if serial_issue is not None:
            raise MemoryStudyDomainError("conflict", serial_issue)
        failed_calls = self._count_calls(study_id, status="failed")
        if failed_calls:
            raise MemoryStudyDomainError(
                "conflict",
                f"{failed_calls} failed call(s) must be manually retried before resume",
            )
        fresh, reason = self._preflight_is_fresh(study)
        if not fresh:
            self._mark_preflight_stale(study, reason)
            raise MemoryStudyDomainError(
                "conflict", reason or "runtime preflight required"
            )
        study.status = StudyStatus.running
        study.error_summary = None
        for shard in self.db.scalars(
            select(MSQuestionRun).where(
                MSQuestionRun.study_id == study_id,
                MSQuestionRun.status.in_(["pending", "paused"]),
            )
        ):
            shard.status = "running"
            shard.error_summary = None
        self.db.commit()
        self.worker_manager.start(study_id)
        return self.get_project(study_id)

    def terminate(self, study_id: str) -> dict:
        study = self._study(study_id)
        if study.status in {StudyStatus.completed, StudyStatus.terminated}:
            raise MemoryStudyDomainError("conflict", "study is already terminal")
        terminator = getattr(self.worker_manager, "terminate", None)
        if callable(terminator):
            terminator(study_id)
        study.status = StudyStatus.terminated
        study.terminated_at = utc_now_naive()
        for call in self.db.scalars(
            select(MSCall).where(
                MSCall.study_id == study_id,
                MSCall.status.in_(["pending", "leased", "running"]),
            )
        ):
            call.status = "cancelled"
            call.worker_id = None
            call.slot_id = None
            call.lease_until = None
        self.db.commit()
        return self.get_project(study_id)

    def _question_run(self, study_id: str, question_id: str) -> MSQuestionRun:
        row = self.db.scalar(
            select(MSQuestionRun).where(
                MSQuestionRun.study_id == study_id,
                MSQuestionRun.question_id == question_id,
            )
        )
        if row is None:
            raise MemoryStudyDomainError("not_found", "question shard not found")
        return row

    def list_question_runs(self, study_id: str) -> list[dict]:
        self._study(study_id)
        return [
            {
                "id": row.id,
                "question_id": row.question_id,
                "status": row.status,
                "error_summary": row.error_summary,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "completed_at": (
                    row.completed_at.isoformat() if row.completed_at else None
                ),
            }
            for row in self.db.scalars(
                select(MSQuestionRun)
                .where(MSQuestionRun.study_id == study_id)
                .order_by(MSQuestionRun.question_id)
            )
        ]

    def start_question(self, study_id: str, question_id: str) -> dict:
        study = self._study(study_id)
        shard = self._question_run(study_id, question_id)
        if shard.status in {"completed", "terminated"}:
            raise MemoryStudyDomainError("conflict", "question shard is terminal")
        if study.status in {
            StudyStatus.ready,
            StudyStatus.draft,
            StudyStatus.completed,
            StudyStatus.terminated,
        }:
            raise MemoryStudyDomainError(
                "conflict",
                "freeze the study and pass runtime preflight before starting a question",
            )
        if study.status in {
            StudyStatus.frozen,
            StudyStatus.paused,
            StudyStatus.attention_required,
        }:
            fresh, reason = self._preflight_is_fresh(study)
            if not fresh:
                self._mark_preflight_stale(study, reason)
                raise MemoryStudyDomainError(
                    "conflict", reason or "runtime preflight required"
                )
        study.status = StudyStatus.running
        study.started_at = study.started_at or utc_now_naive()
        shard.status = "running"
        shard.started_at = shard.started_at or utc_now_naive()
        self.db.commit()
        self.worker_manager.start(study_id)
        return self.get_project(study_id)

    def pause_question(self, study_id: str, question_id: str) -> dict:
        shard = self._question_run(study_id, question_id)
        if shard.status != "running":
            raise MemoryStudyDomainError("conflict", "question shard is not running")
        shard.status = "paused"
        shard.updated_at = utc_now_naive()
        study = self._study(study_id)
        if study.status == StudyStatus.running:
            other_running = self.db.scalar(
                select(func.count(MSQuestionRun.id)).where(
                    MSQuestionRun.study_id == study_id,
                    MSQuestionRun.status == "running",
                    MSQuestionRun.question_id != question_id,
                )
            )
            if not other_running:
                pauser = getattr(self.worker_manager, "pause", None)
                if callable(pauser):
                    pauser(study_id)
                study.status = StudyStatus.paused
        self.db.commit()
        return self.get_project(study_id)

    def resume_question(self, study_id: str, question_id: str) -> dict:
        study = self._study(study_id)
        shard = self._question_run(study_id, question_id)
        if shard.status in {"completed", "terminated"}:
            raise MemoryStudyDomainError("conflict", "question shard is terminal")
        failed = self._count_calls(study_id, status="failed", question_id=question_id)
        if failed:
            raise MemoryStudyDomainError(
                "conflict", f"{failed} failed call(s) must be manually retried first"
            )
        fresh, reason = self._preflight_is_fresh(study)
        if not fresh:
            self._mark_preflight_stale(study, reason)
            raise MemoryStudyDomainError(
                "conflict", reason or "runtime preflight required"
            )
        study.status = StudyStatus.running
        shard.status = "running"
        self.db.commit()
        self.worker_manager.start(study_id)
        return self.get_project(study_id)

    def restore_question(self, study_id: str, question_id: str) -> dict:
        """Reopen an accidentally terminated question without touching results.

        Termination cancels only calls which had not reached a terminal result.
        Restoring therefore requeues those cancelled calls and leaves every
        succeeded/failed attempt and its audit evidence intact.
        """
        study = self._study(study_id)
        shard = self._question_run(study_id, question_id)
        if shard.status != "terminated":
            raise MemoryStudyDomainError(
                "conflict", "only a terminated question shard can be restored"
            )
        if study.status in {StudyStatus.completed, StudyStatus.terminated}:
            raise MemoryStudyDomainError(
                "conflict", "the parent study is terminal and cannot be restored"
            )
        fresh, reason = self._preflight_is_fresh(study)
        if not fresh:
            self._mark_preflight_stale(study, reason)
            raise MemoryStudyDomainError(
                "conflict", reason or "runtime preflight required"
            )
        restored = 0
        for call in self.db.scalars(
            select(MSCall).where(
                MSCall.study_id == study_id,
                MSCall.question_id == question_id,
                MSCall.status == "cancelled",
            )
        ):
            call.status = "pending"
            call.worker_id = None
            call.slot_id = None
            call.lease_until = None
            call.failure_code = None
            call.failure_summary = None
            restored += 1
        shard.status = "running"
        shard.completed_at = None
        shard.updated_at = utc_now_naive()
        study.status = StudyStatus.running
        self.db.commit()
        self.worker_manager.start(study_id)
        result = self.get_project(study_id)
        result["restored_calls"] = restored
        return result

    def terminate_question(self, study_id: str, question_id: str) -> dict:
        study = self._study(study_id)
        shard = self._question_run(study_id, question_id)
        if shard.status in {"completed", "terminated"}:
            raise MemoryStudyDomainError("conflict", "question shard is terminal")
        for call in self.db.scalars(
            select(MSCall).where(
                MSCall.study_id == study_id,
                MSCall.question_id == question_id,
                MSCall.status.in_(["pending", "leased", "running"]),
            )
        ):
            call.status = "cancelled"
            call.worker_id = None
            call.slot_id = None
            call.lease_until = None
        shard.status = "terminated"
        shard.completed_at = utc_now_naive()
        if study.status == StudyStatus.running:
            other_running = self.db.scalar(
                select(func.count(MSQuestionRun.id)).where(
                    MSQuestionRun.study_id == study_id,
                    MSQuestionRun.status == "running",
                    MSQuestionRun.question_id != question_id,
                )
            )
            if not other_running:
                terminator = getattr(self.worker_manager, "terminate", None)
                if callable(terminator):
                    terminator(study_id)
                study.status = StudyStatus.paused
        self.db.commit()
        return self.get_project(study_id)

    # ---------------------------------------------------------------- site
    # config (runner params + embedding) management

    def list_runner_configs(self) -> list[dict]:
        """Return current-protocol runner configs for every speed mode; stored
        rows override frozen defaults, and missing rows use protocol defaults."""
        stored = {
            (row.model, row.speed_mode): row
            for row in self.db.scalars(select(MSRunnerConfig))
        }
        merged = [
            {
                "model": model,
                "speed_mode": speed_mode,
                "reasoning_effort": REASONING_EFFORT,
                "timeout_seconds": INITIAL_TIMEOUT_SECONDS,
            }
            for model in MODEL_IDS
            for speed_mode in RUNNER_SPEED_MODES
        ]
        for item in merged:
            row = stored.get((item["model"], item["speed_mode"]))
            if row is not None:
                item["reasoning_effort"] = row.reasoning_effort
                item["timeout_seconds"] = row.timeout_seconds
        return merged

    def create_runner_config(self, payload: dict) -> dict:
        model = str(payload["model"])
        speed_mode = str(payload["speed_mode"]).strip()
        if model not in MODEL_IDS:
            raise MemoryStudyDomainError(
                "validation",
                f"model must be one of {', '.join(MODEL_IDS)}",
            )
        if speed_mode not in RUNNER_SPEED_MODES:
            raise MemoryStudyDomainError(
                "validation",
                "speed_mode must be one of 'standard', 'fast'",
            )
        if self.db.get(MSRunnerConfig, (model, speed_mode)) is not None:
            raise MemoryStudyDomainError(
                "conflict",
                f"runner config already exists for {model}/{speed_mode}",
            )
        row = MSRunnerConfig(
            model=model, speed_mode=speed_mode, **self._runner_payload_fields(payload)
        )
        self.db.add(row)
        self.db.commit()
        return self._runner_out(row)

    def update_runner_config(self, model: str, speed_mode: str, payload: dict) -> dict:
        row = self.db.get(MSRunnerConfig, (model, speed_mode))
        if row is None:
            raise MemoryStudyDomainError(
                "not_found", f"runner config not found: {model}/{speed_mode}"
            )
        fields = self._runner_payload_fields(payload)
        row.reasoning_effort = fields["reasoning_effort"]
        row.timeout_seconds = fields["timeout_seconds"]
        self.db.commit()
        return self._runner_out(row)

    def delete_runner_config(self, model: str, speed_mode: str) -> None:
        row = self.db.get(MSRunnerConfig, (model, speed_mode))
        if row is None:
            raise MemoryStudyDomainError(
                "not_found", f"runner config not found: {model}/{speed_mode}"
            )
        self.db.delete(row)
        self.db.commit()

    @staticmethod
    def _runner_payload_fields(payload: dict) -> dict:
        reasoning_effort = str(payload["reasoning_effort"]).strip()
        timeout_seconds = payload["timeout_seconds"]
        if not reasoning_effort:
            raise MemoryStudyDomainError("validation", "reasoning_effort is required")
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or not 1 <= timeout_seconds <= 3600
        ):
            raise MemoryStudyDomainError(
                "validation", "timeout_seconds must be an integer between 1 and 3600"
            )
        return {
            "reasoning_effort": reasoning_effort,
            "timeout_seconds": timeout_seconds,
        }

    @staticmethod
    def _runner_out(row: MSRunnerConfig) -> dict:
        return {
            "model": row.model,
            "reasoning_effort": row.reasoning_effort,
            "speed_mode": row.speed_mode,
            "timeout_seconds": row.timeout_seconds,
        }

    def get_site_config(self) -> dict:
        spec = _embedding_spec(self.db)
        backend = spec["backend"]
        return {
            "embedding_backend": backend,
            "embedding_model": spec["model"],
            "embedding_revision": spec["revision"],
            "embedding_api_base": spec["api_base"],
            # The key is never echoed back to the browser; only whether one
            # is configured, so the form can show a "configured" placeholder.
            "embedding_api_key_set": bool(spec["api_key"]),
            "embedding_dims": spec["dims"],
            "revision_pinned": spec["revision"] not in BLOCKED_EMBEDDING_REVISIONS,
        }

    def update_site_config(self, payload: dict) -> dict:
        backend = str(payload.get("embedding_backend") or "openai").strip()
        model = str(payload["embedding_model"]).strip()
        revision = str(payload["embedding_revision"]).strip()
        api_base = str(payload.get("embedding_api_base") or "").strip()
        api_key = str(payload.get("embedding_api_key") or "").strip()
        dims = payload.get("embedding_dims")
        if backend not in EMBEDDING_BACKENDS:
            raise MemoryStudyDomainError(
                "validation",
                f"embedding_backend must be one of {', '.join(EMBEDDING_BACKENDS)}",
            )
        if not model:
            raise MemoryStudyDomainError("validation", "embedding_model is required")
        if not api_base:
            raise MemoryStudyDomainError(
                "validation", "embedding_api_base is required for the openai backend"
            )
        if dims is not None and (
            not isinstance(dims, int) or isinstance(dims, bool) or dims < 1
        ):
            raise MemoryStudyDomainError(
                "validation", "embedding_dims must be a positive integer"
            )
        existing = self.db.get(MSSiteConfig, 1)
        if not api_key and not (existing is not None and existing.embedding_api_key):
            raise MemoryStudyDomainError(
                "validation",
                "embedding_api_key is required for the openai backend",
            )
        if revision in BLOCKED_EMBEDDING_REVISIONS:
            raise MemoryStudyDomainError(
                "validation",
                "embedding revision must be immutable ('', main, latest, unresolved are rejected)",
            )
        row = self.db.get(MSSiteConfig, 1)
        if row is None:
            row = MSSiteConfig(id=1, embedding_model=model, embedding_revision=revision)
            self.db.add(row)
        row.embedding_backend = backend
        row.embedding_model = model
        row.embedding_revision = revision
        row.embedding_api_base = api_base
        # An omitted key keeps the stored secret so the frontend can
        # resubmit the form without re-typing it.
        if api_key:
            row.embedding_api_key = api_key
        row.embedding_dims = dims
        self.db.commit()
        return self.get_site_config()

    def protocol_config(self) -> dict:
        fixed = _fixed_config(StudyKind.formal, protocol_id=PROTOCOL_ID)
        revised = _fixed_config(
            StudyKind.formal, protocol_id=DEFAULT_PROTOCOL_ID
        )
        return {
            "protocol": revised["protocol"],
            "supported_protocols": [
                {
                    "protocol": fixed["protocol"],
                    "order_variants": fixed["order_variants"],
                    "conditions": fixed["conditions"],
                    "retrieval_top_k": fixed["retrieval_top_k"],
                    "memory_profile": fixed["memory_profile"],
                    "scoring_context": fixed["scoring_context"],
                    "historical": True,
                },
                {
                    "protocol": revised["protocol"],
                    "order_variants": revised["order_variants"],
                    "conditions": revised["conditions"],
                    "retrieval_top_k": revised["retrieval_top_k"],
                    "memory_profile": revised["memory_profile"],
                    "scoring_context": revised["scoring_context"],
                    "historical": False,
                },
            ],
            "tokenizer": fixed["tokenizer"],
            "codex_subprocess_limit": fixed["codex_subprocess_limit"],
            "framework_contract": fixed["framework_contract"],
            "official_frameworks": fixed["official_frameworks"],
            "artifact_root": fixed["artifact_root"],
        }

    def delete_project(self, study_id: str) -> None:
        study = self._study(study_id)
        if study.protocol_id == "saf-memory-framework-v2":
            raise MemoryStudyDomainError(
                "conflict", "退役的 v2 项目仅供只读审计，不允许删除"
            )
        if study.status == StudyStatus.running:
            raise MemoryStudyDomainError("conflict", "请先暂停或终止该项目后再删除")
        # CASCADE removes records/stores/calls/attempts/framework_invocations/
        # audit events and the report; then clean the artifact directory.
        self.db.delete(study)
        self.db.commit()
        shutil.rmtree(
            Path(settings.MEMORY_STUDY_ARTIFACT_ROOT) / study_id,
            ignore_errors=True,
        )

    def refresh_progress(self, study_id: str, *, persist: bool = False) -> dict:
        study = self._study(study_id)
        counts = {
            status: count
            for status, count in self.db.execute(
                select(MSCall.status, func.count(MSCall.id))
                .where(MSCall.study_id == study_id)
                .group_by(MSCall.status)
            ).all()
        }
        by_kind = {
            kind: count
            for kind, count in self.db.execute(
                select(MSCall.kind, func.count(MSCall.id))
                .where(MSCall.study_id == study_id)
                .group_by(MSCall.kind)
            ).all()
        }
        invocation_counts = {
            status: count
            for status, count in self.db.execute(
                select(
                    MSFrameworkInvocation.status, func.count(MSFrameworkInvocation.id)
                )
                .join(MSCall, MSCall.id == MSFrameworkInvocation.call_id)
                .where(MSCall.study_id == study_id)
                .group_by(MSFrameworkInvocation.status)
            ).all()
        }
        total = sum(counts.values())
        succeeded = counts.get("succeeded", 0)
        failed = counts.get("failed", 0)
        store_counts = {
            status: count
            for status, count in self.db.execute(
                select(MSMemoryStore.status, func.count(MSMemoryStore.id))
                .where(MSMemoryStore.study_id == study_id)
                .group_by(MSMemoryStore.status)
            ).all()
        }
        model_rows = self.db.execute(
            select(MSCall.model, MSCall.status, MSCall.kind, func.count(MSCall.id))
            .where(MSCall.study_id == study_id)
            .group_by(MSCall.model, MSCall.status, MSCall.kind)
        ).all()
        model_progress: dict[str, dict[str, object]] = {
            model: {
                "total": 0,
                "succeeded": 0,
                "failed": 0,
                "pending": 0,
                "leased": 0,
                "running": 0,
                "memory_write_total": 0,
                "memory_write_succeeded": 0,
                "score_total": 0,
                "score_succeeded": 0,
                # Explicit training/scoring aliases keep the API readable to
                # clients that do not need to know the persisted call-kind
                # spelling (``memory_write``).
                "training_total": 0,
                "training_succeeded": 0,
                "training_score_total": 0,
                "training_score_succeeded": 0,
                "training_failed": 0,
                "training_pending": 0,
                "scoring_total": 0,
                "scoring_succeeded": 0,
                "scoring_failed": 0,
                "scoring_pending": 0,
                "test_score_total": 0,
                "test_score_succeeded": 0,
                "phase": "training",
                "recent_throughput_per_minute": None,
                "eta_seconds": None,
            }
            for model in MODEL_IDS
        }
        for model, status, kind, count in model_rows:
            bucket = model_progress.setdefault(
                model,
                {
                    "total": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "pending": 0,
                    "leased": 0,
                    "running": 0,
                    "memory_write_total": 0,
                    "memory_write_succeeded": 0,
                    "score_total": 0,
                    "score_succeeded": 0,
                    "training_total": 0,
                    "training_succeeded": 0,
                    "training_score_total": 0,
                    "training_score_succeeded": 0,
                    "training_failed": 0,
                    "training_pending": 0,
                    "scoring_total": 0,
                    "scoring_succeeded": 0,
                    "scoring_failed": 0,
                    "scoring_pending": 0,
                    "test_score_total": 0,
                    "test_score_succeeded": 0,
                    "phase": "training",
                    "recent_throughput_per_minute": None,
                    "eta_seconds": None,
                },
            )
            count = int(count)
            bucket["total"] += count
            if status in bucket:
                bucket[status] += count
            kind_key = "memory_write" if kind == "memory_write" else "score"
            bucket[f"{kind_key}_total"] += count
            if status == "succeeded":
                bucket[f"{kind_key}_succeeded"] += count
            alias = "training" if kind == "memory_write" else "scoring"
            bucket[f"{alias}_total"] += count
            if status == "succeeded":
                bucket[f"{alias}_succeeded"] += count
            elif status == "failed":
                bucket[f"{alias}_failed"] += count
            elif status in {"pending", "leased", "running"}:
                bucket[f"{alias}_pending"] += count
        training_score_rows = self.db.execute(
            select(MSCall.model, MSCall.status, func.count(MSCall.id))
            .join(
                MSRecord,
                (MSRecord.study_id == MSCall.study_id)
                & (MSRecord.answer_id == MSCall.answer_id),
            )
            .where(
                MSCall.study_id == study_id,
                MSCall.kind == "score",
                MSRecord.selection_kind == "training",
            )
            .group_by(MSCall.model, MSCall.status)
        ).all()
        for model, status, count in training_score_rows:
            bucket = model_progress[model]
            bucket["training_score_total"] += int(count)
            if status == "succeeded":
                bucket["training_score_succeeded"] += int(count)
        for bucket in model_progress.values():
            bucket["test_score_total"] = int(bucket["scoring_total"]) - int(
                bucket["training_score_total"]
            )
            bucket["test_score_succeeded"] = int(bucket["scoring_succeeded"]) - int(
                bucket["training_score_succeeded"]
            )
        recent_latencies = [
            int(value)
            for value in self.db.scalars(
                select(MSCall.latency_ms)
                .where(
                    MSCall.study_id == study_id,
                    MSCall.status == "succeeded",
                    MSCall.latency_ms.is_not(None),
                )
                .order_by(MSCall.id.desc())
                .limit(100)
            )
            if value is not None
        ]
        mean_latency = (
            sum(recent_latencies) / len(recent_latencies) if recent_latencies else None
        )
        model_etas: list[float] = []
        for model, bucket in model_progress.items():
            model_latencies = [
                int(value)
                for value in self.db.scalars(
                    select(MSCall.latency_ms)
                    .where(
                        MSCall.study_id == study_id,
                        MSCall.model == model,
                        MSCall.status == "succeeded",
                        MSCall.latency_ms.is_not(None),
                    )
                    .order_by(MSCall.id.desc())
                    .limit(50)
                )
                if value is not None
            ]
            model_mean = (
                sum(model_latencies) / len(model_latencies)
                if model_latencies
                else mean_latency
            )
            throughput = 60_000 / model_mean if model_mean else None
            remaining_for_model = sum(
                int(bucket[status]) for status in ("pending", "leased", "running")
            )
            model_eta = (
                remaining_for_model * model_mean / 1000
                if model_mean is not None and remaining_for_model
                else None
            )
            bucket["recent_throughput_per_minute"] = throughput
            bucket["eta_seconds"] = int(model_eta) if model_eta is not None else None
            training_open = int(bucket["training_pending"]) > 0 or (
                int(bucket["training_total"]) > 0
                and int(bucket["training_succeeded"]) < int(bucket["training_total"])
            )
            scoring_open = int(bucket["scoring_pending"]) > 0
            bucket["phase"] = (
                "attention_required"
                if int(bucket["training_failed"]) or int(bucket["scoring_failed"])
                else (
                    "training"
                    if training_open
                    else "scoring" if scoring_open else "complete"
                )
            )
            if model_eta is not None:
                model_etas.append(model_eta)
        # Each configured model advances independently, so wall-clock ETA is
        # the slowest model's remaining queue, not total work divided by the
        # number of models.
        eta_seconds = int(max(model_etas)) if model_etas else None
        progress = {
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
            "pending": counts.get("pending", 0),
            "leased": counts.get("leased", 0),
            "running": counts.get("running", 0),
            "memory_write_total": by_kind.get("memory_write", 0),
            "score_total": by_kind.get("score", 0),
            "training_score_total": sum(
                int(bucket["training_score_total"])
                for bucket in model_progress.values()
            ),
            "training_score_succeeded": sum(
                int(bucket["training_score_succeeded"])
                for bucket in model_progress.values()
            ),
            "test_score_total": sum(
                int(bucket["test_score_total"]) for bucket in model_progress.values()
            ),
            "test_score_succeeded": sum(
                int(bucket["test_score_succeeded"])
                for bucket in model_progress.values()
            ),
            "score_succeeded": self._count_calls(
                study_id, kind="score", status="succeeded"
            ),
            "framework_invocation_total": sum(invocation_counts.values()),
            "framework_invocation_succeeded": invocation_counts.get("succeeded", 0),
            "framework_invocation_failed": invocation_counts.get("failed", 0),
            # Store-level counters make the training phase observable.  Each
            # model slot has its own scoring barrier, so the model buckets are
            # equally important when one model is slower than the other.
            "stores_total": sum(store_counts.values()),
            "stores_completed": store_counts.get("completed", 0),
            "stores_failed": store_counts.get("failed", 0),
            "stores_pending": store_counts.get("pending", 0),
            "models": model_progress,
            "recent_latency_ms": {
                "sample_size": len(recent_latencies),
                "mean": mean_latency,
            },
            "recent_throughput_per_minute": (
                60_000 / mean_latency if mean_latency else None
            ),
            "eta_seconds": eta_seconds,
        }
        if persist:
            study.progress_json = progress
            self.db.commit()
        return progress

    def list_memory_stores(self, study_id: str) -> list[dict]:
        self._study(study_id)
        return [
            self._store_out(row)
            for row in self.db.scalars(
                select(MSMemoryStore)
                .where(MSMemoryStore.study_id == study_id)
                .order_by(
                    MSMemoryStore.model,
                    MSMemoryStore.question_id,
                    MSMemoryStore.condition,
                    MSMemoryStore.order_variant,
                )
            )
        ]

    def inspect_memory(self, study_id: str, store_id: int) -> dict:
        self._study(study_id)
        store = self.db.scalar(
            select(MSMemoryStore).where(
                MSMemoryStore.id == store_id, MSMemoryStore.study_id == study_id
            )
        )
        if store is None:
            raise MemoryStudyDomainError("not_found", "memory store not found")
        calls = list(
            self.db.scalars(
                select(MSCall)
                .where(MSCall.memory_store_id == store.id)
                .order_by(MSCall.id)
            )
        )
        return {
            **self._store_out(store),
            "snapshot": store.snapshot_json,
            "snapshot_sha256": store.snapshot_sha256,
            "snapshot_artifact_sha256": store.snapshot_artifact_sha256,
            "framework_version": store.framework_version,
            "snapshot_stream_id": store.snapshot_stream_id,
            "snapshot_path": store.snapshot_path,
            "attempts": [
                {
                    "id": attempt.id,
                    "call_id": attempt.call_id,
                    "attempt_number": attempt.attempt_number,
                    "status": attempt.status,
                    "requested_model": attempt.requested_model,
                    "request_sha256": attempt.request_sha256,
                    "input_tokens": attempt.input_tokens,
                    "output_tokens": attempt.output_tokens,
                    "total_tokens": attempt.total_tokens,
                    "latency_ms": attempt.latency_ms,
                    "stderr_excerpt": attempt.stderr_excerpt,
                    "error_type": attempt.error_type,
                    "error_message": attempt.error_message,
                }
                for attempt in self.db.scalars(
                    select(MSCallAttempt)
                    .join(MSCall, MSCall.id == MSCallAttempt.call_id)
                    .where(MSCall.memory_store_id == store.id)
                    .order_by(MSCallAttempt.id)
                )
            ],
            "framework_invocations": [
                {
                    "id": invocation.id,
                    "call_id": invocation.call_id,
                    "attempt_number": invocation.attempt_number,
                    "sequence_number": invocation.sequence_number,
                    "framework": invocation.framework,
                    "phase": invocation.phase,
                    "transport": invocation.transport,
                    "requested_model": invocation.requested_model,
                    "status": invocation.status,
                    "request_sha256": invocation.request_sha256,
                    "response_sha256": invocation.response_sha256,
                    "input_tokens": invocation.input_tokens,
                    "output_tokens": invocation.output_tokens,
                    "total_tokens": invocation.total_tokens,
                    "latency_ms": invocation.latency_ms,
                    "error_type": invocation.error_type,
                    "error_message": invocation.error_message,
                }
                for invocation in self.db.scalars(
                    select(MSFrameworkInvocation)
                    .join(MSCall, MSCall.id == MSFrameworkInvocation.call_id)
                    .where(MSCall.memory_store_id == store.id)
                    .order_by(MSFrameworkInvocation.id)
                )
            ],
            "write_calls": [
                {
                    "id": call.id,
                    "answer_id": call.answer_id,
                    "status": call.status,
                    "input_tokens": call.input_tokens,
                    "output_tokens": call.output_tokens,
                    "total_tokens": call.total_tokens,
                    "latency_ms": call.latency_ms,
                    "memory_hash": call.memory_hash,
                    "failure_code": call.failure_code,
                }
                for call in calls
                if call.kind == "memory_write"
            ],
            "retrieval_basis": [
                {
                    "call_id": call.id,
                    "answer_id": call.answer_id,
                    "retrieval": call.retrieval_json,
                    "memory_hash": call.memory_hash,
                }
                for call in self.db.scalars(
                    select(MSCall).where(
                        MSCall.memory_store_id == store.id, MSCall.kind == "score"
                    )
                )
            ],
        }

    def _memory_store(self, study_id: str, store_id: int) -> MSMemoryStore:
        store = self.db.scalar(
            select(MSMemoryStore).where(
                MSMemoryStore.id == store_id, MSMemoryStore.study_id == study_id
            )
        )
        if store is None:
            raise MemoryStudyDomainError("not_found", "memory store not found")
        return store

    @staticmethod
    def _snapshot_file_payload(store: MSMemoryStore) -> dict | None:
        if not store.snapshot_path:
            return None
        path = Path(store.snapshot_path)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _snapshot_is_valid(store: MSMemoryStore) -> bool:
        """Prove that the last committed snapshot and native artifacts agree."""
        if store.snapshot_json is None:
            if store.committed_count != 0:
                return False
            if not store.snapshot_path:
                return True
            snapshot_path = Path(store.snapshot_path)
            if snapshot_path.exists():
                return False
            # An empty logical store must not hide a native sidecar left by a
            # crashed attempt. Requiring an empty stream directory turns that
            # state into an explicit rebuild instead of layering new writes on
            # unknown framework data.
            stream_root = snapshot_path.parent
            if stream_root.is_dir():
                try:
                    return not any(stream_root.iterdir())
                except OSError:
                    return False
            return True
        if not store.snapshot_path:
            return False
        payload = MemoryStudyService._snapshot_file_payload(store)
        if payload is None or memory_hash(payload) != store.snapshot_sha256:
            return False
        if payload != store.snapshot_json:
            return False
        artifact_hash = payload.get("artifact_sha256") or store.snapshot_artifact_sha256
        if artifact_hash:
            root = Path(store.snapshot_path).parent / "native-artifacts"
            if not root.is_dir() or _sha256_path(root) != artifact_hash:
                return False
        return True

    @staticmethod
    def _clear_store_artifacts(study: MSStudy, store: MSMemoryStore) -> None:
        """Delete only mutable files inside this study's validated store root."""
        if not store.snapshot_path:
            return
        root = Path(store.snapshot_path).resolve().parent
        allowed = (
            Path(settings.MEMORY_STUDY_ARTIFACT_ROOT).resolve() / study.id / "memory"
        ).resolve()
        if root == allowed or not root.is_relative_to(allowed):
            raise MemoryStudyDomainError(
                "protocol_drift",
                "memory store snapshot path is outside the study artifact root",
            )
        # Rebuild means a genuinely empty stream.  Remove the entire validated
        # store root rather than a hand-maintained list of framework filenames;
        # this also clears sidecars introduced by a pinned dependency revision.
        if root.is_dir():
            shutil.rmtree(root)
        elif root.exists():
            root.unlink()
        root.mkdir(parents=True, exist_ok=True)
        # Attempt/native staging directories are siblings of the store root.
        # They are safe to remove only after the parent has passed the same
        # artifact-root check.
        for pattern in (
            f"attempt-{store.id}-*",
            f"publish-{store.id}-*",
            f"native-{store.id}-*",
        ):
            for target in root.parent.glob(pattern):
                if target.is_dir():
                    shutil.rmtree(target)

    def _reset_memory_store(
        self, study: MSStudy, store: MSMemoryStore, *, clear_snapshot: bool
    ) -> int:
        calls = list(
            self.db.scalars(
                select(MSCall)
                .where(
                    MSCall.memory_store_id == store.id,
                    MSCall.kind == "memory_write",
                )
                .order_by(MSCall.id)
            )
        )
        for call in calls:
            call.status = "pending"
            call.worker_id = None
            call.slot_id = None
            call.lease_until = None
            call.request_json = None
            call.output_json = None
            call.retrieval_json = None
            call.memory_hash = None
            call.input_tokens = None
            call.output_tokens = None
            call.total_tokens = None
            call.latency_ms = None
            call.failure_code = None
            call.failure_summary = None
            call.completed_at = None
        # Scores produced against a snapshot that is being discarded are no
        # longer valid evidence.  Requeue them too; no result may survive a
        # destructive store rebuild.
        score_calls = list(
            self.db.scalars(
                select(MSCall).where(
                    MSCall.memory_store_id == store.id,
                    MSCall.kind == "score",
                )
            )
        )
        for call in score_calls:
            call.status = "pending"
            call.worker_id = None
            call.slot_id = None
            call.lease_until = None
            call.request_json = None
            call.retrieval_json = None
            call.output_json = None
            call.memory_hash = None
            call.input_tokens = None
            call.output_tokens = None
            call.total_tokens = None
            call.latency_ms = None
            call.manual_score = None
            call.model_score = None
            call.signed_diff = None
            call.absolute_diff = None
            call.normalized_signed_diff = None
            call.normalized_absolute_diff = None
            call.failure_code = None
            call.failure_summary = None
            call.completed_at = None
        if clear_snapshot:
            self._clear_store_artifacts(study, store)
            store.snapshot_json = None
            store.snapshot_sha256 = None
            store.snapshot_artifact_sha256 = None
            store.framework_version = None
            store.snapshot_stream_id = None
            store.last_call_id = None
            store.committed_count = 0
            store.history_count = 0
        store.status = "pending"
        store.error_summary = None
        return len(calls) + len(score_calls)

    def recover_memory_store(self, study_id: str, store_id: int) -> dict:
        """Resume a failed store from its last valid commit or rebuild it.

        A valid snapshot keeps successful ledger rows and only requeues failed
        writes.  If the snapshot/file/native-artifact hashes cannot be proven,
        every write is reset and the mutable store directory is rebuilt from
        scratch; no residual state is ever layered on top.
        """
        study = self._study(study_id)
        if study.status in {StudyStatus.completed, StudyStatus.terminated}:
            raise MemoryStudyDomainError(
                "conflict", "terminal studies cannot recover a memory store"
            )
        if study.status == StudyStatus.running:
            raise MemoryStudyDomainError("conflict", "请先暂停研究后恢复 memory store")
        store = self._memory_store(study_id, store_id)
        if store.framework == "retrieval":
            raise MemoryStudyDomainError(
                "conflict", "deterministic retrieval stores do not need recovery"
            )
        valid = self._snapshot_is_valid(store)
        if valid:
            failed = list(
                self.db.scalars(
                    select(MSCall).where(
                        MSCall.memory_store_id == store.id,
                        MSCall.kind == "memory_write",
                        MSCall.status == "failed",
                    )
                )
            )
            for call in failed:
                call.status = "pending"
                call.worker_id = None
                call.slot_id = None
                call.lease_until = None
                call.failure_code = None
                call.failure_summary = None
            self.db.flush()
            recompute_store_progress(self.db, store)
            requeued = len(failed)
            rebuilt = False
        else:
            requeued = self._reset_memory_store(study, store, clear_snapshot=True)
            rebuilt = True
        study.error_summary = None
        if study.status == StudyStatus.attention_required:
            study.status = StudyStatus.paused
        self.db.add(
            MSAuditEvent(
                study_id=study.id,
                event_type="memory_store_recovered",
                status="rebuilt" if rebuilt else "resumed",
                detail_json={
                    "store_id": store.id,
                    "snapshot_valid": valid,
                    "requeued": requeued,
                },
            )
        )
        self.db.commit()
        self.refresh_progress(study_id)
        return {"store_id": store.id, "requeued": requeued, "rebuilt": rebuilt}

    def list_calls(
        self,
        study_id: str,
        *,
        kind: str | None = None,
        status: str | None = None,
        question_id: str | None = None,
        model: str | None = None,
        condition: str | None = None,
        feedback_mode: str | None = None,
        cursor: int | None = None,
        limit: int = 100,
    ) -> dict:
        """Return one bounded, id-cursor page without materialising the queue."""
        study = self._study(study_id)
        query = select(MSCall).where(MSCall.study_id == study_id).order_by(MSCall.id)
        if kind:
            query = query.where(MSCall.kind == kind)
        if status:
            query = query.where(MSCall.status == status)
        for column, value in (
            (MSCall.question_id, question_id),
            (MSCall.model, model),
            (MSCall.condition, condition),
            (MSCall.feedback_mode, feedback_mode),
        ):
            if value:
                query = query.where(column == value)
        if cursor is not None:
            query = query.where(MSCall.id > cursor)
        bounded_limit = max(1, min(int(limit), 1_000))
        rows = list(self.db.scalars(query.limit(bounded_limit + 1)))
        has_next = len(rows) > bounded_limit
        rows = rows[:bounded_limit]
        total_query = select(func.count(MSCall.id)).where(MSCall.study_id == study_id)
        if kind:
            total_query = total_query.where(MSCall.kind == kind)
        if status:
            total_query = total_query.where(MSCall.status == status)
        for column, value in (
            (MSCall.question_id, question_id),
            (MSCall.model, model),
            (MSCall.condition, condition),
            (MSCall.feedback_mode, feedback_mode),
        ):
            if value:
                total_query = total_query.where(column == value)
        total = int(self.db.scalar(total_query) or 0)
        return {
            "items": [
                self._call_out(row, expose_scores=not study.results_embargoed)
                for row in rows
            ],
            "next_cursor": rows[-1].id if has_next and rows else None,
            "total": total,
        }

    def retry_call(
        self, study_id: str, call_id: int, *, expected_question_id: str | None = None
    ) -> dict:
        study = self._study(study_id)
        if study.status in {StudyStatus.completed, StudyStatus.terminated}:
            raise MemoryStudyDomainError(
                "conflict", "terminal studies cannot retry calls"
            )
        call = self.db.scalar(
            select(MSCall).where(MSCall.id == call_id, MSCall.study_id == study_id)
        )
        if call is None:
            raise MemoryStudyDomainError("not_found", "call not found")
        if (
            expected_question_id is not None
            and call.question_id != expected_question_id
        ):
            raise MemoryStudyDomainError(
                "not_found", "call not found in question shard"
            )
        if call.status != "failed":
            raise MemoryStudyDomainError(
                "conflict", "only failed calls can be manually retried"
            )
        earlier_blocking = self.db.scalar(
            select(MSCall)
            .where(
                MSCall.study_id == study_id,
                MSCall.model == call.model,
                MSCall.question_id == call.question_id,
                MSCall.condition == call.condition,
                MSCall.order_variant == call.order_variant,
                MSCall.id < call.id,
                MSCall.status.not_in(["succeeded", "cancelled"]),
            )
            .order_by(MSCall.id)
            .limit(1)
        )
        if earlier_blocking is not None:
            action = (
                "must be retried before"
                if earlier_blocking.status == "failed"
                else "must be completed before"
            )
            raise MemoryStudyDomainError(
                "conflict",
                f"call {earlier_blocking.id} {action} call {call.id}",
            )
        if call.memory_store_id is not None and call.condition != "no_memory":
            store = self.db.get(MSMemoryStore, call.memory_store_id)
            if store is not None and not self._snapshot_is_valid(store):
                raise MemoryStudyDomainError(
                    "conflict",
                    "memory store snapshot cannot be verified; recover the store before retrying",
                )
        call.status = "pending"
        call.failure_code = None
        call.failure_summary = None
        call.lease_until = None
        if call.memory_store_id is not None:
            store = self.db.get(MSMemoryStore, call.memory_store_id)
            if store is not None:
                # Re-derive the store verdict instead of forcing "pending": the
                # store may still own other failed writes that this single
                # retry did not requeue, and it must stay failed until they are
                # requeued too.  autoflush is off, so flush the requeued call
                # before counting.
                self.db.flush()
                recompute_store_progress(self.db, store)
                if store.status != "failed":
                    store.error_summary = None
        if (
            study.status == StudyStatus.attention_required
            and self._count_calls(study_id, status="failed") == 0
        ):
            study.error_summary = None
        shard = self.db.scalar(
            select(MSQuestionRun).where(
                MSQuestionRun.study_id == study_id,
                MSQuestionRun.question_id == call.question_id,
            )
        )
        if shard is not None:
            shard.status = (
                "running" if study.status == StudyStatus.running else "paused"
            )
            shard.error_summary = None
        self.db.commit()
        return self._call_out(call)

    def retry_all_failed(self, study_id: str, question_id: str | None = None) -> dict:
        """Requeue every failed call of one study and resume the run."""
        study = self._study(study_id)
        if study.status in {StudyStatus.completed, StudyStatus.terminated}:
            raise MemoryStudyDomainError(
                "conflict", "terminal studies cannot retry calls"
            )
        if study.status == "running":
            raise MemoryStudyDomainError(
                "conflict", "cannot requeue calls while the worker is running"
            )
        calls = list(
            self.db.scalars(
                select(MSCall).where(
                    MSCall.study_id == study_id,
                    MSCall.status == "failed",
                    *([MSCall.question_id == question_id] if question_id else []),
                )
            )
        )
        store_ids: set[int] = set()
        for call in calls:
            call.status = "pending"
            call.failure_code = None
            call.failure_summary = None
            call.lease_until = None
            if call.memory_store_id is not None:
                store_ids.add(call.memory_store_id)
        # Flush the requeues first: the recompute below counts the ledger and
        # ``SessionLocal`` runs with autoflush disabled.
        self.db.flush()
        rebuilt_stores: list[int] = []
        requeued = len(calls)
        for store_id in store_ids:
            store = self.db.get(MSMemoryStore, store_id)
            if store is not None:
                if self._snapshot_is_valid(store):
                    self.db.flush()
                    recompute_store_progress(self.db, store)
                    if store.status != "failed":
                        store.error_summary = None
                else:
                    requeued += self._reset_memory_store(
                        study, store, clear_snapshot=True
                    ) - sum(1 for call in calls if call.memory_store_id == store_id)
                    rebuilt_stores.append(store_id)
                    self.db.add(
                        MSAuditEvent(
                            study_id=study.id,
                            event_type="memory_store_rebuilt",
                            status="rebuilt",
                            detail_json={
                                "store_id": store_id,
                                "snapshot_valid": False,
                                "requeued_all_writes": True,
                            },
                        )
                    )
        study.error_summary = None
        if question_id:
            shard = self._question_run(study_id, question_id)
            shard.status = "paused"
            shard.error_summary = None
        if study.status == "attention_required":
            study.status = "paused"
        self.db.commit()
        self.refresh_progress(study_id)
        return {"requeued": requeued, "rebuilt_stores": rebuilt_stores}

    def audit_project(self, study_id: str) -> dict:
        study = self._study(study_id)
        progress = self.refresh_progress(study_id)
        failed = self._count_calls(study_id, status="failed")
        pending = (
            self._count_calls(study_id, status="pending")
            + self._count_calls(study_id, status="leased")
            + self._count_calls(study_id, status="running")
        )
        question_runs = list(
            self.db.scalars(
                select(MSQuestionRun).where(MSQuestionRun.study_id == study_id)
            )
        )
        # A current protocol project must remain Luna-only even if a malformed row
        # is introduced outside the creation API. Legacy v2 projects retain
        # their historical model set and remain readable without being judged
        # by the new protocol checks.
        current_protocol = study.protocol_id in SUPPORTED_PROTOCOL_IDS
        configured_models = tuple(
            str(item.get("model"))
            for item in (study.config_json.get("models") or [])
            if isinstance(item, dict) and item.get("model")
        )
        runtime_models = tuple(
            str(model) for model in (study.config_json.get("runtime_snapshot") or {})
        )
        observed_call_models = {
            str(model)
            for model in self.db.scalars(
                select(MSCall.model).where(MSCall.study_id == study_id).distinct()
            )
        }
        observed_store_models = {
            str(model)
            for model in self.db.scalars(
                select(MSMemoryStore.model)
                .where(MSMemoryStore.study_id == study_id)
                .distinct()
            )
        }
        luna_binding_checks = {
            "config_models_luna_only": not current_protocol
            or configured_models == tuple(MODEL_IDS),
            "runtime_snapshot_luna_only": not current_protocol
            or runtime_models == tuple(MODEL_IDS),
            "calls_luna_only": not current_protocol
            or observed_call_models <= set(MODEL_IDS),
            "stores_luna_only": not current_protocol
            or observed_store_models <= set(MODEL_IDS),
        }
        checks = {
            "preflight_passed": (study.preflight_json or {}).get("status") == "passed",
            "all_calls_terminal": pending == 0,
            "all_question_shards_completed": (
                not question_runs
                or all(row.status == "completed" for row in question_runs)
            ),
            "no_failed_calls": failed == 0,
            "all_memory_stores_completed": (
                self._count_memory_stores(study_id, status="completed")
                == self._count_memory_stores(study_id)
                and self._count_memory_stores(study_id) > 0
            ),
            "strict_serial_execution": self._strict_serial_issue(study_id) is None,
            "no_failed_framework_invocations": self.db.scalar(
                select(func.count(MSFrameworkInvocation.id))
                .join(MSCall, MSCall.id == MSFrameworkInvocation.call_id)
                .where(
                    MSCall.study_id == study_id,
                    MSFrameworkInvocation.status == "failed",
                )
            )
            == 0,
            "protocol_score_call_count": self._count_calls(study_id, kind="score")
            == study.expected_json.get("score_calls"),
            "protocol_framework_write_count": self._count_calls(
                study_id, kind="memory_write"
            )
            == study.expected_json.get("memory_write_calls"),
            "all_score_diffs_present": self._score_diffs_complete(study_id),
            "test_not_written_back": self._test_not_in_memory(study_id),
            "config_has_no_token_limit": study.config_json.get("token_limit") is None,
            "config_has_no_checkpoints": study.config_json.get("checkpoints") == [],
            "config_has_no_bootstrap": study.config_json.get("bootstrap") is False,
            **luna_binding_checks,
        }
        passed = all(checks.values()) and progress["score_succeeded"] > 0
        study.integrity_status = "passed" if passed else "pending"
        study.integrity_json = {
            "checks": checks,
            "scoring_context": study.config_json.get("scoring_context"),
            "audited_at": utc_now_naive().isoformat(),
        }
        if passed and study.status not in {
            StudyStatus.terminated,
            StudyStatus.attention_required,
        }:
            study.status = StudyStatus.completed
            study.completed_at = utc_now_naive()
            study.results_embargoed = False
            self._ensure_report(study)
        elif failed:
            study.status = StudyStatus.attention_required
            study.error_summary = "one or more calls failed; manual retry required"
        self.db.add(
            MSAuditEvent(
                study_id=study_id,
                event_type="integrity_audit",
                status="passed" if passed else "pending",
                detail_json={"checks": checks},
            )
        )
        self.db.commit()
        return {
            "study_id": study_id,
            "status": study.integrity_status,
            "checks": checks,
            "progress": self.refresh_progress(study_id),
        }

    def report(self, study_id: str) -> dict:
        study = self._study(study_id)
        if study.results_embargoed or study.integrity_status != "passed":
            return {
                "study_id": study_id,
                "results_embargoed": True,
                "status": study.status,
                "reason": "正式完整性审计尚未通过",
                "report": None,
            }
        report = self._ensure_report(study)
        return {
            "study_id": study_id,
            "results_embargoed": False,
            "status": study.status,
            "report_sha256": report.report_sha256,
            "report": report.report_json,
        }

    def export(self, study_id: str, kind: str) -> tuple[str, str, str]:
        study = self._study(study_id)
        if kind == "report":
            payload = self.report(study_id)
            return (
                "application/json",
                f"{study.name}-report.json",
                json.dumps(payload, ensure_ascii=False, indent=2),
            )
        if kind not in {"results", "manifest", "html"}:
            raise MemoryStudyDomainError(
                "validation", "export kind must be results, manifest, or html"
            )
        if kind == "manifest":
            return (
                "application/json",
                f"{study.name}-manifest.json",
                json.dumps(
                    {
                        "protocol": study.protocol_id,
                        "data": study.data_manifest_json,
                        "config": _redact_secrets(study.config_json),
                        "expected": study.expected_json,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        if study.results_embargoed:
            raise MemoryStudyDomainError(
                "embargoed", "results remain embargoed until the integrity audit passes"
            )
        record_split = {
            row.answer_id: ("train" if row.selection_kind == "training" else "test")
            for row in self.db.scalars(
                select(MSRecord).where(MSRecord.study_id == study_id)
            )
        }
        frozen_orders = (study.data_manifest_json or {}).get("orders") or {}
        train_steps = {
            (str(question_id), str(order_variant), str(answer_id)): index
            for question_id, variants in frozen_orders.items()
            for order_variant, answer_ids in variants.items()
            if str(order_variant) not in {"training", "test"}
            for index, answer_id in enumerate(answer_ids, start=1)
        }
        rows = [
            self._call_out(row, expose_scores=True)
            for row in self.db.scalars(
                select(MSCall)
                .where(
                    MSCall.study_id == study_id,
                    MSCall.kind == "score",
                    MSCall.status == "succeeded",
                )
                .order_by(MSCall.id)
            )
        ]
        for row in rows:
            row["split"] = record_split.get(str(row["answer_id"]), "test")
            row["train_step"] = train_steps.get(
                (
                    str(row["question_id"]),
                    str(row["order_variant"]),
                    str(row["answer_id"]),
                )
            )
            row["memory_items_before"] = (
                0
                if row["split"] == "train" and row["condition"] == "no_memory"
                else (
                    int(row["train_step"]) - 1
                    if row["split"] == "train" and row["train_step"] is not None
                    else None
                )
            )
        if kind == "results":
            output = io.StringIO()
            fields = [
                "call_id",
                "model",
                "question_id",
                "answer_id",
                "split",
                "train_step",
                "memory_items_before",
                "condition",
                "feedback_mode",
                "order_variant",
                "repeat",
                "manual_score",
                "model_score",
                "signed_diff",
                "absolute_diff",
                "normalized_signed_diff",
                "normalized_absolute_diff",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "latency_ms",
                "memory_hash",
            ]
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(
                {field: row.get(field) for field in fields} for row in rows
            )
            return "text/csv", f"{study.name}-results.csv", output.getvalue()
        report = self.report(study_id)["report"] or {}
        html = "<html><head><meta charset='utf-8'><title>SAF memory study</title></head><body>"
        html += f"<h1>{study.name}</h1><pre>{json.dumps(report, ensure_ascii=False, indent=2)}</pre></body></html>"
        return "text/html; charset=utf-8", f"{study.name}-report.html", html

    def _progress_stall(self, now) -> dict:
        """Diagnose whether any running study is making no visible progress.

        A stall is reported only when a study is ``running`` and has pending
        calls, yet no call has completed within the threshold.  The reason
        names what the slots are actually waiting on so the operator does not
        have to reverse-engineer queue gates from raw counters.
        """
        running_ids = list(
            self.db.scalars(
                select(MSStudy.id).where(MSStudy.status == StudyStatus.running)
            )
        )
        if not running_ids:
            return {"stalled": False, "stalled_seconds": None, "blocked_reason": None}
        pending_filter = MSCall.status == "pending"
        for study_id in running_ids:
            calls = self.db.scalars(
                select(MSCall).where(MSCall.study_id == study_id, pending_filter)
            )
            pending_rows = list(calls)
            if not pending_rows:
                continue
            last_completed = self.db.scalar(
                select(func.max(MSCall.completed_at)).where(MSCall.study_id == study_id)
            )
            stalled_seconds = (
                int((now - last_completed).total_seconds())
                if last_completed is not None
                else None
            )
            if (
                stalled_seconds is not None
                and stalled_seconds < STALL_THRESHOLD_SECONDS
            ):
                continue
            waiting_backoff = sum(
                1
                for row in pending_rows
                if row.retry_after is not None and row.retry_after > now
            )
            if waiting_backoff and waiting_backoff == len(pending_rows):
                return {
                    "stalled": True,
                    "stalled_seconds": stalled_seconds,
                    "blocked_reason": (
                        f"{waiting_backoff} call(s) waiting on transient-failure "
                        "backoff; slots will resume automatically"
                    ),
                }
            blocked_failed = self._count_calls(study_id, status="failed")
            if blocked_failed:
                return {
                    "stalled": True,
                    "stalled_seconds": stalled_seconds,
                    "blocked_reason": (
                        f"{blocked_failed} call(s) failed and need manual retry "
                        "before their streams can continue"
                    ),
                }
            waiting_full_wave = sum(
                1 for row in pending_rows if str(row.condition).endswith("_no_feedback")
            )
            if waiting_full_wave and waiting_full_wave < len(pending_rows):
                # no_feedback 等待同分片 full 波次：设计内的正常串行等待。
                return {
                    "stalled": False,
                    "stalled_seconds": stalled_seconds,
                    "blocked_reason": (
                        "no-feedback wave waits for the full-feedback wave "
                        "of its question shard"
                    ),
                }
            return {
                "stalled": True,
                "stalled_seconds": stalled_seconds,
                "blocked_reason": (
                    f"{len(pending_rows)} call(s) pending with no completion in "
                    f"{STALL_THRESHOLD_SECONDS}s; check slots and worker logs"
                ),
            }
        return {"stalled": False, "stalled_seconds": None, "blocked_reason": None}

    def runtime_status(self) -> dict:
        row = self.db.get(MSSchedulerRuntime, 1)
        now = utc_now_naive()
        lease_live = bool(
            row is not None
            and row.lease_until is not None
            and row.lease_until >= now
            and row.status in {"online", "preflight"}
        )
        active = row.active_subprocesses if lease_live and row is not None else 0
        online = lease_live
        stall = self._progress_stall(now)
        running_studies = int(
            self.db.scalar(
                select(func.count(MSStudy.id)).where(
                    MSStudy.status == StudyStatus.running
                )
            )
            or 0
        )
        if online:
            state = "online"
            reason: str | None = None
            blocked_reason = stall["blocked_reason"]
        elif row is not None and row.status == "offline" and row.owner_id:
            # The worker exited without a lease (released on a clean stop);
            # distinguish "never started" from "started, currently idle".
            state = "idle"
            reason = "worker is idle; no study is running"
            blocked_reason = None
        elif (
            row is not None
            and row.status in {"online", "preflight"}
            and running_studies
        ):
            # A crash left the lease row stale (the worker never released it)
            # while a study is still marked running: the OS-level supervision
            # loop restarts the executor and adopts the study within seconds,
            # so surface the self-healing state instead of a bare failure.
            state = "recovering"
            reason = "executor is restarting; the study will resume automatically"
            blocked_reason = None
        else:
            state = "offline"
            reason = "memory-study worker is not connected"
            blocked_reason = None
        return {
            "online": online,
            "state": state,
            "stalled_seconds": stall["stalled_seconds"],
            "stalled": stall["stalled"],
            "status": row.status if online and row is not None else "offline",
            "global_subprocess_limit": (
                row.max_subprocesses if row is not None else MAX_CODEX_SUBPROCESSES
            ),
            "active_subprocesses": active,
            "slots": row.slots_json if row is not None else {},
            "worker_id": row.owner_id if row is not None else None,
            "last_heartbeat_at": (
                row.heartbeat_at.isoformat()
                if row is not None and row.heartbeat_at
                else None
            ),
            "reason": reason,
            "blocked_reason": blocked_reason,
        }

    def _study(self, study_id: str) -> MSStudy:
        row = self.db.get(MSStudy, study_id)
        if row is None:
            raise MemoryStudyDomainError("not_found", "memory-study project not found")
        return row

    def _count_calls(
        self,
        study_id: str,
        *,
        kind: str | None = None,
        status: str | None = None,
        question_id: str | None = None,
    ) -> int:
        query = select(func.count(MSCall.id)).where(MSCall.study_id == study_id)
        if kind:
            query = query.where(MSCall.kind == kind)
        if status:
            query = query.where(MSCall.status == status)
        if question_id:
            query = query.where(MSCall.question_id == question_id)
        return int(self.db.scalar(query) or 0)

    def _count_memory_stores(self, study_id: str, *, status: str | None = None) -> int:
        query = select(func.count(MSMemoryStore.id)).where(
            MSMemoryStore.study_id == study_id
        )
        if status:
            query = query.where(MSMemoryStore.status == status)
        return int(self.db.scalar(query) or 0)

    def _strict_serial_issue(self, study_id: str) -> str | None:
        """Return a historical per-model ordering violation, if recorded.

        Four condition slots are intentional in the current protocol, but a
        model may only ever be served by its own slot. Older one-slot rows
        remain valid as long as each model still has at most one distinct owner.
        """
        owners: dict[tuple[str, str, str, str], set[str]] = {}
        for model, question_id, condition, order_variant, slot_id in self.db.execute(
            select(
                MSCall.model,
                MSCall.question_id,
                MSCall.condition,
                MSCall.order_variant,
                MSCall.slot_id,
            ).where(
                MSCall.study_id == study_id,
                MSCall.slot_id.is_not(None),
            )
        ):
            key = (str(model), str(question_id), str(condition), str(order_variant))
            owners.setdefault(key, set()).add(str(slot_id))
        violating = [key for key, slots in owners.items() if len(slots) > 1]
        if violating:
            return (
                "study contains a memory stream executed by multiple slots "
                f"({', '.join(':'.join(key) for key in violating)}); create a new study instead"
            )
        return None

    def _score_diffs_complete(self, study_id: str) -> bool:
        return self._count_calls(
            study_id, kind="score", status="succeeded"
        ) == self._count_calls(study_id, kind="score") and all(
            call.normalized_absolute_diff is not None
            for call in self.db.scalars(
                select(MSCall).where(
                    MSCall.study_id == study_id,
                    MSCall.kind == "score",
                    MSCall.status == "succeeded",
                )
            )
        )

    def _test_not_in_memory(self, study_id: str) -> bool:
        test_ids = {
            row.answer_id
            for row in self.db.scalars(
                select(MSRecord).where(
                    MSRecord.study_id == study_id, MSRecord.selection_kind == "test"
                )
            )
        }
        if not test_ids:
            return True
        for store in self.db.scalars(
            select(MSMemoryStore).where(MSMemoryStore.study_id == study_id)
        ):
            snapshot = store.snapshot_json or {}
            if any(
                isinstance(item, dict) and item.get("answer_id") in test_ids
                for item in snapshot.get("items", [])
            ):
                return False
            if any(
                str(item) in test_ids for item in snapshot.get("history_answer_ids", [])
            ):
                return False
        return True

    def _ensure_report(self, study: MSStudy) -> MSReport:
        existing = self.db.scalar(select(MSReport).where(MSReport.study_id == study.id))
        if (
            existing is not None
            and (existing.report_json or {}).get("analysis_method", {}).get(
                "metric_schema"
            )
            == "memory-effect-v2"
        ):
            return existing
        record_split = {
            row.answer_id: ("train" if row.selection_kind == "training" else "test")
            for row in self.db.scalars(
                select(MSRecord).where(MSRecord.study_id == study.id)
            )
        }
        score_floors = (study.data_manifest_json or {}).get("score_floors") or {}
        frozen_orders = (study.data_manifest_json or {}).get("orders") or {}
        train_steps = {
            (str(question_id), str(order_variant), str(answer_id)): index
            for question_id, variants in frozen_orders.items()
            for order_variant, answer_ids in variants.items()
            if str(order_variant) not in {"training", "test"}
            for index, answer_id in enumerate(answer_ids, start=1)
        }
        rows = [
            {
                "call_id": call.id,
                "model": call.model,
                "question_id": call.question_id,
                "answer_id": call.answer_id,
                "condition": call.condition,
                "feedback_mode": call.feedback_mode,
                "order_variant": call.order_variant,
                "repeat": call.repeat,
                "model_score": call.model_score,
                "teacher_score": call.manual_score,
                "max_score": call.max_score,
                "score_floor": score_floors.get(call.question_id, 0.0),
                "split": record_split.get(call.answer_id, "test"),
                "train_step": train_steps.get(
                    (call.question_id, call.order_variant, call.answer_id)
                ),
            }
            for call in self.db.scalars(
                select(MSCall).where(
                    MSCall.study_id == study.id,
                    MSCall.kind == "score",
                    MSCall.status == "succeeded",
                )
            )
        ]
        failures = [
            {
                "call_id": call.id,
                "model": call.model,
                "question_id": call.question_id,
                "condition": call.condition,
                "answer_id": call.answer_id,
                "split": record_split.get(call.answer_id, "test"),
                "train_step": train_steps.get(
                    (call.question_id, call.order_variant, call.answer_id)
                ),
                "failure_code": call.failure_code,
                "failure_summary": call.failure_summary,
            }
            for call in self.db.scalars(
                select(MSCall).where(
                    MSCall.study_id == study.id, MSCall.status == "failed"
                )
            )
        ]
        latencies = [
            call.latency_ms
            for call in self.db.scalars(
                select(MSCall).where(MSCall.study_id == study.id)
            )
            if call.latency_ms is not None
        ]
        configured_models = [
            item
            for item in (study.config_json.get("models") or [])
            if isinstance(item, dict) and item.get("model")
        ]
        configured_limit = study.config_json.get("codex_subprocess_limit")
        try:
            active_slots = int(configured_limit)
        except (TypeError, ValueError):
            active_slots = max(1, len(configured_models)) * 4
        if active_slots < 1:
            active_slots = max(1, len(configured_models)) * 4
        resources = {
            "latency_ms": {
                "count": len(latencies),
                "mean": sum(latencies) / len(latencies) if latencies else None,
                "min": min(latencies) if latencies else None,
                "max": max(latencies) if latencies else None,
            },
            "input_tokens": self._sum_tokens(study.id, "input_tokens"),
            "output_tokens": self._sum_tokens(study.id, "output_tokens"),
            "total_tokens": self._sum_tokens(study.id, "total_tokens"),
            "active_slots": active_slots,
        }
        report_json = compute_report(
            rows,
            failures=failures,
            resources=resources,
            protocol_id=study.protocol_id,
        )
        if existing is None:
            report = MSReport(
                study_id=study.id,
                report_json=report_json,
                report_sha256=report_json["report_sha256"],
            )
            self.db.add(report)
        else:
            existing.report_json = report_json
            existing.report_sha256 = report_json["report_sha256"]
            report = existing
        study.error_summary = None
        return report

    def _sum_tokens(self, study_id: str, field: str) -> int:
        column = getattr(MSCall, field)
        return int(
            self.db.scalar(
                select(func.coalesce(func.sum(column), 0)).where(
                    MSCall.study_id == study_id
                )
            )
            or 0
        )

    def _study_out(self, study: MSStudy) -> dict:
        progress = self.refresh_progress(study.id)
        question_runs = [
            {
                "id": row.id,
                "question_id": row.question_id,
                "status": row.status,
                "error_summary": row.error_summary,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "completed_at": (
                    row.completed_at.isoformat() if row.completed_at else None
                ),
            }
            for row in self.db.scalars(
                select(MSQuestionRun)
                .where(MSQuestionRun.study_id == study.id)
                .order_by(MSQuestionRun.question_id)
            )
        ]
        return {
            "id": study.id,
            "protocol_id": study.protocol_id,
            "name": study.name,
            "kind": study.kind,
            "status": study.status,
            "integrity_status": study.integrity_status,
            "results_embargoed": study.results_embargoed,
            "data_manifest_sha256": study.data_manifest_json.get("manifest_sha256"),
            "config_sha256": _sha(study.config_json),
            "config": {
                # Expose the exact runtime captured for this project.  A site
                # runner edit therefore cannot make the UI suggest different
                # parameters from the worker's immutable snapshot.
                "models": list(
                    (study.config_json.get("runtime_snapshot") or {}).values()
                )
                or study.config_json.get("models", []),
                "conditions": study.config_json.get("conditions", []),
                "order_variants": study.config_json.get("order_variants", []),
                "repeats": study.config_json.get("repeats", []),
                "retrieval_top_k": study.config_json.get("retrieval_top_k"),
                "embedding_model": study.config_json.get("embedding_model"),
                "embedding_revision": study.config_json.get("embedding_revision"),
            },
            "expected": study.expected_json,
            "progress": progress,
            "questions": question_runs,
            "preflight": self._preflight_view(study),
            "integrity": study.integrity_json,
            "error_summary": study.error_summary,
            "created_at": study.created_at.isoformat() if study.created_at else None,
            "frozen_at": study.frozen_at.isoformat() if study.frozen_at else None,
            "started_at": study.started_at.isoformat() if study.started_at else None,
            "completed_at": (
                study.completed_at.isoformat() if study.completed_at else None
            ),
        }

    @staticmethod
    def _preflight_view(study: MSStudy) -> dict:
        """Expose a structural stale state without running a CLI on every GET.

        A changed frozen JSON snapshot is cheap to detect and should immediately
        reopen the preflight gate in the UI.  CLI identity drift is checked by
        ``start``/``resume`` (where invoking ``codex --version`` is deliberate)
        and persisted through ``_mark_preflight_stale``; ordinary polling never
        launches subprocesses merely to render a project card.
        """
        evidence = dict(study.preflight_json or {})
        if not evidence:
            return {"status": "required"}
        if evidence.get("status") == "passed":
            expected = evidence.get("config_sha256")
            current = _sha(study.config_json)
            if expected != current:
                evidence["status"] = "stale"
                evidence["invalid_reason"] = "冻结配置已变化，必须重新运行模型预检"
        return evidence

    def _mark_preflight_stale(self, study: MSStudy, reason: str | None) -> None:
        """Persist why a previously passing preflight can no longer be reused."""
        evidence = dict(study.preflight_json or {})
        if evidence.get("status") == "passed":
            evidence["status"] = "stale"
        if reason:
            evidence["invalid_reason"] = _redact_text(reason, 2_000)
        if evidence:
            study.preflight_json = evidence
            self.db.commit()

    @staticmethod
    def _store_out(store: MSMemoryStore) -> dict:
        return {
            "id": store.id,
            "model": store.model,
            "question_id": store.question_id,
            "framework": store.framework,
            "condition": store.condition,
            "feedback_mode": store.feedback_mode,
            "order_variant": store.order_variant,
            "status": store.status,
            "committed_count": store.committed_count,
            "history_count": store.history_count,
            "snapshot_sha256": store.snapshot_sha256,
            "snapshot_artifact_sha256": store.snapshot_artifact_sha256,
            "framework_version": store.framework_version,
            "snapshot_stream_id": store.snapshot_stream_id,
            "error_summary": store.error_summary,
            "updated_at": store.updated_at.isoformat() if store.updated_at else None,
        }

    @staticmethod
    def _call_out(call: MSCall, *, expose_scores: bool = True) -> dict:
        return {
            "id": call.id,
            "model": call.model,
            "question_id": call.question_id,
            "answer_id": call.answer_id,
            "condition": call.condition,
            "framework": call.framework,
            "feedback_mode": call.feedback_mode,
            "order_variant": call.order_variant,
            "kind": call.kind,
            "repeat": call.repeat,
            "status": call.status,
            "attempt_count": call.attempt_count,
            "input_tokens": call.input_tokens,
            "output_tokens": call.output_tokens,
            "total_tokens": call.total_tokens,
            "latency_ms": call.latency_ms,
            "memory_hash": call.memory_hash,
            "retrieval": call.retrieval_json,
            "manual_score": (
                call.manual_score if call.kind == "score" and expose_scores else None
            ),
            "model_score": (
                call.model_score if call.kind == "score" and expose_scores else None
            ),
            "signed_diff": (
                call.signed_diff if call.kind == "score" and expose_scores else None
            ),
            "absolute_diff": (
                call.absolute_diff if call.kind == "score" and expose_scores else None
            ),
            "normalized_signed_diff": (
                call.normalized_signed_diff
                if call.kind == "score" and expose_scores
                else None
            ),
            "normalized_absolute_diff": (
                call.normalized_absolute_diff
                if call.kind == "score" and expose_scores
                else None
            ),
            "model_feedback": (
                (call.output_json or {}).get("feedback")
                if call.kind == "score" and expose_scores
                else None
            ),
            "failure_code": call.failure_code,
            "failure_summary": call.failure_summary,
            "worker_id": call.worker_id,
            "slot_id": call.slot_id,
        }


__all__ = ["MemoryStudyDomainError", "MemoryStudyService"]
