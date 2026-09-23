import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import select

from app.experiment.common.output_schema import (
    assert_strict_output_schema,
    strictify_output_schema,
)
from app.experiment.memory_study.codex_bridge import (
    CodexFrameworkLLMBridge,
    FrameworkInvocationRecorder,
    _response_schema,
)
from app.experiment.memory_study.dataset import StudyRecord
from app.experiment.memory_study.memory import (
    CaseRetrievalAdapter,
    MemoryFrameworkError,
    OfficialMemoryAdapter,
    _redact_text,
    case_payload,
    create_adapter,
    memory_hash,
)
from app.experiment.memory_study.worker import (
    _minimal_memory_case,
    _runtime_for_model,
)
from app.models.memory_study import MSCall, MSFrameworkInvocation, MSStudy


class FakeProvider:
    model_name = "fake/all-MiniLM-L6-v2"
    revision = "test-revision"

    def encode(self, texts):
        values = []
        for text in texts:
            values.append([float(len(text)), float(sum(map(ord, text)) % 101)])
        matrix = np.asarray(values, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.where(norms == 0, 1, norms)


def record(answer_id: str, text: str, feedback: str = "manual feedback"):
    return StudyRecord(
        answer_id=answer_id,
        group_id=answer_id,
        question_id="q1",
        question_text="What is the answer?",
        reference_answer="The answer is evidence.",
        student_answer=text,
        teacher_score=1.0,
        teacher_feedback=feedback,
        verification_feedback="hidden verification",
        source_split="training",
        source_path="test.xml",
        source_position=0,
        selection_kind="training",
    )


def test_diagnostic_text_redacts_credentials_and_bounds_output():
    message = "Authorization: Bearer sk-live-token api_key=another-secret"
    redacted = _redact_text(message, 80)
    assert "sk-live-token" not in redacted
    assert "another-secret" not in redacted
    assert "<redacted>" in redacted


def test_no_feedback_cases_do_not_contain_feedback_and_snapshot_restores(monkeypatch):
    monkeypatch.setattr(
        "app.experiment.memory_study.memory.payload_token_counts",
        lambda _value: {"stored_tokens": 1, "request_tokens": 1},
    )
    first = record("a1", "evidence and explanation")
    second = record("a2", "evidence with an example")
    adapter = CaseRetrievalAdapter(
        feedback_mode="no_feedback",
        provider=FakeProvider(),
        catalog=[first, second],
    )
    adapter.ingest(first)
    adapter.ingest(second)
    snapshot = adapter.snapshot()
    assert all("reference_answer" not in item["payload"] for item in snapshot["items"])
    assert all("manual_feedback" not in item["payload"] for item in snapshot["items"])
    restored = CaseRetrievalAdapter(
        feedback_mode="no_feedback",
        provider=FakeProvider(),
        catalog=[first, second],
    )
    restored.restore(snapshot)
    results = restored.retrieve(record("test", "evidence"), top_k=20)
    assert [item.answer_id for item in results] == ["a1", "a2"]
    assert case_payload(first, "full")["manual_feedback"] == "manual feedback"
    assert "reference_answer" not in case_payload(first, "full")


def test_create_adapter_accepts_the_worker_catalog_argument():
    """worker._adapter passes catalog=; the factory must forward it."""
    first = record("a1", "evidence and explanation")
    adapter = create_adapter(
        framework="retrieval",
        feedback_mode="no_feedback",
        provider=FakeProvider(),
        catalog=[first],
    )
    adapter.ingest(first)
    snapshot = adapter.snapshot()
    restored = create_adapter(
        framework="retrieval",
        feedback_mode="no_feedback",
        provider=FakeProvider(),
        catalog=[first],
    )
    restored.restore(snapshot)
    results = restored.retrieve(record("test", "evidence"), top_k=5)
    assert [item.answer_id for item in results] == ["a1"]


def test_mem0_official_entrypoints_and_answer_metadata(monkeypatch, tmp_path):
    class FakeMemory:
        instance = None

        @classmethod
        def from_config(cls, config):
            cls.instance = cls()
            cls.instance.config = config
            return cls.instance

        def add(self, messages, *, user_id, metadata, infer):
            self.add_args = (messages, user_id, metadata)
            self.add_infer = infer
            return {"results": [{"id": "mem-1"}]}

        def search(self, query, *, top_k, filters):
            self.search_args = (query, top_k, filters)
            return {
                "results": [
                    {"id": "mem-1", "metadata": {"answer_id": "a1"}, "score": 0.9}
                ]
            }

    module = SimpleNamespace(Memory=FakeMemory, __version__="test-mem0")
    factory_module = SimpleNamespace(
        LlmFactory=SimpleNamespace(register_provider=lambda *args, **kwargs: None)
    )
    original_import = __import__(
        "app.experiment.memory_study.memory", fromlist=["importlib"]
    ).importlib.import_module

    def fake_import(name):
        if name == "mem0":
            return module
        if name == "mem0.utils.factory":
            return factory_module
        return original_import(name)

    monkeypatch.setattr(
        "app.experiment.memory_study.memory.importlib.import_module", fake_import
    )
    adapter = OfficialMemoryAdapter(
        framework="mem0",
        feedback_mode="full",
        config={"stream_id": "stream-1", "snapshot_root": str(tmp_path)},
    )
    result = adapter.ingest(record("a1", "evidence"))
    assert result["answer_id"] == "a1"
    assert FakeMemory.instance.add_args[1:] == (
        "stream-1",
        {"answer_id": "a1"},
    )
    assert FakeMemory.instance.add_infer is False
    retrieved = adapter.retrieve(record("test", "evidence"))
    assert retrieved[0].answer_id == "a1"
    assert FakeMemory.instance.search_args[1:] == (
        20,
        {"user_id": "stream-1"},
    )


def test_mem0_ingest_keeps_cases_verbatim_so_the_projection_works(
    monkeypatch, tmp_path
):
    """Regression for call #146263 (10.2_TC, condition ``mem0_full``).

    ``mem0.Memory.add`` defaults to ``infer=True``, which routes the message
    through mem0's LLM fact-extraction step.  The canonical case JSON the study
    hands over was therefore rewritten into English prose, and the V3-r2
    blinded projection could not read ``student_answer``/``teacher_score``
    back out of it, so the second score of every mem0 stream died as
    ``framework_unavailable``.  The fake below mirrors
    ``mem0.memory.main.Memory._add_to_vector_store``: with ``infer=False`` it
    stores one memory per message, verbatim, with no LLM call.
    """

    class FakeMemory:
        instance = None

        def __init__(self):
            self.memories = {}

        @classmethod
        def from_config(cls, config):
            cls.instance = cls()
            return cls.instance

        def add(self, messages, *, user_id, metadata, infer):
            for message in messages:
                text = message["content"]
                if infer:
                    text = "User answered a question and received partial credit."
                self.memories[metadata["answer_id"]] = text
            return {"results": [{"id": "mem-1"}]}

        def search(self, query, *, top_k, filters):
            return {
                "results": [
                    {
                        "id": answer_id,
                        "memory": text,
                        "metadata": {"answer_id": answer_id},
                        "score": 0.9,
                    }
                    for answer_id, text in self.memories.items()
                ]
            }

    module = SimpleNamespace(Memory=FakeMemory, __version__="test-mem0")
    factory_module = SimpleNamespace(
        LlmFactory=SimpleNamespace(register_provider=lambda *args, **kwargs: None)
    )
    original_import = __import__(
        "app.experiment.memory_study.memory", fromlist=["importlib"]
    ).importlib.import_module

    def fake_import(name):
        if name == "mem0":
            return module
        if name == "mem0.utils.factory":
            return factory_module
        return original_import(name)

    monkeypatch.setattr(
        "app.experiment.memory_study.memory.importlib.import_module", fake_import
    )
    adapter = OfficialMemoryAdapter(
        framework="mem0",
        feedback_mode="full",
        config={"stream_id": "stream-1", "snapshot_root": str(tmp_path)},
    )
    adapter.ingest(record("a1", "evidence"))

    stored = FakeMemory.instance.memories["a1"]
    assert json.loads(stored) == case_payload(
        record("a1", "evidence"), "full", profile="legacy_v1"
    )
    [item] = adapter.retrieve(record("test", "evidence"))
    assert _minimal_memory_case(item, framework="mem0") == {
        "student_answer": "evidence",
        "teacher_score": 1.0,
        "teacher_feedback": "manual feedback",
    }


def test_mem0_native_snapshot_restores_files_and_search(monkeypatch, tmp_path):
    class FakeMemory:
        instance = None

        def __init__(self, config):
            self.config = config
            self.vector_path = Path(config["vector_store"]["config"]["path"])
            self.history_path = Path(config["history_db_path"])
            self.vector_path.mkdir(parents=True, exist_ok=True)
            records = self.vector_path / "records.json"
            self.records = json.loads(records.read_text()) if records.exists() else []

        @classmethod
        def from_config(cls, config):
            cls.instance = cls(config)
            return cls.instance

        def add(self, messages, *, user_id, metadata, infer):
            self.records.append(
                {"answer_id": metadata["answer_id"], "user_id": user_id}
            )
            (self.vector_path / "records.json").write_text(
                json.dumps(self.records), encoding="utf-8"
            )
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            self.history_path.write_text("history", encoding="utf-8")
            return {"results": [{"id": metadata["answer_id"]}]}

        def search(self, query, *, top_k, filters):
            return {
                "results": [
                    {
                        "id": item["answer_id"],
                        "metadata": {"answer_id": item["answer_id"]},
                        "score": 0.9,
                    }
                    for item in self.records[:top_k]
                    if item["user_id"] == filters["user_id"]
                ]
            }

    module = SimpleNamespace(Memory=FakeMemory, __version__="test-mem0")
    factory_module = SimpleNamespace(
        LlmFactory=SimpleNamespace(register_provider=lambda *args, **kwargs: None)
    )
    original_import = __import__(
        "app.experiment.memory_study.memory", fromlist=["importlib"]
    ).importlib.import_module

    def fake_import(name):
        if name == "mem0":
            return module
        if name == "mem0.utils.factory":
            return factory_module
        return original_import(name)

    monkeypatch.setattr(
        "app.experiment.memory_study.memory.importlib.import_module", fake_import
    )
    root = tmp_path / "mem0"
    config = {
        "stream_id": "stream-native",
        "snapshot_root": str(root),
        "vector_store": {
            "provider": "chroma",
            "config": {"path": str(root / "vector")},
        },
        "history_db_path": str(root / "history.db"),
    }
    adapter = OfficialMemoryAdapter(
        framework="mem0", feedback_mode="full", config=config
    )
    adapter.ingest(record("a1", "evidence"))
    before = adapter.retrieve(record("test", "evidence"))
    snapshot = adapter.snapshot()
    snapshot_hash = memory_hash(snapshot)
    assert snapshot["native_artifacts"]
    assert snapshot["artifact_sha256"]

    restored = OfficialMemoryAdapter(
        framework="mem0", feedback_mode="full", config=config
    )
    restored.restore(snapshot)
    assert memory_hash(restored.snapshot()) == snapshot_hash
    assert [
        item.answer_id for item in restored.retrieve(record("test", "evidence"))
    ] == [item.answer_id for item in before]


def test_amem_uses_agentic_memory_system_add_note_and_search(monkeypatch, tmp_path):
    class FakeNote:
        def __init__(self, content, id=None, **kwargs):
            self.content = content
            self.id = id
            self.keywords = kwargs.get("keywords", [])
            self.links = kwargs.get("links", [])
            self.retrieval_count = kwargs.get("retrieval_count", 0)
            self.timestamp = kwargs.get("timestamp", "now")
            self.last_accessed = kwargs.get("last_accessed", "now")
            self.context = kwargs.get("context", "General")
            self.evolution_history = kwargs.get("evolution_history", [])
            self.category = kwargs.get("category", "Uncategorized")
            self.tags = kwargs.get("tags", [])

    class FakeAgenticMemorySystem:
        def __init__(self, **kwargs):
            self.memories = {}
            self.evo_cnt = 0
            self.evo_threshold = 100
            self.llm_controller = SimpleNamespace(llm=object())
            self.retriever = SimpleNamespace(add_document=lambda *args, **kwargs: None)

        def add_note(self, content, *, id):
            self.memories[id] = FakeNote(content, id=id)
            return id

        def search(self, query, *, k):
            return [{"id": "a1", "content": "stored", "score": 0.8}]

    module = SimpleNamespace(
        AgenticMemorySystem=FakeAgenticMemorySystem,
        ChromaRetriever=object,
        MemoryNote=FakeNote,
        __version__="test-amem",
    )
    original_import = __import__(
        "app.experiment.memory_study.memory", fromlist=["importlib"]
    ).importlib.import_module

    def fake_import(name):
        if name == "agentic_memory.memory_system":
            return module
        return original_import(name)

    monkeypatch.setattr(
        "app.experiment.memory_study.memory.importlib.import_module", fake_import
    )
    adapter = OfficialMemoryAdapter(
        framework="amem",
        feedback_mode="no_feedback",
        config={
            "stream_id": "stream-2",
            "snapshot_root": str(tmp_path),
            "embedding_backend": "openai",
        },
    )
    adapter.ingest(record("a1", "evidence"))
    assert (
        "manual_feedback" not in next(iter(adapter._backend.memories.values())).content
    )
    assert adapter.retrieve(record("test", "evidence"))[0].answer_id == "a1"
    snapshot = adapter.snapshot()
    restored = OfficialMemoryAdapter(
        framework="amem",
        feedback_mode="no_feedback",
        config={
            "stream_id": "stream-2",
            "snapshot_root": str(tmp_path),
            "embedding_backend": "openai",
        },
    )
    restored.restore(snapshot)
    assert restored.inspect()["item_count"] == 1
    assert memory_hash(restored.snapshot()) == memory_hash(snapshot)
    assert [
        item.answer_id for item in restored.retrieve(record("test", "evidence"))
    ] == [item.answer_id for item in adapter.retrieve(record("test", "evidence"))]


def test_worker_runtime_binds_the_requested_slot_model():
    study = SimpleNamespace(
        config_json={
            "models": [
                {
                    "model": "gpt-5.6-luna",
                    "reasoning_effort": "medium",
                    "speed_mode": "standard",
                    "timeout_seconds": 120,
                },
                {
                    "model": "gpt-5.6-terra",
                    "reasoning_effort": "medium",
                    "speed_mode": "standard",
                    "timeout_seconds": 120,
                },
            ]
        }
    )
    assert _runtime_for_model(None, study, "gpt-5.6-terra")["model"] == "gpt-5.6-terra"
    assert _runtime_for_model(None, study, "gpt-5.6-luna")["model"] == "gpt-5.6-luna"


def test_framework_bridge_logs_multiple_internal_codex_requests(db_session):
    study = MSStudy(
        id="study-1",
        protocol_id="saf-memory-framework-v1",
        name="bridge",
        kind="development",
        status="running",
        data_processing_confirmed=True,
        data_manifest_json={},
        config_json={},
        expected_json={},
        progress_json={},
        integrity_status="pending",
        integrity_json={},
        results_embargoed=True,
    )
    db_session.add(study)
    db_session.flush()
    call = MSCall(
        study_id=study.id,
        model="gpt-5.6-luna",
        question_id="q1",
        condition="amem_full",
        framework="amem",
        feedback_mode="full",
        order_variant="order_1",
        kind="memory_write",
        answer_id="a1",
        repeat=0,
        status="running",
        attempt_count=1,
    )
    db_session.add(call)
    db_session.flush()

    class FakeRunner:
        def __init__(self):
            self.calls = []

        def run(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                value={"keywords": ["evidence"]},
                raw_json='{"keywords":["evidence"]}',
                latency_ms=2,
            )

    runner = FakeRunner()
    recorder = FrameworkInvocationRecorder(db_session, call, "amem")
    bridge = CodexFrameworkLLMBridge(
        runner=runner,
        runtime={
            "model": "gpt-5.6-luna",
            "reasoning_effort": "medium",
            "timeout_seconds": 120,
        },
        recorder=recorder,
    )
    assert bridge.generate_response(
        messages=[{"role": "user", "content": "one"}],
        response_format={"type": "json_object"},
    )
    assert bridge.get_completion("two", response_format={"type": "json_object"})
    rows = list(
        db_session.scalars(
            select(MSFrameworkInvocation).where(
                MSFrameworkInvocation.call_id == call.id
            )
        )
    )
    assert len(runner.calls) == 2
    assert [row.sequence_number for row in rows] == [1, 2]
    assert all(
        row.transport == "codex_exec" and row.status == "succeeded" for row in rows
    )


def test_amem_swallowed_internal_failure_is_not_committable(
    monkeypatch, db_session, tmp_path
):
    class FakeAgenticMemorySystem:
        def __init__(self, **kwargs):
            self.memories = {}
            self.evo_cnt = 0
            self.evo_threshold = 100
            self.llm_controller = SimpleNamespace(llm=object())

        def add_note(self, content, *, id):
            try:
                self.llm_controller.llm.get_completion(
                    "extract", response_format={"type": "json_object"}
                )
            except Exception:
                return id
            return id

        def search(self, query, *, k):
            return []

    module = SimpleNamespace(
        AgenticMemorySystem=FakeAgenticMemorySystem,
        ChromaRetriever=object,
        MemoryNote=lambda *args, **kwargs: None,
        __version__="test-amem",
    )
    original_import = __import__(
        "app.experiment.memory_study.memory", fromlist=["importlib"]
    ).importlib.import_module

    def fake_import(name):
        if name == "agentic_memory.memory_system":
            return module
        return original_import(name)

    monkeypatch.setattr(
        "app.experiment.memory_study.memory.importlib.import_module", fake_import
    )
    study = MSStudy(
        id="study-2",
        protocol_id="saf-memory-framework-v1",
        name="failure",
        kind="development",
        status="running",
        data_processing_confirmed=True,
        data_manifest_json={},
        config_json={},
        expected_json={},
        progress_json={},
        integrity_status="pending",
        integrity_json={},
        results_embargoed=True,
    )
    db_session.add(study)
    db_session.flush()
    call = MSCall(
        study_id=study.id,
        model="gpt-5.6-luna",
        question_id="q1",
        condition="amem_full",
        framework="amem",
        feedback_mode="full",
        order_variant="order_1",
        kind="memory_write",
        answer_id="a1",
        repeat=0,
        status="running",
        attempt_count=1,
    )
    db_session.add(call)
    db_session.flush()

    class FailingRunner:
        def run(self, **kwargs):
            raise RuntimeError("codex failure")

    recorder = FrameworkInvocationRecorder(db_session, call, "amem")
    adapter = OfficialMemoryAdapter(
        framework="amem",
        feedback_mode="full",
        config={
            "stream_id": "stream-fail",
            "snapshot_root": str(tmp_path),
            "embedding_backend": "openai",
        },
        runner=FailingRunner(),
        runtime={"model": "gpt-5.6-luna", "reasoning_effort": "medium"},
        recorder=recorder,
    )
    try:
        adapter.ingest(record("a1", "evidence"))
    except MemoryFrameworkError as exc:
        assert "swallowed" in str(exc)
    else:
        raise AssertionError("a swallowed A-MEM bridge failure must fail ingest")
    assert recorder.failed is True


# --- the framework schema contract -----------------------------------------
#
# The pilot run failed because every schema the frameworks handed to the CLI was
# rejected by the API, and nothing in the suite looked at those documents: the
# fake runners accepted anything.  These assertions pin each branch instead.  The
# CLI boundary itself is covered in tests/experiment/common.

# Verbatim from `agentic_memory/memory_system.py`: A-MEM names its fields but
# supplies neither `required` nor `additionalProperties`.
AMEM_ANALYSIS_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "response",
        "schema": {
            "type": "object",
            "properties": {
                "keywords": {"type": "array", "items": {"type": "string"}},
                "context": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}

AMEM_EVOLUTION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "response",
        "schema": {
            "type": "object",
            "properties": {
                "should_evolve": {"type": "boolean"},
                "actions": {"type": "array", "items": {"type": "string"}},
                "suggested_connections": {"type": "array", "items": {"type": "string"}},
                "new_context_neighborhood": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "tags_to_update": {"type": "array", "items": {"type": "string"}},
                "new_tags_neighborhood": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                },
            },
            "required": [
                "should_evolve",
                "actions",
                "suggested_connections",
                "tags_to_update",
                "new_context_neighborhood",
                "new_tags_neighborhood",
            ],
            "additionalProperties": False,
        },
        "strict": True,
    },
}

SCHEMA_BRANCHES = {
    "amem-analysis": AMEM_ANALYSIS_RESPONSE_FORMAT,
    "amem-evolution": AMEM_EVOLUTION_RESPONSE_FORMAT,
    "mem0-extraction-json-object": {"type": "json_object"},
    "no-response-format": None,
    "json-schema-without-a-nested-schema": {
        "type": "json_schema",
        "json_schema": {"name": "response"},
    },
}


@pytest.mark.parametrize(
    "response_format", list(SCHEMA_BRANCHES.values()), ids=list(SCHEMA_BRANCHES)
)
def test_every_response_format_branch_reaches_the_cli_as_a_strict_schema(
    response_format,
):
    """Every branch must be *expressible*, and legal once the boundary is done.

    ``_response_schema`` deliberately returns the framework's own document
    unmodified -- strictifying belongs to the CLI boundary so that every schema
    source is covered by one contract.  What each branch owes is a schema that
    the boundary can make legal; a branch returning a free-form object instead
    would raise here rather than after 5760 failed writes.
    """
    assert_strict_output_schema(
        strictify_output_schema(_response_schema(response_format))
    )


def test_a_framework_that_already_delivers_a_strict_schema_is_left_alone():
    """A-MEM's evolution call is compliant; normalisation must not rewrite it."""
    schema = _response_schema(AMEM_EVOLUTION_RESPONSE_FORMAT)
    assert strictify_output_schema(schema) == schema


def test_fallback_keeps_the_mem0_extraction_contract():
    """mem0 reads `json.loads(response).get("memory", [])` and `mem["text"]`."""
    schema = _response_schema({"type": "json_object"})
    assert schema["required"] == ["memory"]
    item = schema["properties"]["memory"]["items"]
    assert item["required"] == ["id", "text", "attributed_to", "linked_memory_ids"]
    assert item["additionalProperties"] is False


def test_each_call_gets_its_own_copy_of_the_fallback():
    """Mutating one request's schema must not corrupt the next."""
    first = _response_schema(None)
    first["properties"]["memory"].clear()
    assert _response_schema(None)["properties"]["memory"]["items"]


def _bridge_call(db_session, study_id: str = "bridge-study") -> MSCall:
    study = MSStudy(
        id=study_id,
        protocol_id="saf-memory-framework-v1",
        name="bridge",
        kind="pilot",
        status="running",
        data_processing_confirmed=True,
        data_manifest_json={},
        config_json={},
        expected_json={},
        progress_json={},
        integrity_status="pending",
        integrity_json={},
        results_embargoed=True,
    )
    db_session.add(study)
    db_session.flush()
    call = MSCall(
        study_id=study.id,
        model="gpt-5.6-luna",
        question_id="q1",
        condition="mem0_full",
        framework="mem0",
        feedback_mode="full",
        order_variant="order_1",
        kind="memory_write",
        answer_id="a1",
        repeat=0,
        status="running",
        attempt_count=1,
    )
    db_session.add(call)
    db_session.flush()
    return call


BRIDGE_RUNTIME = {
    "model": "gpt-5.6-luna",
    "reasoning_effort": "medium",
    "timeout_seconds": 120,
}


def test_bridge_hands_the_runner_a_strict_schema_and_no_substitute(db_session):
    """The framework's own contract must travel to the CLI unsubstituted.

    Passing a generic container in place of the framework's schema would change
    the requested response shape without changing anything observable, which is
    indistinguishable from a measurement defect.
    """
    call = _bridge_call(db_session)
    seen: dict = {}

    class CapturingRunner:
        def run(self, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(
                value={"memory": []}, raw_json='{"memory":[]}', latency_ms=3
            )

    bridge = CodexFrameworkLLMBridge(
        runner=CapturingRunner(),
        runtime=BRIDGE_RUNTIME,
        recorder=FrameworkInvocationRecorder(db_session, call, "mem0"),
    )
    assert bridge.generate_response(
        messages=[{"role": "user", "content": "one"}],
        response_format={"type": "json_object"},
    )
    assert_strict_output_schema(seen["schema_json"])
    assert "schema" not in seen


def test_recorder_remembers_why_an_invocation_failed(db_session):
    """The cause has to survive the framework, which swallows its own errors."""
    call = _bridge_call(db_session)

    class FailingRunner:
        def run(self, **kwargs):
            raise RuntimeError("codex exec exited with 1; stderr: invalid_json_schema")

    recorder = FrameworkInvocationRecorder(db_session, call, "mem0")
    bridge = CodexFrameworkLLMBridge(
        runner=FailingRunner(), runtime=BRIDGE_RUNTIME, recorder=recorder
    )
    with pytest.raises(RuntimeError):
        bridge.generate_response(messages=[{"role": "user", "content": "one"}])

    assert recorder.failed is True
    assert recorder.failed_phase == "mem0.generate_response"
    assert "invalid_json_schema" in recorder.last_error
    row = db_session.scalar(
        select(MSFrameworkInvocation).where(MSFrameworkInvocation.call_id == call.id)
    )
    assert row.status == "failed"
    assert "invalid_json_schema" in row.error_message
