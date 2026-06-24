"""rag.rag_service 的测试（merge_results + 使用 mock 的 build_context/retrieve）。"""
from rag.rag_service import RagService, merge_results
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


# --------------------------------------------------------------------------- #
# RagService.retrieve / build_context
# --------------------------------------------------------------------------- #
def _make_rag(qdrant=None, neo4j=None, llm=None, settings=None):
    return RagService(qdrant=qdrant or MockQdrant(), neo4j=neo4j or MockNeo4j(), llm=llm or MockLLM(), settings=settings)


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
