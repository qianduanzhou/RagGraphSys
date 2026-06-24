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
