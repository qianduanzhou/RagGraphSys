"""nodes.GraphNodes 及路由函数的测试。"""
from langgraph.graph import END

from nodes import (
    GraphNodes,
    make_route_after_reflection,
    route_after_llm,
    route_after_router,
)
from tests.conftest import MockLLM, MockNeo4j, MockQdrant, MockRag


def make_nodes(settings, **llm_kw):
    return GraphNodes(MockLLM(**llm_kw), MockRag(), settings)


# --- router_node（路由节点：始终检索）---
# 设计变更：原实现用 LLM 预判「是否检索」，对基于上传文档的具体提问会误判为
# DIRECT 而跳过检索。现改为始终检索——是否采用结果由 merge_node 的相关度阈值决定。
def test_router_always_retrieves(settings):
    # 即便 LLM 回应 "DIRECT"，router 也应判定需要检索
    nodes = make_nodes(settings, chat_resp="DIRECT")
    out = nodes.router({"question": "消息结构的核心是什么"})
    assert out == {"needs_rag": True, "used_rag": True}


def test_router_does_not_invoke_llm(settings):
    # router 不再调用 LLM 判断；即便 LLM 会抛错也直接返回检索
    llm = MockLLM(raise_on_chat=True)
    GraphNodes(llm, MockRag(), settings).router({"question": "q"})
    assert llm.chat_calls == 0


# --- qdrant_node / neo4j_node（检索节点）---
def test_qdrant_node(settings):
    rag = MockRag(MockQdrant(hits=[{"text": "a", "score": 0.9, "source": "d"}]))
    nodes = GraphNodes(MockLLM(), rag, settings)
    out = nodes.qdrant({"question": "q"})
    assert out["qdrant_results"] == [{"text": "a", "score": 0.9, "source": "d"}]


def test_qdrant_node_degrades(settings):
    rag = MockRag(MockQdrant(raise_search=True))
    nodes = GraphNodes(MockLLM(), rag, settings)
    assert nodes.qdrant({"question": "q"}) == {"qdrant_results": []}


def test_qdrant_node_passes_conversation_id(settings):
    class Q(MockQdrant):
        def search(self, query, top_k=None, owner=None, conversation_id=None):
            return [{"text": str(conversation_id), "score": 0.9, "source": "d"}]

    rag = MockRag(Q())
    nodes = GraphNodes(MockLLM(), rag, settings)
    out = nodes.qdrant({"question": "q", "owner": "alice", "conversation_id": "c1"})
    assert out["qdrant_results"][0]["text"] == "c1"


def test_neo4j_node_passes_conversation_id(settings):
    class N(MockNeo4j):
        def search(self, entities, limit=5, owner=None, conversation_id=None):
            return [{"head": str(conversation_id), "rel": "R", "tail": "Y"}]

    rag = MockRag(None, N())
    nodes = GraphNodes(MockLLM(keywords=["x"]), rag, settings)
    out = nodes.neo4j({"question": "q", "conversation_id": "c1"})
    assert out["neo4j_results"][0]["head"] == "c1"


def test_neo4j_node(settings):
    rag = MockRag(None, MockNeo4j(rels=[{"head": "X", "rel": "R", "tail": "Y"}]))
    nodes = GraphNodes(MockLLM(keywords=["X"]), rag, settings)
    out = nodes.neo4j({"question": "q"})
    assert out["neo4j_results"] == [{"head": "X", "rel": "R", "tail": "Y"}]


# --- merge_node（合并节点）---
def test_merge_node(settings):
    nodes = GraphNodes(MockLLM(), MockRag(), settings)
    out = nodes.merge({
        "qdrant_results": [{"text": "v", "score": 0.8, "source": "d"}],
        "neo4j_results": [{"head": "A", "rel": "R", "tail": "B"}],
    })
    assert out["used_rag"] is True
    assert len(out["sources"]) == 2
    assert out["context"]  # 非空


def test_merge_node_aggregate_replaces_with_scroll(settings):
    # 聚合触发：resolve_vector_hits 把命中改写为整文档分片，sources 数 = 分片数
    scroll = [{"text": f"c{i}", "score": None, "source": "jobs.pdf"} for i in range(5)]
    rag = MockRag(resolve=(scroll, True))
    nodes = GraphNodes(MockLLM(), rag, settings)
    out = nodes.merge({
        "question": "列出所有岗位",
        "qdrant_results": [{"text": "h", "score": 0.9, "source": "jobs.pdf"}],
        "neo4j_results": [],
    })
    assert out["aggregate"] is True
    assert len(out["sources"]) == 5
    assert out["used_rag"] is True


def test_merge_node_non_aggregate_passes_through(settings):
    # 非聚合：resolve_vector_hits 透传原命中、aggregate=False
    nodes = GraphNodes(MockLLM(), MockRag(), settings)
    out = nodes.merge({
        "question": "广州车站行车岗位要什么学历",
        "qdrant_results": [{"text": "v", "score": 0.8, "source": "d"}],
        "neo4j_results": [],
    })
    assert out["aggregate"] is False
    assert len(out["sources"]) == 1


def test_merge_node_passes_conversation_id_to_resolve(settings):
    seen = {}

    class R(MockRag):
        def resolve_vector_hits(self, question, vector_hits, owner=None, conversation_id=None):
            seen["cid"] = conversation_id
            return vector_hits, False

    nodes = GraphNodes(MockLLM(), R(), settings)
    nodes.merge({
        "question": "q",
        "qdrant_results": [{"text": "v", "score": 0.9, "source": "d"}],
        "conversation_id": "c1",
    })
    assert seen["cid"] == "c1"


# --- llm_node（生成节点）---
def test_llm_node_non_stream(settings):
    out = make_nodes(settings, chat_resp="最终答案").llm_generate(
        {"question": "q", "history": [], "context": "ctx", "iterations": 0}
    )
    assert out["answer"] == "最终答案"
    assert out["iterations"] == 1


def test_llm_node_empty_context_answers_directly(settings):
    # 无参考资料时（闲聊 / 非文档问题）仍应直接调用 LLM 给出回答，而非拒绝。
    llm = MockLLM(chat_resp="通用回答")
    nodes = GraphNodes(llm, MockRag(), settings)
    out = nodes.llm_generate({"question": "你好", "history": [], "context": "", "iterations": 0})
    assert out["answer"] == "通用回答"
    assert llm.chat_calls == 1


def test_llm_node_binds_aggregate_max_tokens(settings):
    # 聚合型回答放宽输出上限，避免长列表被截断
    llm = MockLLM(chat_resp="长列表")
    nodes = GraphNodes(llm, MockRag(), settings)
    nodes.llm_generate({
        "question": "列出所有岗位", "history": [], "context": "ctx", "iterations": 0,
        "aggregate": True,
    })
    assert llm.last_chat_kwargs.get("max_tokens") == settings.llm_max_tokens_aggregate


def test_llm_node_default_max_tokens_when_not_aggregate(settings):
    llm = MockLLM(chat_resp="答")
    nodes = GraphNodes(llm, MockRag(), settings)
    nodes.llm_generate({"question": "q", "history": [], "context": "ctx", "iterations": 0})
    assert llm.last_chat_kwargs.get("max_tokens") is None


def test_llm_node_stream_emits_via_writer(settings, monkeypatch):
    captured = []

    def _fake_get_writer():
        # get_stream_writer() 返回一个 writer，writer(payload) 记录 payload["text"]
        return lambda payload: captured.append(payload["text"])

    monkeypatch.setattr("nodes.get_stream_writer", _fake_get_writer)
    out = make_nodes(settings, stream_tokens=["答", "案"]).llm_generate({
        "question": "q", "history": [], "context": "ctx", "iterations": 0,
        "streaming": True,
    })
    assert out["answer"] == "答案"
    assert captured == ["答", "案"]


# --- reflection_node（反思节点）---
def test_reflection_calls_reflect(settings):
    out = make_nodes(settings, reflect_pass=False, reflect_feedback="too vague").reflection(
        {"question": "q", "answer": "a", "context": "c", "iterations": 1}
    )
    assert out["reflection_passed"] is False
    assert out["reflection_feedback"] == "too vague"


def test_reflection_force_pass_at_cap(settings):
    # 默认 max_reflection_iterations == 2；iterations==2 -> 强制通过
    out = make_nodes(settings, reflect_pass=False).reflection(
        {"question": "q", "answer": "a", "context": "c", "iterations": 2}
    )
    assert out["reflection_passed"] is True


# --- routing（路由）---
def test_route_after_router():
    assert route_after_router({"needs_rag": True}) == ["qdrant_node", "neo4j_node"]
    assert route_after_router({"needs_rag": False}) == "llm_node"
    assert route_after_router({}) == ["qdrant_node", "neo4j_node"]  # 默认为 True


def test_route_after_reflection():
    route = make_route_after_reflection(2)
    assert route({"reflection_passed": True}) == END
    assert route({"iterations": 2, "reflection_passed": False}) == END  # 上限
    assert route({"iterations": 1, "reflection_passed": False}) == "llm_node"


def test_route_after_llm():
    assert route_after_llm({"streaming": True}) == END
    assert route_after_llm({}) == "reflection_node"
