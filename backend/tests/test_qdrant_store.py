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


def test_owner_filter_scopes_scan_count_and_delete(settings):
    store = make_store(settings)
    store.upsert(
        ["a1", "a2", "b1"],
        metadatas=[
            {"source": "same.txt", "owner": "alice"},
            {"source": "same.txt", "owner": "alice"},
            {"source": "same.txt", "owner": "bob"},
        ],
    )

    assert store.count(owner="alice") == 2
    assert len(store.scan_all(owner="alice")) == 2
    assert store.delete_by_source("same.txt", owner="alice") == 2
    assert store.count(owner="alice") == 0
    assert store.count(owner="bob") == 1


# --- scroll_by_source（整文档拉取，用于聚合查询）---
def _upsert_chunk(store, source, chunk_index, text, owner=None):
    meta = {"source": source, "chunk_index": chunk_index}
    if owner:
        meta["owner"] = owner
    store.upsert([text], metadatas=[meta])


def test_scroll_by_source_returns_ordered_by_chunk_index(settings):
    store = make_store(settings)
    # 故意乱序写入
    for idx, t in [(3, "c3"), (0, "c0"), (1, "c1"), (2, "c2")]:
        _upsert_chunk(store, "d.txt", idx, t)

    out = store.scroll_by_source("d.txt")
    assert [h["text"] for h in out] == ["c0", "c1", "c2", "c3"]


def test_scroll_by_source_search_shaped_with_none_score(settings):
    store = make_store(settings)
    _upsert_chunk(store, "d.txt", 0, "片段")
    out = store.scroll_by_source("d.txt")
    assert len(out) == 1
    hit = out[0]
    assert set(hit.keys()) >= {"text", "score", "source", "payload"}
    assert hit["score"] is None
    assert hit["source"] == "d.txt"
    assert hit["text"] == "片段"


def test_scroll_by_source_filters_by_source_and_owner(settings):
    store = make_store(settings)
    _upsert_chunk(store, "a.txt", 0, "a0", owner="alice")
    _upsert_chunk(store, "a.txt", 1, "a1", owner="alice")
    _upsert_chunk(store, "a.txt", 0, "a0-bob", owner="bob")
    _upsert_chunk(store, "b.txt", 0, "b0", owner="alice")

    out = store.scroll_by_source("a.txt", owner="alice")
    assert [h["text"] for h in out] == ["a0", "a1"]


def test_scroll_by_source_respects_limit(settings):
    store = make_store(settings)
    for i in range(10):
        _upsert_chunk(store, "d.txt", i, f"c{i}")
    out = store.scroll_by_source("d.txt", limit=3)
    assert [h["text"] for h in out] == ["c0", "c1", "c2"]


def test_scroll_by_source_missing_chunk_index_falls_back(settings):
    # 缺 chunk_index 的历史分片不崩，按 0 退位、保持插入序
    store = make_store(settings)
    store.upsert(["x", "y"], metadatas=[{"source": "d.txt"}, {"source": "d.txt"}])
    out = store.scroll_by_source("d.txt")
    assert [h["text"] for h in out] == ["x", "y"]


def test_scroll_by_source_empty(settings):
    store = make_store(settings)
    assert store.scroll_by_source("missing.txt") == []


# --- conversation_id 隔离（多对话）---
def test_search_filters_by_conversation(settings):
    store = make_store(settings)
    store.client.scored = [
        scored({"text": "a", "source": "d", "conversation_id": "c1"}, 0.9),
        scored({"text": "b", "source": "d", "conversation_id": "c2"}, 0.9),
    ]
    out = store.search("q", top_k=5, conversation_id="c1")
    assert [h["text"] for h in out] == ["a"]


def test_scroll_by_source_filters_by_conversation(settings):
    store = make_store(settings)
    store.upsert(["a"], metadatas=[{"source": "d.txt", "chunk_index": 0, "conversation_id": "c1"}])
    store.upsert(["b"], metadatas=[{"source": "d.txt", "chunk_index": 0, "conversation_id": "c2"}])
    assert [h["text"] for h in store.scroll_by_source("d.txt", conversation_id="c1")] == ["a"]


def test_delete_by_source_scoped_to_conversation(settings):
    store = make_store(settings)
    store.upsert(
        ["a1", "a2"],
        metadatas=[
            {"source": "d.txt", "chunk_index": 0, "conversation_id": "c1"},
            {"source": "d.txt", "chunk_index": 1, "conversation_id": "c2"},
        ],
    )
    assert store.delete_by_source("d.txt", conversation_id="c1") == 1
    remaining = [p.payload["conversation_id"] for p in store.client.points]
    assert remaining == ["c2"]


def test_delete_by_conversation_clears_only_that_conv(settings):
    store = make_store(settings)
    store.upsert(
        ["a", "b", "c"],
        metadatas=[
            {"source": "d1", "conversation_id": "c1"},
            {"source": "d2", "conversation_id": "c1"},
            {"source": "d3", "conversation_id": "c2"},
        ],
    )
    assert store.delete_by_conversation(owner=None, conversation_id="c1") == 2
    assert [p.payload["conversation_id"] for p in store.client.points] == ["c2"]


def test_search_without_conversation_id_no_filter(settings):
    # conversation_id=None 时不加过滤，兼容旧行为
    store = make_store(settings)
    store.client.scored = [
        scored({"text": "a", "source": "d", "conversation_id": "c1"}, 0.9),
        scored({"text": "b", "source": "d", "conversation_id": "c2"}, 0.8),
    ]
    out = store.search("q", top_k=5)
    assert len(out) == 2
