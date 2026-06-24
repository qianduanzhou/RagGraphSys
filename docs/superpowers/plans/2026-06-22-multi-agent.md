# 多智能体问答（RAG + 联网 + 整合）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有 RAG 问答系统叠加可切换的「多智能体」模式：RAG 智能体与联网智能体（Tavily）并行产出各自回答，整合智能体综合后流式输出最终答案；默认仍为 RAG 问答，向后兼容。

**Architecture:** 新建一张独立的 LangGraph（`dispatch → [rag_agent, web_agent] 并行 → integration`），与现有 RAG 图并存；`api.py` 按请求 `mode` 选图。联网搜索封装在独立的 `WebSearchService`（与 `LLMService` 同属模型边界）。复用 `RagService.build_context()` 与 `LLMService.chat()/chat_stream()`，零重复逻辑。

**Tech Stack:** Python 3.11、FastAPI、langgraph 1.2.5、tavily-python（新增）、pytest；React 18 + Vite + TypeScript。

## Global Constraints

- 设计文档：`docs/superpowers/specs/2026-06-22-multi-agent-design.md`（本计划的权威依据）。
- 不改现有 RAG 图（`graph.build_graph` / `nodes.py`）、不改进库逻辑、不改向量/图谱存储；现有 RAG 测试须全部仍通过（无回归）。
- venv 解释器固定为 `backend/.venv/Scripts/python.exe`，所有 pytest 用它执行。
- `get_stream_writer` 在 `multiagent/nodes.py` 必须**模块级导入**（便于单测 `monkeypatch.setattr("multiagent.nodes.get_stream_writer", ...)`）。
- SSE 线上格式不变：`data: {json}\n\n`、frame 类型 `node`/`delta`/`done`/`error`。多智能体复用同一协议，仅新增 `node` 名与 `update` 字段。
- 联网搜索经独立 `WebSearchService` 实现，不绑定具体 LLM；无 Tavily key 时**优雅降级**（不报错、不 503），`web_agent` 返回「（联网搜索不可用）」。
- 前端无测试框架，验证用 `npm run build`（`tsc -b && vite build`）+ 手动运行。
- **项目非 git 仓库**（已确认 `fatal: not a git repository`），故各任务无 `git commit` 步骤，改以「相关测试通过 / 构建通过」为验收检查点。

---

## File Structure

| 文件 | 责任 | 本次改动 |
|---|---|---|
| `backend/core/config.py` | 配置 | 新增 `tavily_api_key`、`tavily_max_results` |
| `backend/requirements.txt` | 依赖 | 新增 `tavily-python==<已安装版>` |
| `backend/.env.example` | 配置示例 | 新增 `TAVILY_API_KEY=` 占位 |
| `backend/services/web_search_service.py` | **新建**：唯一 Tavily 边界 | `WebSearchService.search()` 归一化结果，失败返回 `[]` |
| `backend/multiagent/__init__.py` | **新建**：包导出 | 导出 `build_multi_agent_graph` |
| `backend/multiagent/nodes.py` | **新建**：多智能体状态+节点 | `MultiAgentState`、`MultiAgentNodes`（dispatch/rag_agent/web_agent/integration）、`route_after_dispatch` |
| `backend/multiagent/graph.py` | **新建**：编译多智能体图 | `build_multi_agent_graph()` |
| `backend/main.py` | 应用入口 / lifespan | 构造 `WebSearchService` + `build_multi_agent_graph`，挂 `app.state` |
| `backend/api.py` | HTTP/SSE 层 | `ChatRequest.mode`、`_select_graph`、`/health.web_search`、`_summarize_update` 新节点 |
| `backend/tests/test_web_search_service.py` | **新建** | Tavily 封装单测（注入 fake client） |
| `backend/tests/test_multiagent_nodes.py` | **新建** | 节点单测（fake rag/web/llm） |
| `backend/tests/test_multiagent_graph.py` | **新建** | 图 e2e（全 mock） |
| `backend/tests/test_api.py` | API 测试 | 扩展：mode 路由、health.web_search、新节点 summarize |
| `frontend/src/types.ts` | 类型 | `ChatMode`、`MULTI_AGENT_PIPELINE`、`SourceRef` web、`NodeUpdate.answer/used_web`、`ChatMessage` 扩展、`HealthResponse.web_search` |
| `frontend/src/api/client.ts` | HTTP/SSE 客户端 | `chat`/`chatStream` 携带 `mode` |
| `frontend/src/App.tsx` | 顶层状态/编排 | `mode` 状态、按模式选管线、`onNode` 写子答案 |
| `frontend/src/components/ChatWindow.tsx` | 输入区 | 加模式切换控件 |
| `frontend/src/components/MessageBubble.tsx` | 消息气泡 | 多智能体管线 + web 徽章 + 默认折叠原始回答面板 |
| `frontend/src/components/MessageBubble.css` | 样式 | 折叠面板、web 徽章样式 |
| `README.md` | 文档 | 多智能体模式说明、Tavily 配置、新 SSE 字段 |

---

## Task 1: 配置与 Tavily 依赖

**Files:**
- Modify: `backend/core/config.py`
- Modify: `backend/requirements.txt`
- Modify: `backend/.env.example`

**Interfaces:**
- Consumes: 无
- Produces: `Settings.tavily_api_key`、`Settings.tavily_max_results`；`tavily-python` 入依赖；后续任务据此构建 `WebSearchService`。

- [ ] **Step 1: config.py 新增两个配置项**

打开 `backend/core/config.py`，在 `# ---- Neo4j ----` 段（约第 54-57 行）**之后**、`# ---- RAG 流水线 ----` 段**之前**，插入新的一段：

```python
    # ---- 联网搜索（Tavily） ----
    # 多智能体模式下「联网智能体」使用。留空则联网搜索不可用，web_agent 自动降级。
    tavily_api_key: str = ""
    tavily_max_results: int = 5
```

- [ ] **Step 2: 安装 tavily-python 并读取实际版本**

Run（PowerShell）:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pip install tavily-python
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pip show tavily-python | Select-String "^Version:"
```
Expected: 第二条命令打印形如 `Version: 0.5.x`。记下该版本号 `<VER>`（后续 Step 3 用）。

- [ ] **Step 3: requirements.txt 固定版本**

打开 `backend/requirements.txt`，在 `neo4j==5.24.0` 行之后新增一行（用 Step 2 读到的真实 `<VER>` 替换）：

```
tavily-python==<VER>
```

- [ ] **Step 4: .env.example 增占位**

打开 `backend/.env.example`，在 Neo4j 段之后新增：

```
# ---- 联网搜索（Tavily） ----
# 多智能体模式需要；留空则联网智能体自动降级。免费额度：https://tavily.com
TAVILY_API_KEY=
TAVILY_MAX_RESULTS=5
```

- [ ] **Step 5: 验收检查点**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -c "from core.config import Settings; s=Settings(); print(s.tavily_api_key, s.tavily_max_results)"
```
Expected: 打印空串与 `5`（即 ` 5`），无导入错误。

---

## Task 2: WebSearchService（TDD）

**Files:**
- Create: `backend/services/web_search_service.py`
- Test: `backend/tests/test_web_search_service.py`

**Interfaces:**
- Consumes: `Settings`（Task 1 的 `tavily_api_key` / `tavily_max_results`）
- Produces: `WebSearchService(settings, client=None)`；`.available -> bool`；`.search(query, max_results=None) -> List[{title,url,content,score}]`（失败/不可用返回 `[]`）。后续 `MultiAgentNodes` 持有一个 `WebSearchService` 实例。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_web_search_service.py`：

```python
"""WebSearchService 测试：Tavily 客户端以 fake 注入，全程不触网。"""
from core.config import Settings
from services.web_search_service import WebSearchService


class FakeTavily:
    """模拟 tavily.TavilyClient：只实现本服务用到的 search。"""

    def __init__(self, results=None, raise_search=False):
        self._results = results or []
        self._raise = raise_search

    def search(self, query, max_results=5, search_depth="basic"):
        if self._raise:
            raise RuntimeError("tavily down")
        return {"results": self._results}


def test_search_normalizes_results():
    fake = FakeTavily(results=[{"title": "T", "url": "http://x", "content": "c", "score": 0.9}])
    svc = WebSearchService(Settings(llm_api_key="k"), client=fake)
    assert svc.search("q") == [{"title": "T", "url": "http://x", "content": "c", "score": 0.9}]


def test_search_empty_when_no_results():
    svc = WebSearchService(Settings(llm_api_key="k"), client=FakeTavily(results=[]))
    assert svc.search("q") == []


def test_search_returns_empty_on_exception():
    svc = WebSearchService(Settings(llm_api_key="k"), client=FakeTavily(raise_search=True))
    assert svc.search("q") == []


def test_search_empty_when_no_query():
    svc = WebSearchService(Settings(llm_api_key="k"), client=FakeTavily(results=[{"title": "T"}]))
    assert svc.search("") == []


def test_unavailable_when_no_key_and_no_client():
    svc = WebSearchService(Settings())  # 无 key、无注入 client
    assert svc.available is False
    assert svc.search("q") == []


def test_available_when_client_injected():
    svc = WebSearchService(Settings(llm_api_key="k"), client=FakeTavily())
    assert svc.available is True


def test_search_respects_max_results_override():
    captured = {}

    class _C(FakeTavily):
        def search(self, query, max_results=5, search_depth="basic"):
            captured["max_results"] = max_results
            return {"results": []}

    svc = WebSearchService(Settings(llm_api_key="k"), client=_C())
    svc.search("q", max_results=8)
    assert captured["max_results"] == 8
```

- [ ] **Step 2: 运行测试确认失败**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest backend/tests/test_web_search_service.py -v
```
Expected: FAIL（`ModuleNotFoundError: No module named 'services.web_search_service'`）。

- [ ] **Step 3: 实现 WebSearchService**

创建 `backend/services/web_search_service.py`：

```python
"""Web 搜索服务 —— 唯一封装 Tavily 的地方。

与 services/llm_service.py、services/embedding_service.py 同属「模型边界」：
业务与图代码只调用本服务的方法，不直接接触 Tavily 客户端。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.config import Settings
from core.logger import get_logger

logger = get_logger(__name__)


class WebSearchService:
    """基于 Tavily 的联网搜索封装。不可用时优雅降级（返回空结果）。"""

    def __init__(self, settings: Settings, client: Optional[Any] = None):
        self.settings = settings
        self._max_results = settings.tavily_max_results
        if client is not None:
            # 测试或自定义实现可注入
            self._client = client
        elif settings.tavily_api_key:
            try:
                from tavily import TavilyClient

                self._client = TavilyClient(api_key=settings.tavily_api_key)
                logger.info("WebSearch 初始化完成：max_results=%d", self._max_results)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Tavily 客户端初始化失败，联网搜索不可用：%s", exc)
                self._client = None
        else:
            logger.info("未配置 TAVILY_API_KEY，联网搜索不可用")
            self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def search(self, query: str, max_results: Optional[int] = None) -> List[Dict[str, Any]]:
        """执行联网搜索，返回归一化结果 ``[{title, url, content, score}]``。

        不可用、空 query 或异常时返回 ``[]``，不抛错（让上层 agent 优雅降级）。
        """
        if not self.available or not query:
            return []
        limit = max_results or self._max_results
        try:
            resp = self._client.search(
                query=query,
                max_results=limit,
                search_depth="basic",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tavily 搜索失败：%s", exc)
            return []
        results = resp.get("results", []) if isinstance(resp, dict) else []
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
                "score": r.get("score"),
            }
            for r in results
            if isinstance(r, dict)
        ]
```

- [ ] **Step 4: 运行测试确认通过**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest backend/tests/test_web_search_service.py -v
```
Expected: 7 passed。

---

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

## Task 5: main.py lifespan 装配

**Files:**
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `WebSearchService`（Task 2）、`build_multi_agent_graph`（Task 4）、`settings`
- Produces: `app.state.web`（`WebSearchService`）、`app.state.multi_agent_graph`（编译图）。Task 6 的 `_select_graph`/`/health` 读取它们。

- [ ] **Step 1: 修改 main.py 导入与 lifespan**

打开 `backend/main.py`。

(a) 在导入区（`from graph import build_graph` 之后）新增两行：

```python
from multiagent import build_multi_agent_graph
from services.web_search_service import WebSearchService
```

(b) 在 `lifespan` 函数内、`app.state.graph = build_graph(llm, rag, settings)`（第 53 行）**之后**、`logger.info("Application ready...")` **之前**，插入：

```python
    web = WebSearchService(settings)
    app.state.web = web
    app.state.multi_agent_graph = build_multi_agent_graph(llm, rag, web, settings)
```

（`web` 即使无 key 也会构造成功——`available=False`，`build_multi_agent_graph` 照常编译；运行时 `web_agent` 自动降级。）

- [ ] **Step 2: 验收检查点——应用能启动（降级模式）**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -c "import main; print('ok'); print('web=', main.app.state.__dict__.get('web') is not None or 'lazy'); print('multi=', main.app.state.__dict__.get('multi_agent_graph') is not None or 'lazy')"
```

> 注：`lifespan` 在 `TestClient` / 实际启动时才执行，`import main` 不触发它。上面的命令主要验证「导入 main 不报错」。真正的 lifespan 触发在 Task 6 的 TestClient 集成测试里验证。

Expected: 打印 `ok` 且无导入错误（`lif` 字段无所谓）。

- [ ] **Step 3: 用 TestClient 触发 lifespan 验证装配**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -c "from fastapi.testclient import TestClient; import main; c=TestClient(main.app); c.__enter__(); print('web=', main.app.state.web.available); print('multi=', main.app.state.multi_agent_graph is not None); c.__exit__(None,None,None)"
```
Expected: 打印 `web= False`（无 key）与 `multi= True`，无异常。

---

## Task 6: api.py 模式路由 + health + summarize（TDD）

**Files:**
- Modify: `backend/api.py`
- Test: `backend/tests/test_api.py`（扩展）

**Interfaces:**
- Consumes: `app.state.multi_agent_graph`、`app.state.web`（Task 5）
- Produces: `ChatRequest.mode`、`_select_graph(request, mode)`、`/health` 含 `web_search`、`_summarize_update` 支持新节点名。前端（Task 7+）依赖这些。

- [ ] **Step 1: 先写失败测试（追加到 test_api.py）**

打开 `backend/tests/test_api.py`，在文件末尾追加：

```python
# ------------------------------------------------------------------ #
# 多智能体模式（mode="multi"）
# ------------------------------------------------------------------ #
def test_summarize_rag_agent_includes_answer():
    out = _summarize_update("rag_agent_node", {
        "rag_agent_answer": "RA", "rag_agent_sources": [{"type": "qdrant", "content": "c"}], "used_rag": True,
    })
    assert out == {"answer": "RA", "sources": [{"type": "qdrant", "content": "c"}], "hits": 1, "used_rag": True}


def test_summarize_web_agent_includes_answer():
    out = _summarize_update("web_agent_node", {
        "web_agent_answer": "WA", "web_sources": [{"type": "web", "url": "http://x"}], "used_web": True,
    })
    assert out == {"answer": "WA", "sources": [{"type": "web", "url": "http://x"}], "hits": 1, "used_web": True}


def test_summarize_dispatch_is_empty():
    assert _summarize_update("dispatch_node", {}) == {}


def test_summarize_integration_iterations():
    assert _summarize_update("integration_node", {"answer": "x", "iterations": 1}) == {"iterations": 1}


def test_chat_multi_routes_to_multi_graph(client):
    class _MockMulti:
        def invoke(self, state):
            return {"answer": "multi-answer", "sources": [], "used_rag": True, "iterations": 1}

    main.app.state.multi_agent_graph = _MockMulti()
    r = client.post("/api/chat", json={"message": "hi", "history": [], "mode": "multi"})
    assert r.status_code == 200
    assert r.json()["answer"] == "multi-answer"


def test_chat_default_mode_is_rag(client):
    """不传 mode 时默认 rag，走原 graph。"""
    class _MockGraph:
        def invoke(self, state):
            return {"answer": "rag-answer", "sources": [], "used_rag": False, "iterations": 1}

    main.app.state.graph = _MockGraph()
    r = client.post("/api/chat", json={"message": "hi", "history": []})
    assert r.status_code == 200
    assert r.json()["answer"] == "rag-answer"


def test_chat_multi_503_when_graph_missing(client):
    main.app.state.multi_agent_graph = None
    r = client.post("/api/chat", json={"message": "hi", "history": [], "mode": "multi"})
    assert r.status_code == 503


def test_health_includes_web_search(client):
    class _Web:
        available = True

    main.app.state.web = _Web()
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["web_search"] is True


def test_chat_stream_multi_emits_agent_nodes(client):
    class _MockMultiStream:
        async def astream(self, initial, stream_mode=("updates",)):
            yield ("updates", {"dispatch_node": {}})
            yield ("updates", {"rag_agent_node": {"rag_agent_answer": "RA", "rag_agent_sources": [], "used_rag": True}})
            yield ("updates", {"web_agent_node": {"web_agent_answer": "WA", "web_sources": [], "used_web": False}})
            yield ("custom", {"type": "delta", "text": "整"})
            yield ("custom", {"type": "delta", "text": "合"})
            yield ("updates", {"integration_node": {"answer": "整合", "iterations": 1}})

    main.app.state.multi_agent_graph = _MockMultiStream()
    with client.stream("POST", "/api/chat/stream", json={"message": "q", "history": [], "mode": "multi"}) as r:
        body = "".join(r.iter_text())
    frames = []
    for block in body.split("\n\n"):
        data_lines = [ln for ln in block.split("\n") if ln.startswith("data:")]
        if data_lines:
            frames.append(json.loads(data_lines[0][len("data:"):].strip()))
    nodes = [f.get("node") for f in frames if f["type"] == "node"]
    assert "rag_agent_node" in nodes and "web_agent_node" in nodes and "integration_node" in nodes
    rag = next(f for f in frames if f.get("node") == "rag_agent_node")
    assert rag["update"]["answer"] == "RA"
    assert frames[-1]["type"] == "done"
```

- [ ] **Step 2: 运行测试确认失败**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest backend/tests/test_api.py -v
```
Expected: 新增的 9 项 FAIL（`_summarize_update` 不认识新节点 / `mode` 字段被忽略 / `web_search` 缺失等）。

- [ ] **Step 3: 修改 api.py**

打开 `backend/api.py`。

(a) 导入区 `from typing import ...` 增加 `Literal`：
```python
from typing import Any, Dict, List, Literal, Optional
```

(b) `ChatRequest` 增加 `mode` 字段：
```python
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    history: Optional[List[ChatMessage]] = Field(default_factory=list)
    mode: Literal["rag", "multi"] = "rag"
```

(c) 在 `_state` 辅助函数（约第 90-96 行）**之后**新增 `_select_graph`：
```python
def _select_graph(request: Request, mode: str):
    """按模式选择编译图。multi_agent_graph 缺失时返回 503（正常不触发，图始终构建）。"""
    if mode == "multi":
        graph = getattr(request.app.state, "multi_agent_graph", None)
        if graph is None:
            raise HTTPException(status_code=503, detail="多智能体模式不可用")
        return graph
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        raise HTTPException(status_code=503, detail="application not initialised")
    return graph
```

(d) `/chat` 改用 `_select_graph`：把函数体第一行 `graph, _ = _state(request)` 替换为 `graph = _select_graph(request, payload.mode)`，并把日志行改为 `logger.info("/chat mode=%s question=%s", payload.mode, payload.message[:120])`。

(e) `/chat/stream` 同理：把 `graph = getattr(request.app.state, "graph", None)` 与随后的 None 判断两行，替换为 `graph = _select_graph(request, payload.mode)`。

(f) `/health` 增加 `web_search`：在函数开头取 `web`，并在返回 dict 加字段。修改后的 `health` 函数体如下（替换原第 108-129 行整段）：
```python
@router.get("/health")
def health(request: Request) -> Dict[str, Any]:
    rag = getattr(request.app.state, "rag", None)
    web = getattr(request.app.state, "web", None)
    web_ok = bool(web.available) if web is not None else False
    qdrant_ok = neo4j_ok = False
    counts: Dict[str, Any] = {}
    if rag is not None:
        try:
            counts["qdrant_points"] = rag.qdrant.count()
            qdrant_ok = counts["qdrant_points"] >= 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("health: qdrant check failed: %s", exc)
        try:
            counts["neo4j_entities"] = rag.neo4j.count_entities()
            neo4j_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("health: neo4j check failed: %s", exc)
    return {
        "status": "ok" if (qdrant_ok and neo4j_ok) else "degraded",
        "qdrant": qdrant_ok,
        "neo4j": neo4j_ok,
        "web_search": web_ok,
        "counts": counts,
    }
```

(g) `_summarize_update` 增加新节点分支。在函数末尾 `return {}` **之前**插入：
```python
    if node == "dispatch_node":
        return {}
    if node == "rag_agent_node":
        sources = update.get("rag_agent_sources", []) or []
        return {
            "answer": update.get("rag_agent_answer", ""),
            "sources": sources,
            "hits": len(sources),
            "used_rag": update.get("used_rag"),
        }
    if node == "web_agent_node":
        sources = update.get("web_sources", []) or []
        return {
            "answer": update.get("web_agent_answer", ""),
            "sources": sources,
            "hits": len(sources),
            "used_web": update.get("used_web"),
        }
    if node == "integration_node":
        return {"iterations": update.get("iterations")}
```

- [ ] **Step 4: 运行测试确认通过**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest backend/tests/test_api.py -v
```
Expected: 全部通过（原有 + 新增 9 项）。

- [ ] **Step 5: 跑全套后端测试确认无回归**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest backend/tests -q
```
Expected: 全绿。

---

## Task 7: 前端类型 + 客户端

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`

**Interfaces:**
- Consumes: 后端新 SSE 契约（Task 6）
- Produces: `ChatMode` 类型、`MULTI_AGENT_PIPELINE`、扩展的 `SourceRef`/`NodeUpdate`/`ChatMessage`/`HealthResponse`；`chat`/`chatStream` 携带 `mode`。Task 8/9 的组件消费这些。

- [ ] **Step 1: 扩展 types.ts**

打开 `frontend/src/types.ts`。

(a) `SourceRef` 的 `type` 扩展（替换第 3-8 行）：
```typescript
export interface SourceRef {
  type: "qdrant" | "neo4j" | "web";
  content: string;
  score?: number;
  source?: string;
  // web 来源额外字段
  title?: string;
  url?: string;
}
```

(b) 在 `PIPELINE` 常量（第 19-25 行）**之后**新增模式类型与多智能体管线：
```typescript
export type ChatMode = "rag" | "multi";

/** 多智能体模式管线，key 与多智能体 LangGraph 节点名一一对应。 */
export const MULTI_AGENT_PIPELINE: ReadonlyArray<{ key: string; label: string }> = [
  { key: "dispatch_node", label: "调度" },
  { key: "rag_agent_node", label: "RAG智能体" },
  { key: "web_agent_node", label: "联网智能体" },
  { key: "integration_node", label: "整合" },
];
```

(c) `NodeUpdate` 增加 `answer` 与 `used_web`（替换第 27-35 行）：
```typescript
export interface NodeUpdate {
  needs_rag?: boolean;
  used_rag?: boolean;
  used_web?: boolean;
  hits?: number;
  sources?: SourceRef[];
  answer?: string; // 多智能体下两个 agent 的原始回答文本
  iterations?: number;
  passed?: boolean;
  feedback?: string;
}
```

(d) `ChatMessage` 增加 `mode` 与两个子答案（替换第 37-46 行）：
```typescript
export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  sources?: SourceRef[];
  usedRag?: boolean;
  usedWeb?: boolean;
  error?: boolean;
  streaming?: boolean;
  mode?: ChatMode;
  steps?: PipelineStep[];
  ragAgentAnswer?: string; // 多智能体：RAG 智能体原始回答（折叠面板）
  webAgentAnswer?: string; // 多智能体：联网智能体原始回答（折叠面板）
}
```

(e) `HealthResponse` 增加 `web_search`（替换第 97-105 行）：
```typescript
export interface HealthResponse {
  status: string;
  qdrant: boolean;
  neo4j: boolean;
  web_search: boolean;
  counts: {
    qdrant_points?: number;
    neo4j_entities?: number;
  };
}
```

- [ ] **Step 2: 扩展 client.ts 携带 mode**

打开 `frontend/src/api/client.ts`。

(a) 顶部 import 增加 `ChatMode`：
```typescript
import type {
  BatchIngestResponse,
  ChatHistoryItem,
  ChatMode,
  ChatResponse,
  DeleteDocResponse,
  HealthResponse,
  IngestResponse,
  NodeUpdate,
  SourceRef,
  StreamCallbacks,
  UploadedDoc,
} from "../types";
```

(b) `chat` 增加 `mode` 参数（替换第 25-36 行）：
```typescript
export async function chat(
  message: string,
  history: ChatHistoryItem[],
  mode: ChatMode = "rag"
): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history, mode }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
```

(c) `chatStream` 增加 `mode` 参数：把签名（第 109-113 行）
```typescript
export async function chatStream(
  message: string,
  history: ChatHistoryItem[],
  cb: StreamCallbacks
): Promise<void> {
```
改为：
```typescript
export async function chatStream(
  message: string,
  history: ChatHistoryItem[],
  cb: StreamCallbacks,
  mode: ChatMode = "rag"
): Promise<void> {
```
并把其内 `body: JSON.stringify({ message, history })` 改为 `body: JSON.stringify({ message, history, mode })`。

- [ ] **Step 3: 验收检查点——类型检查 + 构建**

Run:
```powershell
npm --prefix D:\project\customer\AI\RagGraphSys\frontend run build
```
Expected: `tsc -b` 与 `vite build` 均无错误。（此阶段 App.tsx 还没用到 `mode`，但类型导出应编译通过；若 App.tsx 报「chatStream 调用签名不匹配」，Task 8 会一并修复。）

---

## Task 8: 前端模式切换 + App.tsx 编排

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/ChatWindow.tsx`

**Interfaces:**
- Consumes: `ChatMode`、`MULTI_AGENT_PIPELINE`、`chatStream(..., mode)`（Task 7）、`HealthResponse.web_search`
- Produces: 全局 `mode` 状态；按模式选管线；`onNode` 把两个 agent 的原始回答写入消息；`ChatWindow` 渲染模式切换控件并把 `mode` + `webSearchAvailable` 上报。

- [ ] **Step 1: 读 App.tsx 与 ChatWindow.tsx 现状**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -c "print('see files')"
```
（占位——实际用 Read 工具读 `frontend/src/App.tsx` 全文与 `frontend/src/components/ChatWindow.tsx` 全文，确认 `handleSend`、`onNode`、消息构造、ChatWindow props 的现有写法，再据实改写下面的代码。下述代码以现有结构为前提；若 props 名有出入，按实际对齐。）

- [ ] **Step 2: App.tsx 引入 mode 状态**

打开 `frontend/src/App.tsx`。

(a) 顶部 import 增补：
```typescript
import type { ChatMode } from "./types";
import { MULTI_AGENT_PIPELINE, PIPELINE } from "./types";
```
（若已 import `PIPELINE`，改为同时引入 `MULTI_AGENT_PIPELINE` 与 `ChatMode`。）

(b) 在组件状态区（与 `messages` 同处）新增：
```typescript
const [mode, setMode] = useState<ChatMode>("rag");
const [webSearchAvailable, setWebSearchAvailable] = useState<boolean>(true);
```

(c) 在拉取 health 的 effect 里，更新 `webSearchAvailable`（找到 `fetchHealth` 调用处，在拿到 `h` 后加）：
```typescript
setWebSearchAvailable(h.web_search);
```

(d) 改造 `handleSend`（定位现有调用 `chatStream(...)` 处），关键三处改动：
  - 选管线：把构造 `steps` 时用的 `PIPELINE` 改为按模式选择：
    ```typescript
    const pipeline = mode === "multi" ? MULTI_AGENT_PIPELINE : PIPELINE;
    const steps = pipeline.map((p) => ({ ...p, status: "pending" as const }));
    ```
  - 在助手消息对象上加 `mode`：`mode,` 字段。
  - `chatStream` 调用加 `mode`：
    ```typescript
    await chatStream(text, history, {
      onNode: (node, update) => {
        setMessages((prev) => prev.map((m) => {
          if (m.id !== assistantId) return m;
          // 多智能体：把两个 agent 的原始回答 + 来源写入消息（供折叠面板/徽章）
          if (node === "rag_agent_node") {
            return { ...m, ragAgentAnswer: update.answer, sources: update.sources ?? m.sources, usedRag: update.used_rag };
          }
          if (node === "web_agent_node") {
            return { ...m, webAgentAnswer: update.answer, sources: [...(m.sources ?? []), ...(update.sources ?? [])], usedWeb: update.used_web };
          }
          // 通用步进器更新（RAG 与多智能体共用）
          const steps = m.steps?.map((s) =>
            s.key === node ? { ...s, status: "done" as const } : s
          );
          // 激活下一个未完成步骤
          const nextIdx = steps?.findIndex((s) => s.status === "pending");
          const finalSteps = steps?.map((s, i) =>
            i === nextIdx ? { ...s, status: "active" as const } : s
          );
          return { ...m, steps: finalSteps };
        }));
      },
      onDelta: (t) => { /* 现有：追加 token 到助手消息 content */ },
      onDone: () => { /* 现有：标记 streaming=false、所有 step=done */ },
      onError: (msg) => { /* 现有 */ },
    }, mode);  // ← 注意末尾传入 mode
    ```

> 上面 `onNode` 用「替换」语气给出目标逻辑；落地时**保留现有 `onNode/onDelta/onDone/onError` 的其余实现**，只新增 `rag_agent_node`/`web_agent_node` 两个分支并把 `mode` 透传。若现有步进器更新逻辑更复杂，按其结构合并，不要破坏 RAG 模式行为。

(e) 把 `mode`、`setMode`、`webSearchAvailable` 作为 props 传给 `<ChatWindow ...>`。

- [ ] **Step 3: ChatWindow.tsx 增加模式切换控件**

打开 `frontend/src/components/ChatWindow.tsx`。

(a) 在 props 类型里增加：
```typescript
mode: ChatMode;
onModeChange: (m: ChatMode) => void;
webSearchAvailable: boolean;
```
并在文件顶部 `import type { ChatMode } from "../types";`。

(b) 在输入框上方（或对话框顶部）渲染分段控件：
```tsx
<div className="mode-switch" role="tablist" aria-label="问答模式">
  <button
    type="button"
    role="tab"
    aria-selected={mode === "rag"}
    className={mode === "rag" ? "active" : ""}
    onClick={() => onModeChange("rag")}
  >
    RAG问答
  </button>
  <button
    type="button"
    role="tab"
    aria-selected={mode === "multi"}
    className={mode === "multi" ? "active" : ""}
    disabled={!webSearchAvailable}
    title={webSearchAvailable ? "多智能体：RAG + 联网 + 整合" : "未配置 TAVILY_API_KEY"}
    onClick={() => onModeChange("multi")}
  >
    多智能体
  </button>
</div>
```

- [ ] **Step 4: 验收检查点——构建**

Run:
```powershell
npm --prefix D:\project\customer\AI\RagGraphSys\frontend run build
```
Expected: tsc + vite build 通过。若有 TS 报错（如 ChatWindow 漏接 props、App 里 `steps` 类型不匹配），就地修正。

---

## Task 9: 前端 MessageBubble 多智能体渲染 + 折叠面板

**Files:**
- Modify: `frontend/src/components/MessageBubble.tsx`
- Modify: `frontend/src/components/MessageBubble.css`

**Interfaces:**
- Consumes: 消息的 `mode`、`steps`、`ragAgentAnswer`、`webAgentAnswer`、`sources`（含 `type:"web"`）
- Produces: 多智能体消息渲染多智能体管线步进器、web 来源链接徽章、默认折叠的「RAG 原始回答」「联网原始回答」面板。

- [ ] **Step 1: 读 MessageBubble 现状**

用 Read 工具读 `frontend/src/components/MessageBubble.tsx` 与 `MessageBubble.css` 全文，确认：管线步进器渲染、`sources` 徽章渲染、Markdown 渲染的现有结构。下面的改动据实合并。

- [ ] **Step 2: web 来源徽章**

在 `MessageBubble.tsx` 的来源徽章渲染处（现有按 `source.type === "qdrant" | "neo4j"` 分支处），新增 web 分支：web 徽章渲染为可点击链接：
```tsx
{source.type === "web" ? (
  <a
    key={i}
    className="source-badge web"
    href={source.url}
    target="_blank"
    rel="noopener noreferrer"
    title={source.title || source.url}
  >
    🔗 {source.title || source.url}
  </a>
) : (
  /* 现有的 qdrant / neo4j 徽章渲染 */
)}
```

- [ ] **Step 3: 默认折叠的原始回答面板**

在助手消息气泡内、最终答案**之下**，当 `message.mode === "multi"` 且存在子答案时，渲染两个折叠面板（默认收起）：
```tsx
{message.mode === "multi" && (message.ragAgentAnswer || message.webAgentAnswer) && (
  <div className="agent-panels">
    {message.ragAgentAnswer && (
      <details className="agent-panel">
        <summary>📄 查看 RAG 智能体原始回答</summary>
        <div className="agent-panel-body">{message.ragAgentAnswer}</div>
      </details>
    )}
    {message.webAgentAnswer && (
      <details className="agent-panel">
        <summary>🌐 查看联网智能体原始回答</summary>
        <div className="agent-panel-body">{message.webAgentAnswer}</div>
      </details>
    )}
  </div>
)}
```
> `<details>` 原生默认折叠，无需 JS 状态；`<summary>` 为可点击标题。

- [ ] **Step 4: 步进器对多智能体管线的兼容**

确认 `MessageBubble` 渲染 `message.steps` 时是**按 message 自带的 steps 数组**渲染（而非硬编码 `PIPELINE`）。若现有代码硬编码了 5 步，改为读 `message.steps ?? []`（App.tsx 已按模式填充了对应 steps）。这样 RAG 消息显示 5 步、多智能体消息显示 4 步。

- [ ] **Step 5: 补充样式（MessageBubble.css）**

在 `MessageBubble.css` 末尾追加：
```css
.mode-switch {
  display: inline-flex;
  gap: 0;
  border: 1px solid var(--border, #d0d7de);
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 8px;
}
.mode-switch button {
  padding: 6px 14px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 13px;
  color: inherit;
}
.mode-switch button.active {
  background: var(--accent, #2563eb);
  color: #fff;
}
.mode-switch button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.source-badge.web {
  color: var(--link, #2563eb);
  text-decoration: none;
  border: 1px solid var(--border, #d0d7de);
  border-radius: 6px;
  padding: 2px 8px;
  font-size: 12px;
}
.source-badge.web:hover {
  background: rgba(37, 99, 235, 0.08);
}

.agent-panels {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.agent-panel {
  border: 1px solid var(--border, #d0d7de);
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 13px;
}
.agent-panel summary {
  cursor: pointer;
  color: var(--muted, #57606a);
  user-select: none;
}
.agent-panel-body {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed var(--border, #d0d7de);
  white-space: pre-wrap;
  line-height: 1.6;
}
```

- [ ] **Step 6: 验收检查点——构建**

Run:
```powershell
npm --prefix D:\project\customer\AI\RagGraphSys\frontend run build
```
Expected: 通过。

- [ ] **Step 7: 端到端手动验证（需后端 + Tavily key）**

启动后端与前端（用户提供 Tavily key 写入 `backend/.env` 的 `TAVILY_API_KEY`）：
```powershell
# 后端
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" D:\project\customer\AI\RagGraphSys\backend\main.py
# 前端（另一终端）
npm --prefix D:\project\customer\AI\RagGraphSys\frontend run dev
```
验证清单：
- [ ] `/api/health` 返回 `web_search: true`。
- [ ] 切到「多智能体」模式，提问一个知识库里没有、但网络能查到的问题（如「今天日期」「某最新新闻」）。
- [ ] 管线步进器依次点亮 调度→RAG智能体/联网智能体（并行）→整合。
- [ ] 主气泡流式出现整合后的答案；答案里有 `[标题](url)` 网页链接。
- [ ] 来源区出现 web 链接徽章，可点击跳转。
- [ ] 答案下方两个折叠面板默认收起，点开分别能看到 RAG 与联网的原始回答。
- [ ] 切回「RAG问答」模式，行为与改动前一致（无回归）。
- [ ] 无 Tavily key 时：「多智能体」按钮置灰、tooltip 提示；强行发 mode=multi 不报 500，联网降级。

---

## Task 10: README 文档更新

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: 全部前序任务
- Produces: 文档反映新能力（多智能体模式、Tavily 配置、新 SSE 字段）。

- [ ] **Step 1: 在 README 适当位置（技术栈表 / 功能列表 / .env 字段表 / API 表 / SSE 帧格式 各节）增补**

(a) 功能列表增加一条：
```
- 多智能体问答模式：RAG 智能体 + 联网智能体（Tavily）并行检索，整合智能体综合后流式输出（前端可切换，默认 RAG）。
```

(b) `.env` 字段表增加：
```
| TAVILY_API_KEY    | Tavily 联网搜索 API key（多智能体模式用；留空则联网自动降级） | （空）        |
| TAVILY_MAX_RESULTS| 联网搜索返回条数上限                                          | 5             |
```

(c) API 表 `/api/chat`、`/api/chat/stream` 备注里加：请求体新增可选字段 `mode`（`"rag"` 默认 / `"multi"`）。

(d) SSE 帧格式节，`node` 帧的节点名表追加多智能体节点，并说明 `update` 在多智能体下两个 agent 节点会额外携带 `answer`（原始回答文本，供折叠面板）：
```
| rag_agent_node   | {answer, sources, hits, used_rag}            |
| web_agent_node   | {answer, sources:[{type:"web",title,url,...}], hits, used_web} |
| integration_node | {iterations}                                 |
| dispatch_node    | {}                                           |
```

(e) `/api/health` 返回字段说明加 `web_search: bool`。

- [ ] **Step 2: 验收检查点**

人工通读 README 相关小节，确认无遗留 `TBD`、字段名与代码一致。

---

## Self-Review（计划作者自检，已执行）

- **Spec 覆盖**：spec 第 5 节全部组件 → Task 1–6；第 5.3 前端 → Task 7–9；第 6 数据流 → Task 6 SSE 测试 + Task 8 onNode；第 7 Prompt → Task 3 节点实现内嵌；第 8 降级矩阵 → Task 2/3/6 的兜底分支测试；第 9 测试 → 各任务 TDD；第 10 文档 → Task 10。无遗漏。
- **占位符扫描**：Task 1 Step 3 的 `<VER>` 是「安装后回读并替换」的显式指令（非悬空占位）；其余步骤均含可执行代码/命令。
- **类型一致性**：`build_multi_agent_graph(llm, rag, web, settings)`、`WebSearchService(settings, client=None).search()`、`MultiAgentNodes(llm, rag, web, settings)`、`route_after_dispatch`、`_select_graph`、`ChatRequest.mode`、`MULTI_AGENT_PIPELINE` 等命名在定义任务与消费任务间一致。`MultiAgentState` 统一为 `TypedDict(total=False)`（Task 3/4 一致，与 `GraphState` 同构）。
- **修正项**：规划中发现并已更新 spec 的两处——(1) `RagService.retrieve` 返回 dict 而非元组，改用 `build_context`；(2) 无 Tavily key 改为「始终建图 + 优雅降级」而非 503。spec 已同步修改。

---

## 执行交付

计划已保存至 `docs/superpowers/plans/2026-06-22-multi-agent.md`。两种执行方式：

**1. Subagent-Driven（推荐）** — 每个 Task 派发独立 subagent，任务间 review，迭代快。

**2. Inline Execution** — 在当前会话用 executing-plans 批量执行，带检查点。

请选择执行方式。
