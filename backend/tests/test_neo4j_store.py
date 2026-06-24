"""rag.neo4j_store.Neo4jStore 的测试（driver 被替身替换）。"""
import pytest

from rag.neo4j_store import Neo4jStore
from tests.conftest import FakeDriver, FakeSession, FakeTx


def make_store(settings, session=None):
    store = Neo4jStore(settings)
    store.driver = FakeDriver(session or FakeSession())
    return store


def test_add_knowledge_merges_triples(settings):
    tx = FakeTx()
    store = make_store(settings, FakeSession(tx=tx))
    n = store.add_knowledge([("张三", "WORKS_FOR", "公司A"), ("公司A", "LOCATED_IN", "北京")])
    assert n == 2
    assert len(tx.calls) == 2
    cypher0, params0 = tx.calls[0]
    assert "MERGE (a:Entity {name: $head})" in cypher0
    assert "MERGE (a)-[:WORKS_FOR]->(b)" in cypher0
    assert params0 == {"head": "张三", "tail": "公司A"}


def test_add_knowledge_drops_empty_heads(settings):
    store = make_store(settings)
    assert store.add_knowledge([("", "R", "B"), ("A", "R", "")]) == 0


def test_add_knowledge_sanitises_rel_type(settings):
    tx = FakeTx()
    store = make_store(settings, FakeSession(tx=tx))
    store.add_knowledge([("A", "works for!", "B")])
    cypher = tx.calls[0][0]
    # [:...] 内部只允许出现 [A-Za-z0-9_]
    assert "MERGE (a)-[:WORKS_FOR]->(b)" in cypher


def test_search_returns_records(settings):
    records = [
        {"head": "张三", "rel": "WORKS_FOR", "tail": "公司A"},
        {"head": "公司A", "rel": "LOCATED_IN", "tail": "北京"},
    ]
    store = make_store(settings, FakeSession(records=records))
    out = store.search(["张三"], limit=5)
    assert out == records


def test_search_empty_entities_returns_empty(settings):
    store = make_store(settings)
    assert store.search([], limit=5) == []
    assert store.search([""], limit=5) == []


def test_count_entities(settings):
    store = make_store(settings, FakeSession(records=[{"c": 42}]))
    # 我们的 FakeSession.run 返回记录列表；count_entities 读取第一条记录的 ["c"]
    # 真实的 Cypher 会返回一条 key 为 "c" 的记录。
    assert store.count_entities() == 42


def test_add_knowledge_tags_source(settings):
    tx = FakeTx()
    store = make_store(settings, FakeSession(tx=tx))
    store.add_knowledge([("A", "R", "B")], source="doc.txt")
    cypher, params = tx.calls[0]
    assert "SET r.sources =" in cypher  # 关系被打上来源标记
    assert params["source"] == "doc.txt"
    assert params["head"] == "A"


def test_add_knowledge_without_source_omits_tag(settings):
    # 向后兼容：不传 source 时不写 sources 属性，走旧 MERGE
    tx = FakeTx()
    store = make_store(settings, FakeSession(tx=tx))
    store.add_knowledge([("A", "R", "B")])
    cypher = tx.calls[0][0]
    assert "SET r.sources" not in cypher


def test_delete_by_source_returns_deleted_count(settings):
    tx = FakeTx(records=[{"deleted": 3}])
    store = make_store(settings, FakeSession(tx=tx))
    n = store.delete_by_source("doc.txt")
    assert n == 3
    # 至少执行了两条 Cypher：移除来源/删关系 + 清理孤立节点
    assert len(tx.calls) >= 2
    assert "$source IN r.sources" in tx.calls[0][0]


def test_delete_by_source_handles_no_match(settings):
    # 单() 返回 None 时退化为 0，不抛错
    tx = FakeTx(records=[])
    store = make_store(settings, FakeSession(tx=tx))
    assert store.delete_by_source("none") == 0


def test_close_marks_driver_closed(settings):
    store = make_store(settings)
    store.close()
    assert store.driver.closed is True
