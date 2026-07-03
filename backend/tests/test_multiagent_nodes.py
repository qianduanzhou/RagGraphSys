"""MultiAgentNodes 单测：rag/web/llm 全用 fake，不触网。"""
import multiagent.nodes as ma


class FakeRag:
    def __init__(self, context="", sources=None, used_rag=False, raise_build=False, aggregate=False):
        self._ctx = context
        self._sources = sources or []
        self._used = used_rag
        self._raise = raise_build
        self._aggregate = aggregate
        self.last_build_kwargs = {}

    def build_context(self, query, top_k=None, owner=None, conversation_id=None):
        self.last_build_kwargs = {"owner": owner, "conversation_id": conversation_id}
        if self._raise:
            raise RuntimeError("rag down")
        return {
            "context": self._ctx,
            "sources": list(self._sources),
            "used_rag": self._used,
            "aggregate": self._aggregate,
        }


class FakeWeb:
    def __init__(self, results=None, raise_search=False):
        self._results = results or []
        self._raise = raise_search

    def search(self, query, max_results=None):
        if self._raise:
            raise RuntimeError("web down")
        return list(self._results)


def make_nodes(settings, llm, rag=None, web=None):
    return ma.MultiAgentNodes(llm, rag or FakeRag(), web or FakeWeb(), settings)


# --- dispatch ---
def test_dispatch_is_passthrough(settings):
    from tests.conftest import MockLLM
    out = make_nodes(settings, MockLLM()).dispatch({"question": "q"})
    assert out == {}


# --- rag_agent ---
def test_rag_agent_with_context(settings):
    from tests.conftest import MockLLM
    rag = FakeRag(context="文档内容", sources=[{"type": "qdrant", "content": "c"}], used_rag=True)
    nodes = make_nodes(settings, MockLLM(chat_resp="RAG答案"), rag=rag)
    out = nodes.rag_agent({"question": "q"})
    assert out["rag_agent_answer"] == "RAG答案"
    assert out["used_rag"] is True
    assert len(out["rag_agent_sources"]) == 1


def test_rag_agent_no_context(settings):
    from tests.conftest import MockLLM
    nodes = make_nodes(settings, MockLLM(chat_resp="无内容"), rag=FakeRag(used_rag=False))
    out = nodes.rag_agent({"question": "q"})
    assert out["rag_agent_answer"] == "无内容"
    assert out["used_rag"] is False


def test_rag_agent_degrades_on_retrieval_failure(settings):
    from tests.conftest import MockLLM
    nodes = make_nodes(settings, MockLLM(chat_resp="降级"), rag=FakeRag(raise_build=True))
    out = nodes.rag_agent({"question": "q"})
    assert out["used_rag"] is False
    assert out["rag_agent_sources"] == []
    assert out["rag_agent_answer"] == "降级"


def test_rag_agent_degrades_on_llm_failure(settings):
    from tests.conftest import MockLLM
    nodes = make_nodes(settings, MockLLM(raise_on_chat=True))
    out = nodes.rag_agent({"question": "q"})
    assert out["rag_agent_answer"] == "（知识库检索失败）"


def test_rag_agent_binds_aggregate_max_tokens(settings):
    # 整文档拉取（聚合）时放宽输出上限，并透传 rag_aggregate 给整合层
    from tests.conftest import MockLLM
    rag = FakeRag(context="整文档", sources=[], used_rag=True, aggregate=True)
    llm = MockLLM(chat_resp="长列表")
    nodes = make_nodes(settings, llm, rag=rag)
    out = nodes.rag_agent({"question": "列出所有岗位"})
    assert out["rag_aggregate"] is True
    assert llm.last_chat_kwargs.get("max_tokens") == settings.llm_max_tokens_aggregate


def test_rag_agent_default_max_tokens_when_not_aggregate(settings):
    from tests.conftest import MockLLM
    rag = FakeRag(context="片段", used_rag=True)
    llm = MockLLM(chat_resp="答")
    nodes = make_nodes(settings, llm, rag=rag)
    nodes.rag_agent({"question": "q"})
    assert llm.last_chat_kwargs.get("max_tokens") is None


def test_rag_agent_passes_conversation_id(settings):
    from tests.conftest import MockLLM
    rag = FakeRag(context="片段", used_rag=True)
    nodes = make_nodes(settings, MockLLM(chat_resp="答"), rag=rag)
    nodes.rag_agent({"question": "q", "owner": "alice", "conversation_id": "c1"})
    assert rag.last_build_kwargs == {"owner": "alice", "conversation_id": "c1"}


# --- web_agent ---
def test_web_agent_with_results(settings):
    from tests.conftest import MockLLM
    web = FakeWeb(results=[{"title": "T", "url": "http://x", "content": "c", "score": 0.9}])
    nodes = make_nodes(settings, MockLLM(chat_resp="联网答案"), web=web)
    out = nodes.web_agent({"question": "q"})
    assert out["web_agent_answer"] == "联网答案"
    assert out["used_web"] is True
    assert out["web_sources"][0]["type"] == "web"
    assert out["web_sources"][0]["url"] == "http://x"


def test_web_agent_empty_results(settings):
    from tests.conftest import MockLLM
    nodes = make_nodes(settings, MockLLM(chat_resp="无结果"), web=FakeWeb(results=[]))
    out = nodes.web_agent({"question": "q"})
    assert out["used_web"] is False
    assert out["web_sources"] == []


def test_web_agent_degrades_on_search_failure(settings):
    from tests.conftest import MockLLM
    nodes = make_nodes(settings, MockLLM(chat_resp="失败兜底"), web=FakeWeb(raise_search=True))
    out = nodes.web_agent({"question": "q"})
    assert out["used_web"] is False
    assert out["web_sources"] == []
    assert out["web_agent_answer"] == "失败兜底"


# --- integration ---
def test_integration_non_stream(settings):
    from tests.conftest import MockLLM
    nodes = make_nodes(settings, MockLLM(chat_resp="最终整合答案"))
    out = nodes.integration({
        "question": "q", "history": [],
        "rag_agent_answer": "A", "web_agent_answer": "B",
    })
    assert out["answer"] == "最终整合答案"
    assert out["iterations"] == 1


def test_integration_stream_writes_deltas(settings, monkeypatch):
    from tests.conftest import MockLLM
    written = []
    monkeypatch.setattr("multiagent.nodes.get_stream_writer", lambda: lambda payload: written.append(payload))
    nodes = make_nodes(settings, MockLLM(stream_tokens=["整", "合"]))
    out = nodes.integration({
        "question": "q", "history": [], "streaming": True,
        "rag_agent_answer": "A", "web_agent_answer": "B", "iterations": 0,
    })
    assert out["answer"] == "整合"
    assert written == [{"type": "delta", "text": "整"}, {"type": "delta", "text": "合"}]


def test_integration_degrades_on_llm_failure(settings):
    from tests.conftest import MockLLM
    nodes = make_nodes(settings, MockLLM(raise_on_chat=True))
    out = nodes.integration({"question": "q", "rag_agent_answer": "A", "web_agent_answer": "B"})
    assert out["answer"].startswith("抱歉，整合回答时出错")


def test_integration_binds_max_tokens_when_rag_aggregate(settings):
    # RAG 为整文档拉取时，整合层也放宽输出上限，避免长列表二次截断
    from tests.conftest import MockLLM
    llm = MockLLM(chat_resp="整合长列表")
    nodes = make_nodes(settings, llm)
    nodes.integration({
        "question": "q", "history": [],
        "rag_agent_answer": "A", "web_agent_answer": "B", "rag_aggregate": True,
    })
    assert llm.last_chat_kwargs.get("max_tokens") == settings.llm_max_tokens_aggregate


def test_integration_default_max_tokens_when_not_aggregate(settings):
    from tests.conftest import MockLLM
    llm = MockLLM(chat_resp="整合")
    nodes = make_nodes(settings, llm)
    nodes.integration({"question": "q", "history": [], "rag_agent_answer": "A", "web_agent_answer": "B"})
    assert llm.last_chat_kwargs.get("max_tokens") is None


# --- route ---
def test_route_after_dispatch_fans_out():
    assert ma.route_after_dispatch({}) == ["rag_agent_node", "web_agent_node"]
