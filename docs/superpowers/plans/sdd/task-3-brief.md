## Task 3: 多智能体节点（TDD）

**Files:**
- Create: `backend/multiagent/__init__.py`
- Create: `backend/multiagent/nodes.py`
- Test: `backend/tests/test_multiagent_nodes.py`

**Interfaces:**
- Consumes: `LLMService`（`chat`/`chat_stream`）、`RagService`（`build_context`）、`WebSearchService`（Task 2）、`Settings`、模块级 `get_stream_writer`
- Produces: `MultiAgentState`（TypedDict）、`MultiAgentNodes(llm, rag, web, settings)` 含方法 `dispatch`/`rag_agent`/`web_agent`/`integration`、函数 `route_after_dispatch(state) -> ["rag_agent_node","web_agent_node"]`。Task 4 的图组装它们。

- [ ] **Step 1: 创建包 `__init__.py`**

创建 `backend/multiagent/__init__.py`（**本步只放包文档字符串，不导入 graph**——`graph.py` 在 Task 4 才创建；`import multiagent.nodes` 会先执行 `__init__.py`，若此处导入不存在的 `multiagent.graph` 会让 Task 3 的测试在收集阶段就 ImportError）：

```python
"""多智能体问答：RAG + 联网 + 整合。"""
```

> Task 4 创建 `graph.py` 后，会把 `build_multi_agent_graph` 的导出补回本 `__init__.py`。

- [ ] **Step 2: 写失败测试**

创建 `backend/tests/test_multiagent_nodes.py`：

```python
"""MultiAgentNodes 单测：rag/web/llm 全用 fake，不触网。"""
import multiagent.nodes as ma


class FakeRag:
    def __init__(self, context="", sources=None, used_rag=False, raise_build=False):
        self._ctx = context
        self._sources = sources or []
        self._used = used_rag
        self._raise = raise_build

    def build_context(self, query, top_k=None):
        if self._raise:
            raise RuntimeError("rag down")
        return {"context": self._ctx, "sources": list(self._sources), "used_rag": self._used}


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


# --- route ---
def test_route_after_dispatch_fans_out():
    assert ma.route_after_dispatch({}) == ["rag_agent_node", "web_agent_node"]
```

> 说明：所有用例统一 `from tests.conftest import MockLLM` 注入 LLM 替身；`FakeRag`/`FakeWeb` 为本文件内联的轻量替身（实现 `build_context` / `search`）。

- [ ] **Step 3: 运行测试确认失败**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest backend/tests/test_multiagent_nodes.py -v
```
Expected: FAIL（`ModuleNotFoundError: No module named 'multiagent.nodes'`）。

- [ ] **Step 4: 实现 multiagent/nodes.py**

创建 `backend/multiagent/nodes.py`：

```python
"""多智能体 LangGraph：状态、节点实现与并行-汇合编排。

图拓扑::

    START -> dispatch_node --(fan-out)--> [rag_agent_node, web_agent_node]
                                                |                |
                                                +-> integration_node -> END

- rag_agent_node / web_agent_node 并行执行，各产出一份回答 + 来源；
- integration_node 汇合两者，综合后流式输出最终答案。
"""
from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from langgraph.config import get_stream_writer

from core.config import Settings
from core.logger import get_logger
from core.utils import truncate
from rag.rag_service import RagService
from services.llm_service import LLMService
from services.web_search_service import WebSearchService

logger = get_logger(__name__)


class MultiAgentState(TypedDict, total=False):
    """在多智能体图中流转的可变状态。``total=False`` 允许部分字段更新（与 nodes.GraphState 同构）。"""

    question: str
    history: List[Dict[str, str]]
    rag_agent_answer: str
    rag_agent_sources: List[Dict[str, Any]]
    web_agent_answer: str
    web_sources: List[Dict[str, Any]]
    used_rag: bool
    used_web: bool
    answer: str
    iterations: int
    streaming: bool


class MultiAgentNodes:
    """持有服务依赖，对外暴露多智能体图的节点可调用对象。"""

    def __init__(self, llm: LLMService, rag: RagService, web: WebSearchService, settings: Settings):
        self.llm = llm
        self.rag = rag
        self.web = web
        self.settings = settings

    # ------------------------------------------------------------------ #
    # dispatch_node — 启动多智能体（透传 + 记日志）
    # ------------------------------------------------------------------ #
    def dispatch(self, state: MultiAgentState) -> Dict[str, Any]:
        logger.info("multi_agent dispatch | question=%s", truncate(state["question"]))
        return {}

    # ------------------------------------------------------------------ #
    # rag_agent_node — 检索知识库并生成回答
    # ------------------------------------------------------------------ #
    def rag_agent(self, state: MultiAgentState) -> Dict[str, Any]:
        question = state["question"]
        try:
            retrieved = self.rag.build_context(question)
            context = retrieved.get("context", "") or ""
            sources = retrieved.get("sources", []) or []
            used_rag = bool(retrieved.get("used_rag", False))
        except Exception as exc:  # noqa: BLE001
            logger.exception("rag_agent retrieval failed: %s", exc)
            context, sources, used_rag = "", [], False

        system = (
            "你是知识库检索助手。仅根据下方「知识库资料」回答用户问题。"
            "若资料无关或不足以回答，明确回复「知识库中无相关内容」，不要编造。"
            "回答简洁、准确。"
        )
        system += f"\n\n知识库资料：\n{context}" if context else "\n\n（知识库中无相关资料）"

        try:
            answer = self.llm.chat([
                {"role": "system", "content": system},
                {"role": "user", "content": question},
            ])
        except Exception as exc:  # noqa: BLE001
            logger.exception("rag_agent generation failed: %s", exc)
            answer = "（知识库检索失败）"

        logger.info("rag_agent: used_rag=%s, sources=%d", used_rag, len(sources))
        return {"rag_agent_answer": answer, "rag_agent_sources": sources, "used_rag": used_rag}

    # ------------------------------------------------------------------ #
    # web_agent_node — 联网搜索并生成回答
    # ------------------------------------------------------------------ #
    def web_agent(self, state: MultiAgentState) -> Dict[str, Any]:
        question = state["question"]
        try:
            results = self.web.search(question)
        except Exception as exc:  # noqa: BLE001
            logger.exception("web_agent search failed: %s", exc)
            results = []

        used_web = bool(results)
        sources = [
            {
                "type": "web",
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
                "score": r.get("score"),
            }
            for r in results
        ]

        system = (
            "你是联网搜索助手。根据下方「搜索结果」回答用户问题。"
            "结果来自网络、未必准确；若与问题无关，回复「联网未找到相关结果」。"
            "用 Markdown 链接 [标题](url) 标注来源。回答简洁、准确。"
        )
        if results:
            ctx = "\n\n".join(
                f"[{i + 1}] {r.get('title', '')} ({r.get('url', '')})\n{r.get('content', '')}"
                for i, r in enumerate(results)
            )
            system += f"\n\n搜索结果：\n{ctx}"
        else:
            system += "\n\n（联网搜索无结果）"

        try:
            answer = self.llm.chat([
                {"role": "system", "content": system},
                {"role": "user", "content": question},
            ])
        except Exception as exc:  # noqa: BLE001
            logger.exception("web_agent generation failed: %s", exc)
            answer = "（联网搜索不可用）"

        logger.info("web_agent: used_web=%s, sources=%d", used_web, len(sources))
        return {"web_agent_answer": answer, "web_sources": sources, "used_web": used_web}

    # ------------------------------------------------------------------ #
    # integration_node — 综合两份回答，流式输出最终答案
    # ------------------------------------------------------------------ #
    def integration(self, state: MultiAgentState) -> Dict[str, Any]:
        question = state["question"]
        history = state.get("history", []) or []
        rag_answer = state.get("rag_agent_answer", "") or ""
        web_answer = state.get("web_agent_answer", "") or ""
        iterations = state.get("iterations", 0)

        system = (
            "你是整合助手。综合下方「知识库回答」与「联网回答」，给用户一个最终答案。\n"
            "规则：\n"
            "- 涉及用户上传文档的内容以「知识库回答」为准；最新/外部/通用信息以「联网回答」为准；\n"
            "- 知识库的文档内容用 Markdown 引用块（每行以 > 开头）标注；\n"
            "- 联网内容用 [标题](url) 链接标注来源；\n"
            "- 若某一方明确表示无相关内容（如「知识库中无相关内容」「联网未找到相关结果」），"
            "则以另一方为主，不要重复该说明；\n"
            "- 不要赘述两个来源的过程，直接给出整合后的答案。\n"
            "回答简洁、准确、有条理。"
        )
        system += f"\n\n知识库回答：\n{rag_answer or '（空）'}"
        system += f"\n\n联网回答：\n{web_answer or '（空）'}"

        messages = [{"role": "system", "content": system}] + history + [{"role": "user", "content": question}]

        try:
            if state.get("streaming"):
                writer = get_stream_writer()
                buffer: List[str] = []
                for token in self.llm.chat_stream(messages):
                    buffer.append(token)
                    writer({"type": "delta", "text": token})
                answer = "".join(buffer)
            else:
                answer = self.llm.chat(messages)
        except Exception as exc:  # noqa: BLE001
            logger.exception("integration generation failed: %s", exc)
            answer = f"抱歉，整合回答时出错：{exc}"

        logger.info("integration: produced answer (%d chars)", len(answer))
        return {"answer": answer, "iterations": iterations + 1}


# ---------------------------------------------------------------------- #
# 条件路由：dispatch 后并行扇出到两个 agent
# ---------------------------------------------------------------------- #
def route_after_dispatch(state: MultiAgentState) -> List[str]:
    """dispatch 后扇出到 RAG 与联网两个 agent（并行）。"""
    return ["rag_agent_node", "web_agent_node"]
```

> `MultiAgentState` 与现有 `nodes.GraphState` 同为 `TypedDict(total=False)`，是本仓库已验证的 StateGraph 状态写法。

- [ ] **Step 5: 运行测试确认通过**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest backend/tests/test_multiagent_nodes.py -v
```
Expected: 全部通过（约 13 项）。

---

