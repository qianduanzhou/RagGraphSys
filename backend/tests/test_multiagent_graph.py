"""多智能体图 e2e：rag/web/llm 全 fake，串跑整张图。"""
import multiagent.nodes as ma
from multiagent.graph import build_multi_agent_graph
from tests.conftest import MockLLM


class FakeRag:
    def build_context(self, query, top_k=None):
        return {"context": "文档内容", "sources": [{"type": "qdrant", "content": "c"}], "used_rag": True}


class FakeWeb:
    def search(self, query, max_results=None):
        return [{"title": "T", "url": "http://x", "content": "c", "score": 0.9}]


def test_graph_runs_rag_and_web_then_integration(settings):
    graph = build_multi_agent_graph(MockLLM(chat_resp="子答案", stream_tokens=["最", "终"]), FakeRag(), FakeWeb(), settings)
    result = graph.invoke({"question": "q", "history": [], "iterations": 0})
    # 最终答案来自 integration（这里 MockLLM.chat 恒返回 chat_resp）
    assert result["answer"] == "子答案"
    assert result["rag_agent_answer"] == "子答案"
    assert result["web_agent_answer"] == "子答案"
    assert result["used_rag"] is True
    assert result["used_web"] is True
    assert result["rag_agent_sources"][0]["type"] == "qdrant"
    assert result["web_sources"][0]["type"] == "web"


def test_graph_degrades_when_both_empty(settings):
    class _EmptyRag:
        def build_context(self, query, top_k=None):
            return {"context": "", "sources": [], "used_rag": False}

    class _EmptyWeb:
        def search(self, query, max_results=None):
            return []

    graph = build_multi_agent_graph(MockLLM(chat_resp="整合空"), _EmptyRag(), _EmptyWeb(), settings)
    result = graph.invoke({"question": "q", "history": [], "iterations": 0})
    assert result["answer"] == "整合空"
    assert result["used_rag"] is False
    assert result["used_web"] is False
