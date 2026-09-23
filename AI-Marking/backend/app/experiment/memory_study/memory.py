"""Framework adapters with a common operational contract.

The contract is deliberately small: ``ingest``, ``snapshot``, ``restore``,
``retrieve`` and ``inspect``.  The adapter never imposes a memory token limit
or silently truncates a framework result.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import shutil
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

import numpy as np

from app.experiment.common.tokenization import token_count
from app.experiment.memory_study.dataset import StudyRecord
from app.experiment.memory_study.protocol import (
    AMEM_COMMIT,
    EMBEDDING_MODEL,
    MEM0_COMMIT,
    RETRIEVAL_TOP_K,
)


class MemoryFrameworkError(RuntimeError):
    """A framework cannot be loaded or returned an unrecoverable response."""


class MemoryEvidenceError(MemoryFrameworkError):
    """A retrieved case cannot be projected into the scoring-visible shape.

    This is a data-shape failure, not a missing adapter: the framework loaded
    and answered, but the evidence it returned cannot be reduced to the
    student-visible scoring fields.  Subclassing keeps every existing
    ``except MemoryFrameworkError`` handler working while letting the failure
    classifier report the real cause instead of "framework unavailable".
    """


def _safe_chroma_collection_name(value: Any) -> str:
    """Return a Chroma-compatible, deterministic collection name.

    Chroma limits names to 3--63 characters and rejects UUID-like values that
    are not valid collection names.  Older study configs could carry a full
    study UUID here, so normalize and bound it at the adapter boundary.
    """
    raw = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "")).strip("-_")
    if len(raw) < 3:
        raw = f"saf-{hashlib.sha256(str(value).encode()).hexdigest()[:12]}"
    if len(raw) > 63:
        raw = f"saf-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"
    if raw[0].isdigit() and raw[-1].isdigit() and re.fullmatch(r"\d+(?:\.\d+){3}", raw):
        raw = f"saf-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"
    return raw


class _CodexPlaceholderLLM:
    """Construction-only Mem0 provider; every real request is replaced by bridge."""

    def __init__(self, _config: Any) -> None:
        pass

    def generate_response(self, **_kwargs: Any) -> str:
        raise MemoryFrameworkError(
            "Mem0 placeholder LLM must be replaced by Codex bridge"
        )


class _UninitializedRetriever:
    """Construction-only A-MEM retriever for API embedding mode."""

    class _Client:
        @staticmethod
        def reset() -> None:
            return None

    client = _Client()

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass


_AMEM_CONSTRUCTOR_LOCK = threading.Lock()

# Retrieval repeatedly asks for the same training payloads (once per score
# call).  Keep a bounded process-local cache in front of the on-disk cache so
# a score batch does not repeatedly mmap/read hundreds of small ``.npy``
# files.  The key is the fully-qualified cache path, which already includes
# provider, endpoint, model and revision.
_VECTOR_CACHE_LIMIT = 4096
_VECTOR_CACHE: OrderedDict[str, np.ndarray] = OrderedDict()
_VECTOR_CACHE_LOCK = threading.RLock()


def _cached_vector(path: Path) -> np.ndarray | None:
    key = str(path.resolve())
    with _VECTOR_CACHE_LOCK:
        value = _VECTOR_CACHE.get(key)
        if value is not None:
            _VECTOR_CACHE.move_to_end(key)
            return value.copy()
    if not path.is_file():
        return None
    value = np.asarray(np.load(path, allow_pickle=False), dtype=np.float32)
    value.setflags(write=False)
    with _VECTOR_CACHE_LOCK:
        _VECTOR_CACHE[key] = value
        _VECTOR_CACHE.move_to_end(key)
        while len(_VECTOR_CACHE) > _VECTOR_CACHE_LIMIT:
            _VECTOR_CACHE.popitem(last=False)
    return value.copy()


def _cache_vector(path: Path, vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float32)
    value.setflags(write=False)
    key = str(path.resolve())
    with _VECTOR_CACHE_LOCK:
        _VECTOR_CACHE[key] = value
        _VECTOR_CACHE.move_to_end(key)
        while len(_VECTOR_CACHE) > _VECTOR_CACHE_LIMIT:
            _VECTOR_CACHE.popitem(last=False)
    return value.copy()


def _write_vector(path: Path, vector: np.ndarray, cache_dir: Path) -> None:
    """Persist one vector with an atomic same-directory replace."""
    generated: Path | None = None
    with tempfile.NamedTemporaryFile(
        dir=cache_dir, prefix="embedding-", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        np.save(temporary, vector, allow_pickle=False)
        generated = temporary.with_suffix(temporary.suffix + ".npy")
        if not path.exists():
            os.replace(generated, path)
        else:
            generated.unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)
        # ``np.save`` appends ``.npy`` to our ``.tmp`` name.  If serialization
        # or the atomic replace fails, remove that sibling too so an embedding
        # error cannot accumulate unbounded cache temp files.
        if generated is not None:
            generated.unlink(missing_ok=True)


class EmbeddingProvider(Protocol):
    model_name: str
    revision: str

    def encode(self, texts: list[str]) -> np.ndarray: ...


@dataclass(frozen=True, slots=True)
class RetrievedCase:
    answer_id: str
    score: float
    similarity: float
    payload: dict[str, Any]
    native_relevance_score: float | None = None

    def __post_init__(self) -> None:
        # ``score`` is retained for the V3 wire shape; the native relevance
        # value is kept separately for audit purposes.
        if self.native_relevance_score is None:
            object.__setattr__(self, "native_relevance_score", self.score)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _redact_secrets(value: Any) -> Any:
    """Return a JSON-safe config copy with credentials removed.

    Framework snapshots are durable research artifacts and are routinely
    exported for audit.  Keep the full config in memory for the live client,
    but never serialize API keys (or similarly named credentials) into a
    snapshot manifest.
    """
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            compact = normalized.replace("_", "")
            if any(
                token in normalized or token.replace("_", "") in compact
                for token in ("api_key", "access_token", "password", "secret")
            ):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_secrets(item) for item in value]
    return value


_SECRET_TEXT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|password|secret|authorization)\b"
    r"\s*[:=]\s*[^\s,;]+"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")


def _redact_text(value: Any, limit: int | None = None) -> str:
    """Bound and redact credential-shaped values in diagnostic text."""
    text = str(value)
    # Handle ``Authorization: Bearer …`` before the generic key/value rule so
    # the latter cannot consume only the word ``Bearer`` and leave the token.
    text = _BEARER_PATTERN.sub("Bearer <redacted>", text)
    text = _SECRET_TEXT_PATTERN.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    if limit is not None and len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text


def _portable_config(value: Any, snapshot_root: str | Path) -> Any:
    """Replace attempt-specific absolute paths before hashing a config.

    A memory write is evaluated in a temporary directory and then promoted to
    the committed stream.  Hashing the literal path would make an otherwise
    identical snapshot fail restore on every retry because the temporary
    directory name necessarily changes.  Only paths rooted at this stream are
    rewritten; endpoint URLs and other user values remain byte-for-byte intact.
    """
    root = str(Path(snapshot_root).resolve()).rstrip(os.sep)
    if isinstance(value, str):
        if value == root:
            return "<snapshot_root>"
        prefix = root + os.sep
        if value.startswith(prefix):
            return "<snapshot_root>" + value[len(root) :]
        return value
    if isinstance(value, dict):
        return {key: _portable_config(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_portable_config(item, root) for item in value]
    if isinstance(value, tuple):
        return [_portable_config(item, root) for item in value]
    return value


def memory_hash(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(snapshot).encode()).hexdigest()


def case_payload(
    record: StudyRecord,
    feedback_mode: str,
    *,
    profile: str = "legacy_v1",
    score_ceiling: float | None = None,
) -> dict[str, Any]:
    """Build a memory case without the reference answer or hidden labels."""
    del score_ceiling
    if profile == "legacy_v1":
        payload = {
            "question": record.question_text,
            "answer": record.student_answer,
            "manual_score": record.teacher_score,
        }
    else:
        raise ValueError(f"unknown memory profile: {profile}")
    if feedback_mode == "full":
        payload["manual_feedback"] = record.teacher_feedback
    elif feedback_mode != "no_feedback":
        raise ValueError(f"unknown feedback mode: {feedback_mode}")
    return payload


def query_payload(record: StudyRecord, *, profile: str = "legacy_v1") -> dict[str, Any]:
    """Build a memory query without the reference answer or labels."""
    if profile == "legacy_v1":
        return {"question": record.question_text, "answer": record.student_answer}
    raise ValueError(f"unknown memory profile: {profile}")


def payload_token_counts(payload: Any) -> dict[str, int]:
    """Count literal request/storage material using the frozen o200k_base codec."""
    encoded = _canonical(payload)
    return {
        "stored_tokens": token_count(encoded),
        "request_tokens": token_count(encoded),
    }


class SentenceTransformerEmbeddingProvider:
    """Lazy local MiniLM provider with an atomic on-disk embedding cache."""

    def __init__(
        self,
        *,
        model_name: str = EMBEDDING_MODEL,
        revision: str,
        cache_dir: str | Path,
    ) -> None:
        if not revision or revision in {"main", "latest", "unresolved"}:
            raise MemoryFrameworkError(
                "embedding revision must be explicitly pinned before a study can run"
            )
        self.model_name = model_name
        self.revision = revision
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._model: Any | None = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                module = importlib.import_module("sentence_transformers")
            except ImportError as exc:  # pragma: no cover - depends on runtime setup
                raise MemoryFrameworkError(
                    "sentence-transformers is required for the pinned MiniLM retriever"
                ) from exc
            try:
                self._model = module.SentenceTransformer(
                    self.model_name,
                    revision=self.revision,
                    cache_folder=str(self.cache_dir),
                )
            except Exception as exc:  # pragma: no cover - model download/runtime
                raise MemoryFrameworkError(
                    f"cannot load {self.model_name}@{self.revision}: {exc}"
                ) from exc
        return self._model

    def _cache_path(self, text: str) -> Path:
        key = hashlib.sha256(
            f"{self.model_name}@{self.revision}\n{text}".encode()
        ).hexdigest()
        return self.cache_dir / f"{key}.npy"

    def _encode_many(self, texts: list[str]) -> dict[str, np.ndarray]:
        vectors = self._load().encode(
            texts, convert_to_numpy=True, normalize_embeddings=True
        )
        return {
            text: np.asarray(vector, dtype=np.float32)
            for text, vector in zip(texts, vectors, strict=True)
        }

    def _encode_one(self, text: str) -> np.ndarray:
        path = self._cache_path(text)
        cached = _cached_vector(path)
        if cached is not None:
            return cached
        vector = self._encode_many([text])[text]
        _write_vector(path, vector, self.cache_dir)
        return _cache_vector(path, vector)

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, 0), dtype=np.float32)
        unique = list(dict.fromkeys(texts))
        vectors: dict[str, np.ndarray] = {}
        missing: list[str] = []
        for text in unique:
            cached = _cached_vector(self._cache_path(text))
            if cached is None:
                missing.append(text)
            else:
                vectors[text] = cached
        if missing:
            generated = self._encode_many(missing)
            for text, vector in generated.items():
                path = self._cache_path(text)
                _write_vector(path, vector, self.cache_dir)
                vectors[text] = _cache_vector(path, vector)
        return np.vstack([vectors[text] for text in texts])


class OpenAICompatibleEmbeddingProvider:
    """Cloud provider for any OpenAI-compatible ``/v1/embeddings`` endpoint.

    Vector widths are resolved from the first live response instead of being
    configured, so a revision pin is the only reproducibility input.  Results
    reuse the same atomic on-disk cache contract as the local provider; the
    cache key includes the base URL and model so two endpoints never share a
    vector.  The API key is read from the study config (which is frozen from
    the site config at creation time) and is never written into snapshots.
    """

    backend = "openai"

    # Ark (Doubao) accepts at most 10 inputs per embeddings request; other
    # OpenAI-compatible endpoints accept at least that many, so chunking to 10
    # stays inside every provider's limit.
    REMOTE_BATCH_SIZE = 10

    def __init__(
        self,
        *,
        model_name: str,
        revision: str,
        api_base: str,
        api_key: str,
        cache_dir: str | Path,
    ) -> None:
        if not revision or revision in {"main", "latest", "unresolved"}:
            raise MemoryFrameworkError(
                "embedding revision must be explicitly pinned before a study can run"
            )
        if not api_base:
            raise MemoryFrameworkError(
                "openai embedding backend requires embedding_api_base"
            )
        if not api_key:
            raise MemoryFrameworkError(
                "openai embedding backend requires embedding_api_key"
            )
        self.model_name = model_name
        self.revision = revision
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client: Any | None = None

    def _load(self) -> Any:
        if self._client is None:
            try:
                module = importlib.import_module("openai")
            except ImportError as exc:  # pragma: no cover - depends on runtime setup
                raise MemoryFrameworkError(
                    "the openai package is required for the cloud embedding backend"
                ) from exc
            try:
                self._client = module.OpenAI(
                    api_key=self.api_key, base_url=self.api_base
                )
            except Exception as exc:  # pragma: no cover - client construction
                raise MemoryFrameworkError(
                    f"cannot construct the embedding client for {self.api_base}: {exc}"
                ) from exc
        return self._client

    def _embed_remote_batch(self, texts: list[str]) -> dict[str, list[float]]:
        if not texts:
            return {}
        try:
            response = self._load().embeddings.create(
                input=texts, model=self.model_name, encoding_format="float"
            )
            data = list(response.data)
            data.sort(key=lambda item: int(getattr(item, "index", 0)))
            if len(data) != len(texts):
                raise ValueError(
                    f"embedding response returned {len(data)} vectors for "
                    f"{len(texts)} inputs"
                )
            return {
                text: list(item.embedding)
                for text, item in zip(texts, data, strict=True)
            }
        except MemoryFrameworkError:
            raise
        except Exception as exc:
            raise MemoryFrameworkError(
                f"embedding request to {self.api_base} failed for "
                f"{self.model_name}@{self.revision}: {exc}"
            ) from exc

    def _embed_remote(self, text: str) -> list[float]:
        """Compatibility helper for callers that need one remote vector."""
        return self._embed_remote_batch([text])[text]

    def _cache_path(self, text: str) -> Path:
        key = hashlib.sha256(
            f"{self.backend}\x1f{self.api_base}\x1f{self.model_name}@{self.revision}\n{text}".encode()
        ).hexdigest()
        return self.cache_dir / f"{key}.npy"

    def _encode_one(self, text: str) -> np.ndarray:
        path = self._cache_path(text)
        cached = _cached_vector(path)
        if cached is not None:
            return cached
        vector = np.asarray(self._embed_remote(text), dtype=np.float32)
        _write_vector(path, vector, self.cache_dir)
        return _cache_vector(path, vector)

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, 0), dtype=np.float32)
        unique = list(dict.fromkeys(texts))
        vectors: dict[str, np.ndarray] = {}
        missing: list[str] = []
        for text in unique:
            cached = _cached_vector(self._cache_path(text))
            if cached is None:
                missing.append(text)
            else:
                vectors[text] = cached
        if missing:
            generated: dict[str, list[float]] = {}
            for start in range(0, len(missing), self.REMOTE_BATCH_SIZE):
                generated.update(
                    self._embed_remote_batch(
                        missing[start : start + self.REMOTE_BATCH_SIZE]
                    )
                )
            for text, raw in generated.items():
                vector = np.asarray(raw, dtype=np.float32)
                path = self._cache_path(text)
                _write_vector(path, vector, self.cache_dir)
                vectors[text] = _cache_vector(path, vector)
        return np.vstack([vectors[text] for text in texts])


class OpenAIEmbeddingFunction:
    """Chroma embedding function backed by :class:`OpenAICompatibleEmbeddingProvider`.

    The provider supplies the on-disk cache and error contract; this shim only
    adapts the ``Documents -> Embeddings`` call signature that ChromaDB and the
    A-MEM retriever expect.
    """

    def __init__(self, provider: OpenAICompatibleEmbeddingProvider) -> None:
        self._provider = provider

    def __call__(self, input: Any) -> list[list[float]]:
        texts = list(input)
        if not texts:
            return []
        return [vector.tolist() for vector in self._provider.encode(texts)]


class AmemCloudRetriever:
    """Line-faithful cloud mirror of the pinned A-MEM ``ChromaRetriever``.

    Upstream constructs a local ``SentenceTransformerEmbeddingFunction`` in
    its constructor, which would download and run MiniLM even when the study
    is configured for a cloud embedder.  This mirror keeps the upstream
    enhancement, metadata serialization and query contract byte-for-byte and
    swaps only the embedding function.  ``search`` returns the same
    ``{"ids": [[...]], "distances": [[...]], "metadatas": [[...]]}`` shape the
    pinned ``AgenticMemorySystem.search`` consumes.
    """

    def __init__(
        self,
        collection_name: str,
        embedding_function: OpenAIEmbeddingFunction,
        persist_dir: str | None = None,
    ) -> None:
        chromadb = importlib.import_module("chromadb")
        settings_cls = getattr(importlib.import_module("chromadb.config"), "Settings")
        path = persist_dir or tempfile.mkdtemp(prefix="amem-chroma-")
        # ``chromadb.Client`` with no arguments connects to the local Chroma
        # server at 127.0.0.1:8000, which is our own API port; use a persistent
        # on-disk client so every study stream owns an isolated collection.
        self.client = chromadb.PersistentClient(
            path=path, settings=settings_cls(allow_reset=True)
        )
        self.embedding_function = embedding_function
        self.collection = self.client.get_or_create_collection(
            name=collection_name, embedding_function=self.embedding_function
        )

    def add_document(self, document: str, metadata: dict, doc_id: str) -> None:
        enhanced_document = document
        if "context" in metadata and metadata["context"] != "General":
            enhanced_document += f" context: {metadata['context']}"
        if "keywords" in metadata and metadata["keywords"]:
            keywords = (
                metadata["keywords"]
                if isinstance(metadata["keywords"], list)
                else json.loads(metadata["keywords"])
            )
            if keywords:
                enhanced_document += f" keywords: {', '.join(keywords)}"
        if "tags" in metadata and metadata["tags"]:
            tags = (
                metadata["tags"]
                if isinstance(metadata["tags"], list)
                else json.loads(metadata["tags"])
            )
            if tags:
                enhanced_document += f" tags: {', '.join(tags)}"
        processed_metadata = {}
        for key, value in metadata.items():
            if isinstance(value, (list, dict)):
                processed_metadata[key] = json.dumps(value)
            else:
                processed_metadata[key] = str(value)
        processed_metadata["enhanced_content"] = enhanced_document
        self.collection.add(
            documents=[enhanced_document],
            metadatas=[processed_metadata],
            ids=[doc_id],
        )

    def delete_document(self, doc_id: str) -> None:
        self.collection.delete(ids=[doc_id])

    def search(self, query: str, k: int = 5) -> dict[str, Any]:
        results = self.collection.query(query_texts=[query], n_results=k)
        if "metadatas" in results and results["metadatas"]:
            for i in range(len(results["metadatas"])):
                if isinstance(results["metadatas"][i], list):
                    for j in range(len(results["metadatas"][i])):
                        if isinstance(results["metadatas"][i][j], dict):
                            metadata = results["metadatas"][i][j]
                            for key, value in metadata.items():
                                try:
                                    if isinstance(value, str) and (
                                        value.startswith("[") or value.startswith("{")
                                    ):
                                        metadata[key] = json.loads(value)
                                    elif (
                                        isinstance(value, str)
                                        and value.replace(".", "", 1).isdigit()
                                    ):
                                        if "." in value:
                                            metadata[key] = float(value)
                                        else:
                                            metadata[key] = int(value)
                                except (json.JSONDecodeError, ValueError):
                                    pass
        return results


class MemoryAdapter(Protocol):
    framework: str
    feedback_mode: str
    memory_profile: str

    def ingest(self, record: StudyRecord) -> dict[str, Any]: ...

    def snapshot(self) -> dict[str, Any]: ...

    def restore(self, snapshot: dict[str, Any]) -> None: ...

    def retrieve(
        self, record: StudyRecord, top_k: int = RETRIEVAL_TOP_K
    ) -> list[RetrievedCase]: ...

    def inspect(self) -> dict[str, Any]: ...


class CaseRetrievalAdapter:
    """Complete-case cosine retrieval using the shared embedding provider."""

    framework = "retrieval"

    def __init__(
        self,
        *,
        feedback_mode: str,
        provider: EmbeddingProvider,
        catalog: Iterable[StudyRecord] = (),
        items: Iterable[tuple[StudyRecord, dict[str, Any]]] = (),
        memory_profile: str = "legacy_v1",
        score_ceiling: float | None = None,
    ) -> None:
        self.feedback_mode = feedback_mode
        self.memory_profile = memory_profile
        self.score_ceiling = score_ceiling
        self.provider = provider
        self._items: list[tuple[StudyRecord, dict[str, Any]]] = list(items)
        self._catalog = {record.answer_id: record for record in catalog}

    def ingest(self, record: StudyRecord) -> dict[str, Any]:
        if any(existing.answer_id == record.answer_id for existing, _ in self._items):
            raise MemoryFrameworkError(
                f"retrieval store already contains {record.answer_id}"
            )
        payload = case_payload(
            record,
            self.feedback_mode,
            profile=self.memory_profile,
            score_ceiling=self.score_ceiling,
        )
        self._items.append((record, payload))
        return {
            "answer_id": record.answer_id,
            "payload_tokens": payload_token_counts(payload),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "feedback_mode": self.feedback_mode,
            "memory_profile": self.memory_profile,
            "score_ceiling": self.score_ceiling,
            "embedding_model": self.provider.model_name,
            "embedding_revision": self.provider.revision,
            "items": [
                {"answer_id": record.answer_id, "payload": payload}
                for record, payload in self._items
            ],
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        if snapshot.get("framework") != self.framework:
            raise MemoryFrameworkError(
                "snapshot framework does not match retrieval adapter"
            )
        if snapshot.get("feedback_mode") != self.feedback_mode:
            raise MemoryFrameworkError("snapshot feedback mode does not match adapter")
        if snapshot.get("memory_profile", "legacy_v1") != self.memory_profile:
            raise MemoryFrameworkError("snapshot memory profile does not match adapter")
        if snapshot.get("score_ceiling") != self.score_ceiling:
            raise MemoryFrameworkError("snapshot score ceiling does not match adapter")
        if snapshot.get("embedding_model") != self.provider.model_name:
            raise MemoryFrameworkError(
                "snapshot embedding model does not match adapter"
            )
        if snapshot.get("embedding_revision") != self.provider.revision:
            raise MemoryFrameworkError(
                "snapshot embedding revision does not match adapter"
            )
        by_id = self._catalog or {record.answer_id: record for record, _ in self._items}
        restored: list[tuple[StudyRecord, dict[str, Any]]] = []
        restored_ids: set[str] = set()
        for item in snapshot.get("items", []):
            answer_id = str(item["answer_id"])
            if answer_id in restored_ids:
                raise MemoryFrameworkError(
                    f"snapshot contains duplicate answer {answer_id}"
                )
            if answer_id not in by_id:
                raise MemoryFrameworkError(
                    f"snapshot references unknown answer {answer_id}"
                )
            restored.append((by_id[answer_id], dict(item["payload"])))
            restored_ids.add(answer_id)
        self._items = restored

    def retrieve(
        self, record: StudyRecord, top_k: int = RETRIEVAL_TOP_K
    ) -> list[RetrievedCase]:
        if not self._items:
            return []
        query = self.provider.encode(
            [_canonical(query_payload(record, profile=self.memory_profile))]
        )[0]
        vectors = self.provider.encode(
            [_canonical(payload) for _, payload in self._items]
        )
        scores = vectors @ query
        order = np.argsort(-scores, kind="stable")[:top_k]
        return [
            RetrievedCase(
                answer_id=self._items[index][0].answer_id,
                score=float(self._items[index][0].teacher_score),
                similarity=float(scores[index]),
                payload=self._items[index][1],
                native_relevance_score=float(scores[index]),
            )
            for index in order
        ]

    def inspect(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        return {
            **snapshot,
            "item_count": len(self._items),
            "stored_token_count": token_count(_canonical(snapshot)),
        }


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    if path.is_dir():
        for child in sorted(path.rglob("*")):
            if child.is_file():
                digest.update(str(child.relative_to(path)).encode("utf-8"))
                with child.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
        return digest.hexdigest()
    return hashlib.sha256(b"").hexdigest()


def _framework_version(module: Any, package_name: str) -> str:
    value = getattr(module, "__version__", None)
    if value:
        return str(value)
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def verify_official_memory_dependencies() -> dict[str, str]:
    """Verify the pinned official packages before a real worker starts."""
    required_modules = (
        "mem0",
        "agentic_memory.memory_system",
        "sentence_transformers",
        "chromadb",
        "sklearn",
        "rank_bm25",
        "nltk",
        "transformers",
        "litellm",
        "openai",
    )
    for module_name in required_modules:
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # pragma: no cover - optional runtime gate
            raise MemoryFrameworkError(
                f"memory-study dependency {module_name} is unavailable: {exc}"
            ) from exc

    revisions: dict[str, str] = {}
    for package_name, expected_commit in (
        ("mem0ai", MEM0_COMMIT),
        ("agentic-memory", AMEM_COMMIT),
    ):
        try:
            distribution = importlib.metadata.distribution(package_name)
            direct_url = distribution.read_text("direct_url.json")
            payload = json.loads(direct_url or "{}")
            commit = str(payload.get("vcs_info", {}).get("commit_id", ""))
        except Exception as exc:  # pragma: no cover - optional runtime gate
            raise MemoryFrameworkError(
                f"cannot verify {package_name} pinned revision: {exc}"
            ) from exc
        if commit != expected_commit:
            raise MemoryFrameworkError(
                f"{package_name} revision mismatch: expected {expected_commit}, "
                f"found {commit or 'unresolved'}"
            )
        revisions[package_name] = commit
    return revisions


class OfficialMemoryAdapter:
    """Adapter for the pinned upstream Mem0 and academic A-MEM APIs.

    The official framework remains responsible for extraction, evolution and
    ranking.  This boundary only supplies the framework's documented entry
    points and translates its native results into SAF records.  There is no
    legacy A-MEM module path and no retrieval fallback.
    """

    def __init__(
        self,
        *,
        framework: str,
        feedback_mode: str,
        config: dict[str, Any],
        runner: Any | None = None,
        runtime: dict[str, Any] | None = None,
        recorder: Any | None = None,
        snapshot_root: str | Path | None = None,
        stream_id: str | None = None,
        memory_profile: str = "legacy_v1",
        score_ceiling: float | None = None,
    ) -> None:
        if framework not in {"mem0", "amem"}:
            raise ValueError(f"unsupported official framework: {framework}")
        self.framework = framework
        self.feedback_mode = feedback_mode
        self.memory_profile = memory_profile
        self.score_ceiling = score_ceiling
        self.config = dict(config)
        self.stream_id = str(
            stream_id or self.config.get("stream_id") or "memory-stream"
        )
        self.snapshot_root = Path(
            snapshot_root or self.config.get("snapshot_root", tempfile.gettempdir())
        )
        self.snapshot_root.mkdir(parents=True, exist_ok=True)
        self._runner = runner
        self._runtime = dict(runtime or {})
        self._recorder = recorder
        self._history: list[str] = []
        self._module: Any
        self.framework_version = "unknown"
        self._backend: Any | None = None
        self._bridge_attached = False
        # Mem0 opens both Chroma and SQLite connections during construction.
        # A restored snapshot replaces those database files, so construct the
        # client only after restore() has installed the snapshot artifacts.
        # A-MEM restore mutates its in-memory note collection and still needs
        # an initialized backend here.
        if self.framework != "mem0":
            self._ensure_backend()

    def _ensure_backend(self) -> Any:
        if self._backend is None:
            self._backend = self._initialize_backend()
        if (
            self._runner is not None
            and self._recorder is not None
            and not self._bridge_attached
        ):
            self._attach_bridge()
        return self._backend

    def _initialize_backend(self) -> Any:
        try:
            if self.framework == "mem0":
                module = importlib.import_module("mem0")
                factory_module = importlib.import_module("mem0.utils.factory")
                llm_factory = getattr(factory_module, "LlmFactory", None)
                register_provider = getattr(llm_factory, "register_provider", None)
                if not callable(register_provider):
                    raise MemoryFrameworkError(
                        "Mem0 pinned package does not expose LlmFactory.register_provider()"
                    )
                register_provider(
                    "saf_codex_bridge",
                    "app.experiment.memory_study.memory._CodexPlaceholderLLM",
                )
                memory_cls = getattr(module, "Memory", None)
                factory = getattr(memory_cls, "from_config", None)
                if memory_cls is None or not callable(factory):
                    raise MemoryFrameworkError(
                        "Mem0 pinned package must expose Memory.from_config()"
                    )
                backend = factory(self._mem0_config())
                self._module = module
                self.framework_version = _framework_version(module, "mem0ai")
                return backend

            module = importlib.import_module("agentic_memory.memory_system")
            memory_cls = getattr(module, "AgenticMemorySystem", None)
            if memory_cls is None:
                raise MemoryFrameworkError(
                    "A-MEM pinned package must expose AgenticMemorySystem"
                )
            if self.config.get("embedding_backend") != "openai":
                raise MemoryFrameworkError(
                    "A-MEM requires the study-wide OpenAI-compatible API embedding backend"
                )
            # Upstream A-MEM eagerly creates two local SentenceTransformer
            # retrievers in its constructor.  Replace only that construction
            # hook, under a process lock, then install the API-backed mirror
            # before any memory operation can run.
            original_retriever = getattr(module, "ChromaRetriever", None)
            if original_retriever is None:
                raise MemoryFrameworkError(
                    "A-MEM pinned package does not expose its ChromaRetriever hook"
                )
            with _AMEM_CONSTRUCTOR_LOCK:
                module.ChromaRetriever = _UninitializedRetriever
                try:
                    backend = memory_cls(**self._amem_config())
                finally:
                    module.ChromaRetriever = original_retriever
            self._module = module
            self.framework_version = _framework_version(module, "agentic-memory")
            self._configure_amem_collection(backend)
            return backend
        except MemoryFrameworkError:
            raise
        except (ImportError, ModuleNotFoundError) as exc:
            raise MemoryFrameworkError(
                f"{self.framework} official package is unavailable; no fallback is permitted"
            ) from exc
        except Exception as exc:
            raise MemoryFrameworkError(
                f"cannot initialize pinned {self.framework} official API: {exc}"
            ) from exc

    def _mem0_config(self) -> dict[str, Any]:
        config = dict(self.config)
        vector = dict(config.get("vector_store", {}))
        vector_config = dict(vector.get("config", {}))
        vector_config.setdefault(
            "collection_name", _safe_chroma_collection_name(f"saf_{self.stream_id}")
        )
        vector["config"] = vector_config
        config["vector_store"] = vector
        config.setdefault("history_db_path", str(self.snapshot_root / "history.db"))
        # Mem0's factory needs an LLM provider object at construction time,
        # even though it is replaced immediately by the Codex bridge.  The
        # dummy key is never sent over the network.
        llm = dict(config.get("llm", {}))
        llm_config = dict(llm.get("config", {}))
        llm_config.setdefault("model", self._runtime.get("model", "gpt-6-luna"))
        llm_config.setdefault("api_key", "codex-exec-bridge-disabled")
        # ``litellm`` is a mem0-whitelisted provider whose constructor makes no
        # network call; mem0's ``LlmConfig`` validator rejects out-of-whitelist
        # providers (dynamic registers do not satisfy it).  The bridge replaces
        # this object before any completion is issued.
        llm.setdefault("provider", "litellm")
        llm["config"] = llm_config
        config["llm"] = llm
        return config

    def _amem_config(self) -> dict[str, Any]:
        config = dict(self.config.get("constructor", self.config))
        # In API mode this value is metadata only during construction because
        # the eager upstream local retriever hook is temporarily isolated.
        config.setdefault(
            "model_name",
            self.config.get("model_name", EMBEDDING_MODEL.rsplit("/", 1)[-1]),
        )
        # A-MEM's upstream constructor creates an LLM controller even though
        # its object is replaced immediately.  ``sglang`` is the constructor
        # path that creates no OpenAI/LiteLLM client and performs no network
        # request; all later completions use the Codex bridge.
        config.setdefault("llm_backend", "sglang")
        config.setdefault("llm_model", self._runtime.get("model", "gpt-6-luna"))
        config.setdefault("api_key", "codex-exec-bridge-disabled")
        return {
            key: value
            for key, value in config.items()
            if key
            in {
                "model_name",
                "llm_backend",
                "llm_model",
                "evo_threshold",
                "api_key",
                "sglang_host",
                "sglang_port",
            }
        }

    def _configure_amem_collection(self, backend: Any) -> None:
        collection_name = self.config.get("collection_name")
        if not collection_name:
            return
        collection_name = _safe_chroma_collection_name(collection_name)
        try:
            if self.config.get("embedding_backend") == "openai":
                # Install the line-faithful API mirror.  The frozen study
                # config carries the resolved endpoint/key; they never reach
                # the snapshot manifest.
                provider = OpenAICompatibleEmbeddingProvider(
                    model_name=self.config["embedding_model"],
                    revision=self.config["embedding_revision"],
                    api_base=self.config["embedding_api_base"],
                    api_key=self.config["embedding_api_key"],
                    cache_dir=self.snapshot_root / "embeddings",
                )
                backend.retriever = AmemCloudRetriever(
                    str(collection_name),
                    OpenAIEmbeddingFunction(provider),
                    persist_dir=str(self.snapshot_root / "chroma"),
                )
                return
            raise MemoryFrameworkError(
                "A-MEM local embedding is disabled by the frozen study protocol"
            )
        except MemoryFrameworkError:
            raise
        except Exception as exc:
            raise MemoryFrameworkError(
                f"cannot isolate A-MEM Chroma collection: {exc}"
            ) from exc

    def _attach_bridge(self) -> None:
        from app.experiment.memory_study.codex_bridge import CodexFrameworkLLMBridge

        bridge = CodexFrameworkLLMBridge(
            runner=self._runner, runtime=self._runtime, recorder=self._recorder
        )
        if self.framework == "mem0":
            if not hasattr(self._backend, "llm"):
                raise MemoryFrameworkError("Mem0 backend has no documented llm member")
            self._backend.llm = bridge
            self._bridge_attached = True
            return
        controller = getattr(self._backend, "llm_controller", None)
        if controller is None or not hasattr(controller, "llm"):
            raise MemoryFrameworkError(
                "A-MEM AgenticMemorySystem has no llm_controller.llm member"
            )
        controller.llm = bridge
        self._bridge_attached = True

    def _messages(self, record: StudyRecord) -> list[dict[str, str]]:
        return [
            {
                "role": "user",
                "content": _canonical(
                    case_payload(
                        record,
                        self.feedback_mode,
                        profile=self.memory_profile,
                        score_ceiling=self.score_ceiling,
                    )
                ),
            }
        ]

    def _raise_if_bridge_failed(self) -> None:
        """Turn a framework that swallowed an LLM error into a real failure.

        Both pinned frameworks catch their own LLM exceptions and carry on, so
        the only evidence that an ingest did not actually happen lives in the
        bridge's recorder.  Its message is quoted here because this text becomes
        ``MSCall.failure_summary``: without it the ledger could only say that an
        invocation had failed, never why, which is what reduced an entire broken
        pilot run to the single word "exited".
        """
        if self._recorder is None or not getattr(self._recorder, "failed", False):
            return
        detail = getattr(self._recorder, "last_error", None)
        phase = getattr(self._recorder, "failed_phase", None)
        where = f" in {phase}" if phase else ""
        why = f": {detail}" if detail else ""
        raise MemoryFrameworkError(
            f"{self.framework} swallowed a failed internal Codex invocation"
            f"{where}{why}"
        )

    def ingest(self, record: StudyRecord) -> dict[str, Any]:
        backend = self._ensure_backend()
        try:
            if self.framework == "mem0":
                add = getattr(backend, "add", None)
                if not callable(add):
                    raise MemoryFrameworkError("Mem0 backend has no add() operation")
                kwargs = {
                    "user_id": self.stream_id,
                    "metadata": {"answer_id": record.answer_id},
                    # ``infer=False`` stores the canonical case verbatim
                    # (``mem0.memory.main.Memory._add_to_vector_store``: one
                    # message -> one memory, no LLM call).  Mem0's default
                    # ``infer=True`` runs its fact-extraction prompt and
                    # rewrites the case into prose, which the V3-r2 blinded
                    # projection cannot parse back into
                    # student_answer/teacher_score.  Verbatim storage also
                    # keeps the mem0 condition comparable with the retrieval
                    # condition, which stores the same canonical JSON.
                    "infer": False,
                }
                result = add(self._messages(record), **kwargs)
            else:
                add_note = getattr(backend, "add_note", None)
                if not callable(add_note):
                    raise MemoryFrameworkError(
                        "A-MEM AgenticMemorySystem has no add_note() operation"
                    )
                kwargs = {"id": record.answer_id}
                result = add_note(
                    _canonical(
                        case_payload(
                            record,
                            self.feedback_mode,
                            profile=self.memory_profile,
                            score_ceiling=self.score_ceiling,
                        )
                    ),
                    **kwargs,
                )
        except MemoryFrameworkError:
            raise
        except Exception as exc:
            raise MemoryFrameworkError(
                f"{self.framework} official ingest failed: {exc}"
            ) from exc
        self._raise_if_bridge_failed()
        self._history.append(record.answer_id)
        return {"answer_id": record.answer_id, "result": result}

    def _artifact_sources(self) -> list[tuple[str, Path]]:
        if self.framework != "mem0":
            return []
        vector = self.config.get("vector_store", {})
        vector_config = vector.get("config", {}) if isinstance(vector, dict) else {}
        candidates = [
            ("vector_store", vector_config.get("path")),
            ("vector_store", vector_config.get("db_path")),
            ("history_db", self.config.get("history_db_path")),
        ]
        return [(kind, Path(str(value))) for kind, value in candidates if value]

    def _write_native_artifacts(self) -> tuple[list[dict[str, Any]], str | None]:
        sources = self._artifact_sources()
        if not sources:
            return [], None
        target = self.snapshot_root / "native-artifacts"
        temporary = Path(tempfile.mkdtemp(prefix="native-", dir=self.snapshot_root))
        entries: list[dict[str, Any]] = []
        try:
            for index, (kind, source) in enumerate(sources):
                if not source.exists():
                    continue
                destination = temporary / f"{index}-{source.name}"
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                entries.append(
                    {
                        "kind": kind,
                        "source": str(source),
                        "artifact": destination.name,
                        "sha256": _sha256_path(destination),
                    }
                )
            if target.exists():
                shutil.rmtree(target)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        if not entries:
            return [], None
        return entries, _sha256_path(target)

    def _note_snapshot(self) -> list[dict[str, Any]]:
        memories = getattr(self._ensure_backend(), "memories", None)
        if not isinstance(memories, dict):
            raise MemoryFrameworkError("A-MEM backend has no serializable memories map")
        return [
            {"id": str(memory_id), **dict(vars(note))}
            for memory_id, note in memories.items()
        ]

    def snapshot(self) -> dict[str, Any]:
        backend = self._ensure_backend()
        if self.framework == "mem0":
            native_artifacts, artifact_hash = self._write_native_artifacts()
            config = self._mem0_config()
            return {
                "framework": self.framework,
                "framework_version": self.framework_version,
                "framework_revision": MEM0_COMMIT,
                "feedback_mode": self.feedback_mode,
                "memory_profile": self.memory_profile,
                "score_ceiling": self.score_ceiling,
                "stream_id": self.stream_id,
                "config_manifest": _redact_secrets(
                    _portable_config(config, self.snapshot_root)
                ),
                "config_sha256": memory_hash(
                    _portable_config(config, self.snapshot_root)
                ),
                "history_answer_ids": list(self._history),
                "native_artifacts": native_artifacts,
                "artifact_sha256": artifact_hash,
            }
        return {
            "framework": self.framework,
            "framework_version": self.framework_version,
            "framework_revision": AMEM_COMMIT,
            "feedback_mode": self.feedback_mode,
            "memory_profile": self.memory_profile,
            "score_ceiling": self.score_ceiling,
            "stream_id": self.stream_id,
            "embedding_revision": self.config.get("embedding_revision"),
            "config_sha256": memory_hash(
                {
                    "constructor": self._amem_config(),
                    "embedding_revision": self.config.get("embedding_revision"),
                    "collection_name": self.config.get("collection_name"),
                }
            ),
            "history_answer_ids": list(self._history),
            "evo_cnt": int(getattr(backend, "evo_cnt", 0)),
            "evo_threshold": int(getattr(backend, "evo_threshold", 100)),
            "notes": self._note_snapshot(),
            "index_rebuild": {
                "collection_name": self.config.get("collection_name"),
                "model_name": self._amem_config().get("model_name"),
                "note_ids": [
                    str(memory_id)
                    for memory_id in getattr(backend, "memories", {})
                ],
            },
        }

    def _restore_native_artifacts(self, snapshot: dict[str, Any]) -> None:
        expected_sources = self._artifact_sources()
        for entry in snapshot.get("native_artifacts", []):
            artifact = self.snapshot_root / "native-artifacts" / str(entry["artifact"])
            source: Path | None = None
            # Native artifact names are emitted as ``<source-index>-<name>``;
            # prefer that stable index because the absolute source path is
            # expected to differ between a committed stream and a temp attempt.
            prefix, separator, _ = str(entry.get("artifact", "")).partition("-")
            if separator and prefix.isdigit():
                index = int(prefix)
                if 0 <= index < len(expected_sources):
                    kind, candidate = expected_sources[index]
                    if entry.get("kind") == kind:
                        source = candidate
            if source is None:
                raw_source = Path(str(entry.get("source", "")))
                matches = [
                    candidate
                    for kind, candidate in expected_sources
                    if kind == entry.get("kind") and candidate.name == raw_source.name
                ]
                if len(matches) == 1:
                    source = matches[0]
            if source is None:
                raise MemoryFrameworkError(
                    "Mem0 native snapshot source does not match adapter config"
                )
            if not artifact.exists() or _sha256_path(artifact) != entry.get("sha256"):
                raise MemoryFrameworkError(
                    "Mem0 native snapshot artifact hash mismatch"
                )
            temporary = source.with_name(f".{source.name}.restore-tmp")
            if temporary.exists():
                shutil.rmtree(temporary) if temporary.is_dir() else temporary.unlink()
            if artifact.is_dir():
                shutil.copytree(artifact, temporary)
                if source.exists():
                    shutil.rmtree(source)
            else:
                temporary.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(artifact, temporary)
                source.unlink(missing_ok=True)
            os.replace(temporary, source)

    def _rebuild_amem_index(self) -> None:
        collection_name = self.config.get("collection_name")
        if collection_name:
            self._configure_amem_collection(self._backend)
        add_document = getattr(
            getattr(self._backend, "retriever", None), "add_document", None
        )
        if not callable(add_document):
            raise MemoryFrameworkError(
                "A-MEM retriever cannot rebuild its Chroma index"
            )
        for note in getattr(self._backend, "memories", {}).values():
            metadata = {
                key: getattr(note, key)
                for key in (
                    "id",
                    "content",
                    "keywords",
                    "links",
                    "retrieval_count",
                    "timestamp",
                    "last_accessed",
                    "context",
                    "evolution_history",
                    "category",
                    "tags",
                )
                if hasattr(note, key)
            }
            add_document(note.content, metadata, note.id)

    def restore(self, snapshot: dict[str, Any]) -> None:
        if (
            snapshot.get("framework") != self.framework
            or snapshot.get("feedback_mode") != self.feedback_mode
            or snapshot.get("stream_id") != self.stream_id
            or snapshot.get("memory_profile", "legacy_v1") != self.memory_profile
            or snapshot.get("score_ceiling") != self.score_ceiling
        ):
            raise MemoryFrameworkError("official snapshot does not match adapter")
        expected_revision = MEM0_COMMIT if self.framework == "mem0" else AMEM_COMMIT
        if snapshot.get("framework_revision") != expected_revision:
            raise MemoryFrameworkError(
                "official framework revision does not match snapshot"
            )
        expected_config_hash = (
            memory_hash(_portable_config(self._mem0_config(), self.snapshot_root))
            if self.framework == "mem0"
            else memory_hash(
                {
                    "constructor": self._amem_config(),
                    "embedding_revision": self.config.get("embedding_revision"),
                    "collection_name": self.config.get("collection_name"),
                }
            )
        )
        if snapshot.get("config_sha256") != expected_config_hash:
            raise MemoryFrameworkError("official snapshot configuration hash mismatch")
        if self.framework == "mem0" and snapshot.get(
            "config_manifest"
        ) != _redact_secrets(_portable_config(self._mem0_config(), self.snapshot_root)):
            raise MemoryFrameworkError("Mem0 snapshot configuration manifest mismatch")
        snapshot_version = snapshot.get("framework_version")
        if self.framework != "mem0" and snapshot_version not in {
            None,
            "unknown",
            self.framework_version,
        }:
            raise MemoryFrameworkError(
                "official framework version does not match snapshot"
            )
        self._history = [str(value) for value in snapshot.get("history_answer_ids", [])]
        if len(self._history) != len(set(self._history)):
            raise MemoryFrameworkError("official snapshot contains duplicate history ids")
        if self.framework == "mem0":
            artifact_hash = snapshot.get("artifact_sha256")
            artifact_dir = self.snapshot_root / "native-artifacts"
            if artifact_hash and (
                not artifact_dir.is_dir() or _sha256_path(artifact_dir) != artifact_hash
            ):
                raise MemoryFrameworkError(
                    "Mem0 native snapshot aggregate hash mismatch"
                )
            self._restore_native_artifacts(snapshot)
            # The adapter has not opened Mem0 before this point. Initialize it
            # only after native SQLite/Chroma files are in place, so no cached
            # client can retain a handle to the pre-restore database inode.
            self._ensure_backend()
            if snapshot_version not in {None, "unknown", self.framework_version}:
                raise MemoryFrameworkError(
                    "official framework version does not match snapshot"
                )
            return
        backend = self._ensure_backend()
        note_cls = getattr(self._module, "MemoryNote", None)
        if note_cls is None:
            raise MemoryFrameworkError("A-MEM snapshot restore requires MemoryNote")
        backend.memories = {
            str(item["id"]): note_cls(
                **{key: value for key, value in item.items() if key != "id"},
                id=str(item["id"]),
            )
            for item in snapshot.get("notes", [])
        }
        backend.evo_cnt = int(snapshot.get("evo_cnt", 0))
        backend.evo_threshold = int(snapshot.get("evo_threshold", 100))
        index_rebuild = snapshot.get("index_rebuild") or {}
        expected_ids = [str(memory_id) for memory_id in backend.memories]
        if index_rebuild.get("collection_name") != self.config.get("collection_name"):
            raise MemoryFrameworkError(
                "A-MEM snapshot collection does not match adapter"
            )
        if index_rebuild.get("model_name") != self._amem_config().get("model_name"):
            raise MemoryFrameworkError(
                "A-MEM snapshot embedding model does not match adapter"
            )
        if index_rebuild.get("note_ids") != expected_ids:
            raise MemoryFrameworkError(
                "A-MEM snapshot index rebuild list does not match notes"
            )
        self._rebuild_amem_index()

    def retrieve(
        self, record: StudyRecord, top_k: int = RETRIEVAL_TOP_K
    ) -> list[RetrievedCase]:
        backend = self._ensure_backend()
        query = _canonical(query_payload(record, profile=self.memory_profile))
        try:
            if self.framework == "mem0":
                search = getattr(backend, "search", None)
                if not callable(search):
                    raise MemoryFrameworkError("Mem0 backend has no search() operation")
                raw = search(
                    query,
                    top_k=top_k,
                    filters={"user_id": self.stream_id},
                )
                values = (
                    raw if isinstance(raw, list) else (raw or {}).get("results", [])
                )
            else:
                search = getattr(backend, "search", None)
                if not callable(search):
                    raise MemoryFrameworkError(
                        "A-MEM AgenticMemorySystem has no search() operation"
                    )
                values = search(query, k=top_k)
        except MemoryFrameworkError:
            raise
        except Exception as exc:
            raise MemoryFrameworkError(
                f"{self.framework} official retrieval failed: {exc}"
            ) from exc
        if not isinstance(values, list):
            raise MemoryFrameworkError(
                f"{self.framework} search returned a non-list result"
            )
        result: list[RetrievedCase] = []
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                raise MemoryFrameworkError(
                    f"{self.framework} search returned invalid item"
                )
            metadata = (
                item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            )
            answer_id = (
                metadata.get("answer_id") or item.get("answer_id") or item.get("id")
            )
            if answer_id is None:
                raise MemoryFrameworkError(
                    f"{self.framework} search result {index} has no answer_id metadata"
                )
            similarity = item.get(
                "similarity", item.get("score", item.get("distance", 0))
            )
            result.append(
                RetrievedCase(
                    answer_id=str(answer_id),
                    score=float(item.get("score", 0)),
                    similarity=float(similarity),
                    payload=dict(item),
                    native_relevance_score=float(similarity),
                )
            )
        return result

    def inspect(self) -> dict[str, Any]:
        backend = self._ensure_backend()
        details = {
            "framework": self.framework,
            "framework_version": self.framework_version,
            "framework_revision": (
                MEM0_COMMIT if self.framework == "mem0" else AMEM_COMMIT
            ),
            "feedback_mode": self.feedback_mode,
            "stream_id": self.stream_id,
            "history_answer_ids": list(self._history),
        }
        if self.framework == "amem":
            details["item_count"] = len(getattr(backend, "memories", {}))
            details["evo_cnt"] = int(getattr(backend, "evo_cnt", 0))
        return details


def create_adapter(
    *,
    framework: str,
    feedback_mode: str,
    provider: EmbeddingProvider | None = None,
    catalog: Iterable[StudyRecord] = (),
    items: Iterable[tuple[StudyRecord, dict[str, Any]]] = (),
    config: dict[str, Any] | None = None,
    runner: Any | None = None,
    runtime: dict[str, Any] | None = None,
    recorder: Any | None = None,
    snapshot_root: str | Path | None = None,
    stream_id: str | None = None,
    memory_profile: str = "legacy_v1",
    score_ceiling: float | None = None,
) -> MemoryAdapter:
    if framework == "retrieval":
        if provider is None:
            raise MemoryFrameworkError(
                "retrieval adapter requires the shared embedding provider"
            )
        return CaseRetrievalAdapter(
            feedback_mode=feedback_mode,
            provider=provider,
            catalog=catalog,
            items=items,
            memory_profile=memory_profile,
            score_ceiling=score_ceiling,
        )
    return OfficialMemoryAdapter(
        framework=framework,
        feedback_mode=feedback_mode,
        config=config or {},
        runner=runner,
        runtime=runtime,
        recorder=recorder,
        snapshot_root=snapshot_root,
        stream_id=stream_id,
        memory_profile=memory_profile,
        score_ceiling=score_ceiling,
    )


__all__ = [
    "AmemCloudRetriever",
    "CaseRetrievalAdapter",
    "EmbeddingProvider",
    "MemoryAdapter",
    "MemoryEvidenceError",
    "MemoryFrameworkError",
    "OfficialMemoryAdapter",
    "OpenAICompatibleEmbeddingProvider",
    "OpenAIEmbeddingFunction",
    "RetrievedCase",
    "SentenceTransformerEmbeddingProvider",
    "case_payload",
    "create_adapter",
    "memory_hash",
    "payload_token_counts",
    "query_payload",
]
