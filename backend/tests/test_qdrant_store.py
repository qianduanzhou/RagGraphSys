"""rag.qdrant_store.QdrantStore 的测试（QdrantClient 被替身替换）。"""
import pytest

from rag.qdrant_store import QdrantStore
from tests.conftest import FakeEmbedding, FakeQdrantClient, scored


def make_store(settings, embedding=None, client=None):
    store = QdrantStore(settings, embedding or FakeEmbedding())
    store.client = client or FakeQdrantClient()
    return store


def test_ensure_collection_creates_when_missing(settings):
    store = make_store(settings)
    store.ensure_collection()
    assert len(store.client.created) == 1
    vec_cfg = store.client.created[0]["vectors_config"]
    assert vec_cfg.size == 4  # FakeEmbedding 维度
    from qdrant_client import models
    assert vec_cfg.distance == models.Distance.COSINE


def test_ensure_collection_skips_when_exists(settings):
    store = make_store(settings)
    store.client.exists = True
    store.ensure_collection()
    assert store.client.created == []


def test_upsert_embeds_and_stores_points(settings):
    store = make_store(settings)
    count = store.upsert(["hello", "world"], metadatas=[{"source": "a"}, {"source": "b"}])
    assert count == 2
    assert len(store.client.points) == 2
    p0 = store.client.points[0]
    assert p0.payload["text"] == "hello"
    assert p0.payload["source"] == "a"


def test_upsert_length_mismatch_raises(settings):
    store = make_store(settings)
    with pytest.raises(ValueError):
        store.upsert(["a"], metadatas=[{}, {}])


def test_upsert_empty_is_noop(settings):
    store = make_store(settings)
    assert store.upsert([]) == 0
    assert store.client.points == []


def test_search_parses_scored_points(settings):
    store = make_store(settings)
    store.client.scored = [
        scored({"text": "命中A", "source": "d.txt"}, 0.91),
        scored({"text": "命中B", "source": "d.txt"}, 0.77),
    ]
    results = store.search("query", top_k=5)
    assert [r["text"] for r in results] == ["命中A", "命中B"]
    assert results[0]["score"] == pytest.approx(0.91)
    assert results[0]["source"] == "d.txt"


def test_count_returns_point_count(settings):
    store = make_store(settings)
    store.upsert(["a", "b"])
    assert store.count() == 2


def test_scan_all_returns_payloads(settings):
    store = make_store(settings)
    store.upsert(["a", "b"], metadatas=[{"source": "d1"}, {"source": "d2"}])
    points = store.scan_all()
    assert len(points) == 2
    assert {p["payload"]["source"] for p in points} == {"d1", "d2"}
    assert all("text" in p["payload"] for p in points)


def test_scan_all_paginates_across_batches(settings):
    # 用极小的 batch_size 强制分页，验证 offset 翻页能取到全部点
    store = make_store(settings)
    store.upsert([f"chunk-{i}" for i in range(7)])
    points = store.scan_all(batch_size=3)
    assert len(points) == 7


def test_scan_all_empty(settings):
    store = make_store(settings)
    assert store.scan_all() == []


def test_delete_by_source_removes_matching_points(settings):
    store = make_store(settings)
    store.upsert(
        ["a1", "a2", "b1"],
        metadatas=[{"source": "a.txt"}, {"source": "a.txt"}, {"source": "b.txt"}],
    )
    removed = store.delete_by_source("a.txt")
    assert removed == 2
    remaining = [p.payload["source"] for p in store.client.points]
    assert remaining == ["b.txt"]


def test_delete_by_source_no_match(settings):
    store = make_store(settings)
    store.upsert(["a"], metadatas=[{"source": "a.txt"}])
    assert store.delete_by_source("missing.txt") == 0
    assert len(store.client.points) == 1
