"""rag.rag_service 的测试（merge_results + 使用 mock 的 build_context/retrieve）。"""
from rag.rag_service import RagService, _sample_graph_chunks, merge_results
from tests.conftest import MockLLM, MockNeo4j, MockQdrant, MockRag


# --------------------------------------------------------------------------- #
# merge_results（纯函数）
# --------------------------------------------------------------------------- #
def test_merge_results_empty():
    assert merge_results([], []) == ("", [])


def test_merge_results_qdrant_only():
    hits = [{"text": "片段A", "score": 0.9, "source": "doc.txt"}]
    context, sources = merge_results(hits, [])
    assert "片段A" in context
    assert "向量检索结果" in context
    assert sources == [{"type": "qdrant", "content": "片段A", "score": 0.9, "source": "doc.txt"}]


def test_merge_results_neo4j_only():
    rels = [{"head": "X", "rel": "WORKS_FOR", "tail": "Y"}]
    context, sources = merge_results([], rels)
    assert "X -[WORKS_FOR]-> Y" in context
    assert sources == [{"type": "neo4j", "content": "X -[WORKS_FOR]-> Y"}]


def test_merge_results_both_tags_distinctly():
    hits = [{"text": "v", "score": 0.5, "source": "s"}]
    rels = [{"head": "A", "rel": "R", "tail": "B"}]
    _, sources = merge_results(hits, rels)
    assert sources[0]["type"] == "qdrant"
    assert sources[1]["type"] == "neo4j"


def test_merge_results_filters_low_score():
    # 低于阈值的相关度结果被过滤，不进入上下文
    hits = [
        {"text": "相关片段", "score": 0.8, "source": "d"},
        {"text": "无关片段", "score": 0.10, "source": "d"},
    ]
    context, sources = merge_results(hits, [], score_threshold=0.35)
    assert len(sources) == 1
    assert sources[0]["content"] == "相关片段"
    assert "无关片段" not in context


def test_merge_results_default_threshold_keeps_all():
    # 默认阈值 0.0 不过滤，保持向后兼容
    hits = [{"text": "低分", "score": 0.05, "source": "d"}]
    _, sources = merge_results(hits, [])
    assert len(sources) == 1


def test_merge_results_none_score_passes_threshold():
    # 整文档拉取的分片 score=None，无条件放行阈值过滤
    hits = [{"text": "整文档片段", "score": None, "source": "d"}]
    context, sources = merge_results(hits, [], score_threshold=0.35)
    assert len(sources) == 1
    assert "整文档片段" in context


def test_merge_results_renders_na_for_none_score():
    hits = [{"text": "x", "score": None, "source": "d"}]
    context, _ = merge_results(hits, [])
    assert "score=n/a" in context


# --------------------------------------------------------------------------- #
# RagService.retrieve / build_context
# --------------------------------------------------------------------------- #
def _make_rag(qdrant=None, neo4j=None, llm=None, settings=None):
    return RagService(qdrant=qdrant or MockQdrant(), neo4j=neo4j or MockNeo4j(), llm=llm or MockLLM(), settings=settings)


def test_sample_graph_chunks_covers_head_middle_tail():
    chunks = [f"chunk-{i}" for i in range(20)]
    sampled = _sample_graph_chunks(chunks)

    assert sampled[:3] == ["chunk-0", "chunk-1", "chunk-2"]
    assert "chunk-10" in sampled
    assert sampled[-2:] == ["chunk-18", "chunk-19"]
    assert len(sampled) <= 8


def test_retrieve_returns_both_paths(settings):
    rag = _make_rag(
        qdrant=MockQdrant(hits=[{"text": "v1", "score": 0.8, "source": "d"}]),
        neo4j=MockNeo4j(rels=[{"head": "A", "rel": "R", "tail": "B"}]),
        llm=MockLLM(keywords=["A"]),
        settings=settings,
    )
    out = rag.retrieve("q")
    assert out["qdrant"] == [{"text": "v1", "score": 0.8, "source": "d"}]
    assert out["neo4j"] == [{"head": "A", "rel": "R", "tail": "B"}]


def test_retrieve_degrades_when_qdrant_raises(settings):
    rag = _make_rag(qdrant=MockQdrant(raise_search=True), settings=settings)
    out = rag.retrieve("q")
    assert out["qdrant"] == []
    # neo4j 路径仍会执行
    assert out["neo4j"] == [{"head": "X", "rel": "RELATES_TO", "tail": "Y"}]


def test_build_context_produces_sources_and_flag(settings):
    rag = _make_rag(
        qdrant=MockQdrant(hits=[{"text": "片段", "score": 0.9, "source": "d.txt"}]),
        neo4j=MockNeo4j(rels=[]),
        settings=settings,
    )
    built = rag.build_context("q")
    assert built["used_rag"] is True
    assert built["sources"][0]["type"] == "qdrant"
    assert "片段" in built["context"]


def test_build_context_no_hits_means_no_rag(settings):
    rag = _make_rag(
        qdrant=MockQdrant(hits=[]),
        neo4j=MockNeo4j(rels=[]),
        settings=settings,
    )
    built = rag.build_context("q")
    assert built["used_rag"] is False
    assert built["sources"] == []
    assert built["context"] == ""


# --------------------------------------------------------------------------- #
# 聚合型查询：resolve_vector_hits（经 retrieve / build_context 触发）
# --------------------------------------------------------------------------- #
def test_retrieve_aggregate_replaces_with_scroll(settings):
    # 5 条 top-k 命中均来自 jobs.pdf 且高分 -> 主导，整文档拉取 18 个分片
    jobs_hits = [{"text": f"h{i}", "score": 0.9, "source": "jobs.pdf"} for i in range(5)]
    scroll = [{"text": f"chunk-{i}", "score": None, "source": "jobs.pdf"} for i in range(18)]
    rag = _make_rag(
        qdrant=MockQdrant(hits=jobs_hits, scroll_hits=scroll),
        neo4j=MockNeo4j(rels=[]),
        settings=settings,
    )
    out = rag.retrieve("列出所有岗位")
    assert out["aggregate"] is True
    assert len(out["qdrant"]) == 18
    assert all(h["score"] is None for h in out["qdrant"])


def test_retrieve_aggregate_skipped_when_no_dominant_source(settings):
    # 5 条命中分散在 3 个文档、无人过半 -> 不触发整文档拉取
    scattered = [
        {"text": "a1", "score": 0.9, "source": "a.pdf"},
        {"text": "a2", "score": 0.9, "source": "a.pdf"},
        {"text": "b1", "score": 0.9, "source": "b.pdf"},
        {"text": "b2", "score": 0.9, "source": "b.pdf"},
        {"text": "c1", "score": 0.9, "source": "c.pdf"},
    ]
    rag = _make_rag(
        qdrant=MockQdrant(hits=scattered, scroll_hits=[{"text": "x"}]),
        settings=settings,
    )
    out = rag.retrieve("列出所有岗位")
    assert out["aggregate"] is False
    assert len(out["qdrant"]) == 5  # 原命中原样返回


def test_retrieve_aggregate_skipped_when_low_score(settings):
    # 聚合问句但命中分 0.1 < 阈值 -> 不触发（闲聊误触发兜底）
    low = [{"text": "h", "score": 0.1, "source": "jobs.pdf"}] * 5
    rag = _make_rag(
        qdrant=MockQdrant(hits=low, scroll_hits=[{"text": "x"}]),
        settings=settings,
    )
    out = rag.retrieve("列出所有岗位")
    assert out["aggregate"] is False


def test_retrieve_non_aggregate_unchanged(settings):
    hits = [{"text": "v1", "score": 0.8, "source": "d"}]
    rag = _make_rag(
        qdrant=MockQdrant(hits=hits, scroll_hits=[{"text": "should-not-be-used"}]),
        settings=settings,
    )
    out = rag.retrieve("广州车站行车岗位要什么学历")
    assert out["aggregate"] is False
    assert out["qdrant"] == hits


def test_build_context_propagates_aggregate(settings):
    jobs_hits = [{"text": f"h{i}", "score": 0.9, "source": "jobs.pdf"} for i in range(5)]
    scroll = [{"text": f"chunk-{i}", "score": None, "source": "jobs.pdf"} for i in range(3)]
    rag = _make_rag(
        qdrant=MockQdrant(hits=jobs_hits, scroll_hits=scroll),
        neo4j=MockNeo4j(rels=[]),
        settings=settings,
    )
    built = rag.build_context("列出所有岗位")
    assert built["aggregate"] is True
    assert built["used_rag"] is True
    assert len(built["sources"]) == 3


# --------------------------------------------------------------------------- #
# conversation_id 透传（多对话隔离）
# --------------------------------------------------------------------------- #
def test_ingest_text_passes_conversation_id_to_qdrant(settings):
    seen = {}

    class Q(MockQdrant):
        def upsert(self, texts, metadatas=None):
            seen["meta"] = metadatas
            return len(texts)

    rag = _make_rag(qdrant=Q(), settings=settings)
    rag.ingest_text("内容", source="d.pdf", owner="alice", conversation_id="c1")
    assert seen["meta"][0]["conversation_id"] == "c1"


def test_ingest_text_can_defer_graph_extraction(settings):
    class Q(MockQdrant):
        def upsert(self, texts, metadatas=None):
            return len(texts)

    class L(MockLLM):
        def extract_graph(self, text, max_triples=12):
            raise AssertionError("graph extraction should be deferred")

    rag = _make_rag(qdrant=Q(), llm=L(), settings=settings)
    stats = rag.ingest_text("alpha\n\nbeta\n\ngamma", source="d.pdf", extract_graph=False)

    assert stats["chunks"] >= 1
    assert stats["triples"] == 0
    assert stats["graph_chunks"]


def test_retrieve_passes_conversation_id(settings):
    class Q(MockQdrant):
        def search(self, query, top_k=None, owner=None, conversation_id=None):
            return [{"text": str(conversation_id), "score": 0.9, "source": "d"}]

    rag = _make_rag(qdrant=Q(), settings=settings)
    out = rag.retrieve("q", owner="alice", conversation_id="c1")
    assert out["qdrant"][0]["text"] == "c1"


def test_resolve_vector_hits_passes_conversation_to_scroll(settings):
    seen = {}

    class Q(MockQdrant):
        def search(self, query, top_k=None, owner=None, conversation_id=None):
            return [{"text": "h", "score": 0.9, "source": "d.pdf"}]

        def scroll_by_source(self, source, owner=None, conversation_id=None, limit=None):
            seen["conv"] = conversation_id
            return [{"text": "full", "score": None, "source": source}]

    rag = _make_rag(qdrant=Q(), neo4j=MockNeo4j(rels=[]), settings=settings)
    rag.retrieve("列出所有岗位", owner="alice", conversation_id="c1")
    assert seen["conv"] == "c1"


def test_delete_document_passes_conversation_id(settings):
    seen = {}

    class Q(MockQdrant):
        def delete_by_source(self, source, owner=None, conversation_id=None):
            seen["q"] = (source, conversation_id)
            return 1

    class N(MockNeo4j):
        def delete_by_source(self, source):
            seen["n_source"] = source
            return 0

    rag = _make_rag(qdrant=Q(), neo4j=N(), settings=settings)
    rag.delete_document("d.pdf", owner="alice", conversation_id="c1")
    assert seen["q"] == ("d.pdf", "c1")
    # Neo4j 来源标记含对话维度
    assert seen["n_source"] == "alice::c1::d.pdf"


def test_delete_conversation_calls_both_stores(settings):
    seen = {}

    class Q(MockQdrant):
        def delete_by_conversation(self, owner, conversation_id):
            seen["q"] = (owner, conversation_id)
            return 7

    class N(MockNeo4j):
        def delete_by_conversation(self, owner, conversation_id):
            seen["n"] = (owner, conversation_id)
            return 3

    rag = _make_rag(qdrant=Q(), neo4j=N(), settings=settings)
    out = rag.delete_conversation("alice", "c1")
    assert out == {"chunks": 7, "relations": 3}
    assert seen["q"] == ("alice", "c1") and seen["n"] == ("alice", "c1")


def test_ingest_text_requires_nonempty(settings):
    rag = _make_rag(settings=settings)
    import pytest
    with pytest.raises(ValueError):
        rag.ingest_text("   ")


def test_delete_document_calls_both_stores(settings):
    rag = _make_rag(
        qdrant=MockQdrant(deleted=5),
        neo4j=MockNeo4j(deleted=2),
        settings=settings,
    )
    out = rag.delete_document("doc.txt")
    assert out == {"source": "doc.txt", "chunks": 5, "relations": 2}
