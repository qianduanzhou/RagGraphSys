## Task 4: 多智能体图（TDD）

**Files:**
- Create: `backend/multiagent/graph.py`
- Test: `backend/tests/test_multiagent_graph.py`

**Interfaces:**
- Consumes: `MultiAgentNodes`、`MultiAgentState`、`route_after_dispatch`（Task 3）
- Produces: `build_multi_agent_graph(llm, rag, web, settings) -> CompiledStateGraph`。`main.py`（Task 5）与 `multiagent/__init__.py` 导出它。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_multiagent_graph.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest backend/tests/test_multiagent_graph.py -v
```
Expected: FAIL（`ModuleNotFoundError: No module named 'multiagent.graph'`）。

- [ ] **Step 3: 实现 graph.py**

创建 `backend/multiagent/graph.py`：

```python
"""构建编译后的多智能体图（RAG + 联网 + 整合）。"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from core.config import Settings
from core.logger import get_logger
from multiagent.nodes import MultiAgentNodes, MultiAgentState, route_after_dispatch
from rag.rag_service import RagService
from services.llm_service import LLMService
from services.web_search_service import WebSearchService

logger = get_logger(__name__)


def build_multi_agent_graph(
    llm: LLMService,
    rag: RagService,
    web: WebSearchService,
    settings: Settings,
) -> CompiledStateGraph:
    """将多智能体节点与边连接为编译后的 StateGraph。"""
    nodes = MultiAgentNodes(llm=llm, rag=rag, web=web, settings=settings)

    graph = StateGraph(MultiAgentState)

    graph.add_node("dispatch_node", nodes.dispatch)
    graph.add_node("rag_agent_node", nodes.rag_agent)
    graph.add_node("web_agent_node", nodes.web_agent)
    graph.add_node("integration_node", nodes.integration)

    # START -> dispatch --(fan-out)--> [rag_agent, web_agent] --> integration -> END
    graph.add_edge(START, "dispatch_node")
    graph.add_conditional_edges("dispatch_node", route_after_dispatch)
    graph.add_edge("rag_agent_node", "integration_node")
    graph.add_edge("web_agent_node", "integration_node")
    graph.add_edge("integration_node", END)

    compiled = graph.compile()
    logger.info("MultiAgent graph compiled: dispatch->(rag,web)->integration")
    return compiled
```

随后更新 `backend/multiagent/__init__.py`，把 Task 3 留空的包导出补上（此时 `graph.py` 已存在，导入安全）：

```python
"""多智能体问答：RAG + 联网 + 整合。"""
from multiagent.graph import build_multi_agent_graph

__all__ = ["build_multi_agent_graph"]
```

- [ ] **Step 4: 运行测试确认通过**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest backend/tests/test_multiagent_graph.py -v
```
Expected: 2 passed。

- [ ] **Step 5: 跑全套后端测试，确认无回归**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest backend/tests -q
```
Expected: 全绿（含原有 RAG 测试 + 新增三个测试文件）。若有失败，优先排查 `multiagent/__init__.py` 的 graph 导入是否在收集阶段触发（本任务的测试只 import `multiagent.nodes` / `multiagent.graph`，不经过包 `__init__` 的再导出，应无碍）。

---

