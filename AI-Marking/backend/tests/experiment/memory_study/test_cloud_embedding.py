"""Cloud (OpenAI-compatible) embedding provider unit tests.

These tests never touch the network: the OpenAI client is stubbed at the
``openai.OpenAI`` construction boundary inside the memory module.
"""

import numpy as np
import pytest

from app.experiment.memory_study import memory as memory_module
from app.experiment.memory_study.memory import (
    AmemCloudRetriever,
    MemoryFrameworkError,
    OpenAICompatibleEmbeddingProvider,
)


class _FakeEmbeddingsApi:
    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = vectors

    def create(self, *, input, model, encoding_format):
        assert encoding_format == "float"
        data = []
        for index, text in enumerate(input):
            vector = self.vectors[model]
            data.append(type("Item", (), {"embedding": vector, "index": index}))
        return type("Response", (), {"data": data})


class _FakeOpenAI:
    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs
        self.embeddings = _FakeEmbeddingsApi(
            {"doubao-embedding-vision": [0.1, 0.2, 0.3]}
        )


@pytest.fixture()
def fake_openai(monkeypatch):
    # Patch at import boundary: the provider imports openai lazily.
    import sys
    import types

    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = _FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", openai_stub)
    _FakeOpenAI.last_kwargs = {}
    return _FakeOpenAI


def _provider(tmp_path):
    return OpenAICompatibleEmbeddingProvider(
        model_name="doubao-embedding-vision",
        revision="doubao-embedding-vision-250615",
        api_base="https://ark.example.com/api/v3/",
        api_key="sk-test",
        cache_dir=tmp_path / "embeddings",
    )


def test_cloud_provider_requires_pinned_revision_and_endpoint(tmp_path):
    with pytest.raises(MemoryFrameworkError, match="revision"):
        OpenAICompatibleEmbeddingProvider(
            model_name="m",
            revision="unresolved",
            api_base="https://x",
            api_key="k",
            cache_dir=tmp_path,
        )
    with pytest.raises(MemoryFrameworkError, match="api_base"):
        OpenAICompatibleEmbeddingProvider(
            model_name="m",
            revision="rev",
            api_base="",
            api_key="k",
            cache_dir=tmp_path,
        )
    with pytest.raises(MemoryFrameworkError, match="api_key"):
        OpenAICompatibleEmbeddingProvider(
            model_name="m",
            revision="rev",
            api_base="https://x",
            api_key="",
            cache_dir=tmp_path,
        )


def test_cloud_provider_encodes_with_cache(fake_openai, tmp_path):
    provider = _provider(tmp_path)
    # the client is constructed lazily on first encode
    assert _FakeOpenAI.last_kwargs == {}

    first = provider.encode(["a", "b"])
    assert first.shape == (2, 3)
    assert np.allclose(first[0], [0.1, 0.2, 0.3])
    assert _FakeOpenAI.last_kwargs["base_url"] == "https://ark.example.com/api/v3"
    assert _FakeOpenAI.last_kwargs["api_key"] == "sk-test"

    # a second instance reuses the on-disk cache without new clients
    second = _provider(tmp_path)
    again = second.encode(["a", "b"])
    assert np.allclose(first, again)

    # revision changes the cache key, so vectors never mix across pins
    other = OpenAICompatibleEmbeddingProvider(
        model_name="doubao-embedding-vision",
        revision="doubao-embedding-vision-250999",
        api_base="https://ark.example.com/api/v3",
        api_key="sk-test",
        cache_dir=tmp_path / "embeddings",
    )
    assert other._cache_path("a") != provider._cache_path("a")


def test_cloud_provider_wraps_remote_errors(fake_openai, tmp_path):
    provider = _provider(tmp_path)

    def boom(*_args, **_kwargs):
        raise ConnectionError("network down")

    provider._client = type(
        "C", (), {"embeddings": type("E", (), {"create": staticmethod(boom)})}
    )
    with pytest.raises(MemoryFrameworkError, match="embedding request"):
        provider.encode(["a"])


def test_cloud_provider_chunks_requests_to_the_provider_limit(fake_openai, tmp_path):
    """Ark caps embeddings input at 10; 21 texts must go out as 3 requests."""
    requests: list[int] = []

    class _CountingEmbeddings:
        def create(self, *, input, model, encoding_format):
            requests.append(len(input))
            data = [
                type("Item", (), {"embedding": [0.1, 0.2, 0.3], "index": index})
                for index in range(len(input))
            ]
            return type("Response", (), {"data": data})

    provider = _provider(tmp_path)
    provider._client = type("C", (), {"embeddings": _CountingEmbeddings()})

    vectors = provider.encode([f"text-{i}" for i in range(21)])

    assert requests == [10, 10, 1]
    assert vectors.shape == (21, 3)

    # cached texts never re-hit the remote API
    provider.encode([f"text-{i}" for i in range(21)])
    assert requests == [10, 10, 1]


def test_amem_cloud_retriever_isolates_collection_and_adds(
    fake_openai, tmp_path, monkeypatch
):
    created = {}

    class _FakeCollection:
        def __init__(self, name, embedding_function):
            self.name = name
            self.embedding_function = embedding_function
            self.documents = []
            created[name] = self

        def add(self, documents, metadatas, ids):
            self.documents.append((documents[0], metadatas[0], ids[0]))

        def query(self, query_texts, n_results):
            return {
                "ids": [[doc[2] for doc in self.documents][:n_results]],
                "distances": [[0.1] * min(len(self.documents), n_results)],
                "metadatas": [[doc[1] for doc in self.documents][:n_results]],
            }

    class _FakeClient:
        def __init__(self, settings):
            self.settings = settings

        def get_or_create_collection(self, name, embedding_function):
            return _FakeCollection(name, embedding_function)

    class _FakePersistentClient(_FakeClient):
        def __init__(self, path, settings=None):
            self.path = path
            self.settings = settings

    chroma_stub = type("module", (), {})()
    chroma_stub.Client = _FakeClient
    chroma_stub.PersistentClient = _FakePersistentClient
    config_stub = type("module", (), {})()
    config_stub.Settings = lambda **kwargs: kwargs

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "chromadb":
            return chroma_stub
        if name == "chromadb.config":
            return config_stub
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(memory_module.importlib, "import_module", fake_import)

    provider = _provider(tmp_path)
    retriever = AmemCloudRetriever(
        "saf_stream", memory_module.OpenAIEmbeddingFunction(provider)
    )

    retriever.add_document(
        "content body",
        {"context": "General", "keywords": ["k1", "k2"], "tags": ["t1"], "id": "n1"},
        "n1",
    )
    stored_doc, stored_meta, stored_id = created["saf_stream"].documents[0]
    # upstream enhancement contract preserved
    assert "keywords: k1, k2" in stored_doc
    assert stored_meta["keywords"] == '["k1", "k2"]'
    assert stored_meta["enhanced_content"] == stored_doc
    assert stored_id == "n1"

    results = retriever.search("query", k=5)
    assert results["ids"] == [["n1"]]
    # numeric strings round-trip to floats like upstream search does
    assert results["distances"] == [[0.1]]
