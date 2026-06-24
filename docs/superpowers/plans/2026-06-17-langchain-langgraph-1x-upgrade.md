# langchain / langgraph 升级到最新版 + 用法重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `requirements.txt` 与 venv 实际安装的 langchain 1.x / langgraph 1.x 最新版一致，并消除当前用法中的脆弱模式（embedding 全局环境变量污染、流式输出的手搓线程桥）。

**Architecture:** 三处独立改动。(P1) 修正 `requirements.txt` 版本声明并补齐缺失的 `langchain-openai`；(P2) `embedding_service.py` 改用与 `ChatOpenAI` 同源的 `OpenAIEmbeddings` 直接传参，去掉 `os.environ` 写入；(P3) 流式输出迁移到 langgraph 1.x 原生 `get_stream_writer()` + `astream(stream_mode=["updates","custom"])`，删除 `GraphState.sink` callable 与 api 层的 `asyncio.Queue`/`call_soon_threadsafe` 线程桥。SSE 线上协议零变化。

**Tech Stack:** Python 3.11、FastAPI、langchain 1.3.9、langchain-core 1.4.7、langchain-openai 1.3.2、langchain-text-splitters 1.1.2、langgraph 1.2.5、pytest。

## Global Constraints

- 目标版本（必须与 venv 一致）：`langchain==1.3.9`、`langchain-core==1.4.7`、`langchain-openai==1.3.2`、`langchain-text-splitters==1.1.2`、`langgraph==1.2.5`。
- venv 解释器固定为 `backend/.venv/Scripts/python.exe`，所有 pytest / 探测脚本都用它执行。
- SSE 线上格式禁止变化：`data: {json}\n\n`、frame 类型 `node`/`delta`/`done`/`error`、delta 必须先于 `llm_node` 的 node-complete 帧。
- `get_stream_writer` 在 `nodes.py` 必须**模块级导入**（便于单测 `monkeypatch.setattr("nodes.get_stream_writer", ...)`）。
- 不改前端、不引入新依赖、不重构与 langchain/langgraph 无关的代码。
- **项目非 git 仓库**（已确认 `fatal: not a git repository`），故各任务无 `git commit` 步骤，改以「跑相关测试通过」为验收检查点。若后续初始化 git，可用 `qnhl-git-commit` skill 按模块分批提交。

---

## File Structure

| 文件 | 责任 | 本次改动 |
|---|---|---|
| `backend/requirements.txt` | 依赖版本声明 | P1：更新 AI/RAG 段版本，补 `langchain-openai` |
| `backend/services/embedding_service.py` | 唯一与智谱 embedding 交互处 | P2：改 `OpenAIEmbeddings` 直传参，删 `os.environ` 写入 |
| `backend/nodes.py` | GraphState + 节点 + 路由 | P3a：模块级导入 `get_stream_writer`，`llm_generate` 用 writer，删 `GraphState.sink` |
| `backend/api.py` | HTTP/SSE 层 | P3b：`/chat/stream` 改多模式消费，删 queue/线程桥，删多余 `import asyncio` |
| `backend/tests/test_nodes.py` | 节点单测 | P3a：`test_llm_node_stream_uses_sink` → 改为 patch writer |
| `backend/tests/test_graph.py` | 真实图 e2e | P3a：流式测试改 `stream_mode=["updates","custom"]` 元组消费 |
| `backend/tests/test_api.py` | SSE 端点测试 | P3b：`_MockStreamGraph` mock 改新契约 |

---

## Task 1: P1 — 修正 requirements.txt 版本脱节

**Files:**
- Modify: `backend/requirements.txt:14-18`

**Interfaces:**
- Consumes: 无
- Produces: 与 venv 一致的依赖声明；后续全新 `pip install -r requirements.txt` 会得到 1.x 最新版

- [ ] **Step 1: 更新 AI/RAG 段**

把 `backend/requirements.txt` 第 14-18 行（`# ===== AI / RAG =====` 段）替换为：

```
# ===== AI / RAG =====
langchain==1.3.9
langchain-core==1.4.7
langchain-openai==1.3.2
langchain-text-splitters==1.1.2
langgraph==1.2.5
```

注意：原文件没有 `langchain-openai` 行，必须新增（`llm_service.py` 直接 `from langchain_openai import ChatOpenAI` 依赖它）。

- [ ] **Step 2: 校验 requirements 与 venv 一致且无需改动**

Run（PowerShell）:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pip install -r "D:\project\customer\AI\RagGraphSys\backend\requirements.txt" --dry-run
```
Expected: 输出 `Requirement already satisfied` 行，且**不出现**任何 `Would install` / `Would download`（说明 venv 已满足声明，无版本冲突）。

- [ ] **Step 3: 任务检查点**

无测试涉及本文件。Step 2 输出无安装动作即视为本任务完成。

---

## Task 2: P2 — embedding_service 去除全局环境变量污染

**Files:**
- Modify: `backend/services/embedding_service.py`
- Test（不改，仅运行）: `backend/tests/test_embedding_service.py`

**Interfaces:**
- Consumes: `core.config.Settings`（`zhipu_api_key`、`zhipu_base_url`、`zhipu_embedding_model`）
- Produces: `EmbeddingService.embeddings` 仍为 `langchain_core.embeddings.Embeddings` 兼容对象（`embed_query`/`embed_documents` 接口不变），下游 `qdrant_store.py` 零改动

- [ ] **Step 1: 替换导入**

把 `backend/services/embedding_service.py` 顶部的：

```python
import os
from typing import List, Optional

from langchain_core.embeddings import Embeddings
from langchain.embeddings import init_embeddings
```

替换为：

```python
from typing import List, Optional

from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings
```

（删除 `import os` 和 `from langchain.embeddings import init_embeddings`。）

- [ ] **Step 2: 替换构造逻辑**

把 `EmbeddingService.__init__` 中（注入分支之后、else 之前）的实际构造块：

```python
        # init_embeddings 的 openai provider 通过环境变量读取
        # OPENAI_API_KEY / OPENAI_BASE_URL，因此先指向智谱的 OpenAI 兼容端点。
        os.environ["OPENAI_API_KEY"] = settings.zhipu_api_key
        os.environ["OPENAI_BASE_URL"] = settings.zhipu_base_url
        self.embeddings = init_embeddings(f"openai:{settings.zhipu_embedding_model}")
        logger.info(
            "Embedding 初始化完成：model=%s，base_url=%s",
            settings.zhipu_embedding_model,
            settings.zhipu_base_url,
        )
```

替换为：

```python
        # 直接以参数形式把智谱的 OpenAI 兼容端点传给 OpenAIEmbeddings
        # （与 ChatOpenAI 同源），避免向进程级 os.environ 写入凭据。
        self.embeddings = OpenAIEmbeddings(
            model=settings.zhipu_embedding_model,
            api_key=settings.zhipu_api_key,
            base_url=settings.zhipu_base_url,
        )
        logger.info(
            "Embedding 初始化完成：model=%s，base_url=%s",
            settings.zhipu_embedding_model,
            settings.zhipu_base_url,
        )
```

注入分支（`if embeddings is not None: ... return`）保持不变。

- [ ] **Step 3: 同步更新模块文档字符串**

`backend/services/embedding_service.py` 顶部 docstring 第 3 行原为：

```
通过 LangChain 的 :func:`langchain.embeddings.init_embeddings`（``openai`` provider）
初始化，指向智谱的 OpenAI 兼容接口。业务 / 检索代码只调用本服务的
``embed`` / ``embed_batch``，不直接接触 LangChain 对象。
```

替换为：

```
通过 LangChain 的 :class:`langchain_openai.OpenAIEmbeddings` 初始化，
直接以参数指向智谱的 OpenAI 兼容接口（与 ``ChatOpenAI`` 同源）。
业务 / 检索代码只调用本服务的 ``embed`` / ``embed_batch``，不直接接触 LangChain 对象。
```

并把类 docstring 中 ``init_embeddings`` 的字样改为 ``OpenAIEmbeddings``：
将 `"""基于 LangChain ``init_embeddings`` 的 embedding 高层封装。"""` 改为 `"""基于 LangChain ``OpenAIEmbeddings`` 的 embedding 高层封装。"""`。

- [ ] **Step 4: 跑测试确认未破坏行为**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest "D:\project\customer\AI\RagGraphSys\backend\tests\test_embedding_service.py" -q
```
Expected: `5 passed`（注入式构造未变，测试零改动应全过）。

- [ ] **Step 5: 确认无 os.environ 写入（静态校验）**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -c "import ast; t=ast.parse(open(r'D:\project\customer\AI\RagGraphSys\backend\services\embedding_service.py',encoding='utf-8').read()); src=open(r'D:\project\customer\AI\RagGraphSys\backend\services\embedding_service.py',encoding='utf-8').read(); print('os.environ refs:', src.count('os.environ')); print('init_embeddings refs:', src.count('init_embeddings'))"
```
Expected:
```
os.environ refs: 0
init_embeddings refs: 0
```

---

## Task 3: P3a — 节点流式迁移到原生 StreamWriter

**Files:**
- Modify: `backend/nodes.py`
- Modify: `backend/tests/test_nodes.py`
- Modify: `backend/tests/test_graph.py`

**Interfaces:**
- Consumes: `services.llm_service.LLMService.chat_stream`（生成 token 迭代器）、`langgraph.config.get_stream_writer`
- Produces: `nodes.GraphState`（移除 `sink`，保留 `streaming`）；`llm_generate` 在流式时通过 writer 发 `{"type":"delta","text":token}` custom 事件；`graph.py`（build_graph）零改动

- [ ] **Step 1: nodes.py — 模块级导入 get_stream_writer**

在 `backend/nodes.py` 现有 `from langgraph.graph import END`（约第 17 行）下方新增一行，使其变为：

```python
from langgraph.graph import END
from langgraph.config import get_stream_writer
```

- [ ] **Step 2: nodes.py — 从 GraphState 删除 sink**

把 `GraphState` 末尾两行：

```python
    iterations: int
    streaming: bool
    sink: Any
```

改为：

```python
    iterations: int
    streaming: bool
```

（删除 `sink: Any`。`Any` 仍被 `qdrant_results` 等字段使用，`from typing import Any` 保留。）

- [ ] **Step 3: nodes.py — llm_generate 流式分支改用 writer**

把 `llm_generate` 内当前的流式块：

```python
        sink = state.get("sink") if state.get("streaming") else None
        try:
            if sink:
                # 按字符流式生成；逐个转发到 SSE sink，同时累积完整回答供下游状态使用。
                buffer: List[str] = []
                for token in self.llm.chat_stream(messages):
                    buffer.append(token)
                    sink(token)
                answer = "".join(buffer)
            else:
                answer = self.llm.chat(messages)
        except Exception as exc:  # noqa: BLE001
            logger.exception("llm_node generation failed: %s", exc)
            answer = f"抱歉，生成回答时出错：{exc}"
```

替换为：

```python
        try:
            if state.get("streaming"):
                # 按字符流式生成；通过 langgraph 的 StreamWriter 把每个 token 作为
                # custom 事件转发（由 astream(stream_mode="custom") 消费），
                # 同时累积完整回答供下游状态使用。
                writer = get_stream_writer()
                buffer: List[str] = []
                for token in self.llm.chat_stream(messages):
                    buffer.append(token)
                    writer({"type": "delta", "text": token})
                answer = "".join(buffer)
            else:
                answer = self.llm.chat(messages)
        except Exception as exc:  # noqa: BLE001
            logger.exception("llm_node generation failed: %s", exc)
            answer = f"抱歉，生成回答时出错：{exc}"
```

- [ ] **Step 4: 先更新 test_nodes 单测为「patch writer」契约（此时跑会失败）**

把 `backend/tests/test_nodes.py` 中的：

```python
def test_llm_node_stream_uses_sink(settings):
    captured = []
    out = make_nodes(settings, stream_tokens=["答", "案"]).llm_generate({
        "question": "q", "history": [], "context": "ctx", "iterations": 0,
        "streaming": True, "sink": captured.append,
    })
    assert out["answer"] == "答案"
    assert captured == ["答", "案"]
```

替换为：

```python
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
```

Run（应失败，因为 Step 3 已改实现但本步先确认测试本身可执行；若 Step 3 已应用则应直接通过）:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest "D:\project\customer\AI\RagGraphSys\backend\tests\test_nodes.py::test_llm_node_stream_emits_via_writer" -q
```
Expected: `1 passed`（Step 3 已实现）。若报 `AttributeError: <module 'nodes'> has no attribute 'get_stream_writer'`，回查 Step 1 是否漏了模块级导入。

- [ ] **Step 5: 跑全部 test_nodes 确认未破坏其它用例**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest "D:\project\customer\AI\RagGraphSys\backend\tests\test_nodes.py" -q
```
Expected: 全部通过（原 12 个用例，其中 1 个改名，数量不变）。

- [ ] **Step 6: 更新 test_graph 流式测试为新契约**

把 `backend/tests/test_graph.py` 中的 `test_streaming_pipeline_events_and_live_tokens`（第 8-68 行整个函数）替换为：

```python
def test_streaming_pipeline_events_and_live_tokens(settings):
    """真实 graph.astream(stream_mode=["updates","custom"])：节点事件按序到达，
    token 作为 custom 事件在 merge 与 llm 之间实时产生。"""
    llm = MockLLM(stream_tokens=["答", "案"])
    rag = MockRag(
        MockQdrant(hits=[{"text": "v", "score": 0.9, "source": "d"}]),
        MockNeo4j(rels=[{"head": "X", "rel": "R", "tail": "Y"}]),
    )
    graph = build_graph(llm, rag, settings)

    async def drive():
        initial = {"question": "q", "history": [], "iterations": 0, "streaming": True}
        events = []
        async for mode, payload in graph.astream(initial, stream_mode=["updates", "custom"]):
            if mode == "updates":
                for node in payload:
                    events.append(("node", node))
            elif mode == "custom":
                # 节点写入的负载，形如 {"type": "delta", "text": "答"}
                events.append(("delta", payload.get("text")))
        return events

    events = asyncio.run(drive())
    nodes = [e[1] for e in events if e[0] == "node"]
    deltas = [e[1] for e in events if e[0] == "delta"]

    # qdrant/neo4j 并行执行 -> 相对顺序不确定，因此断言拓扑结构而非精确序列
    assert nodes[0] == "router_node"
    assert nodes[-1] == "llm_node"
    assert {"qdrant_node", "neo4j_node", "merge_node"}.issubset(nodes)
    assert nodes.index("merge_node") > nodes.index("qdrant_node")
    assert nodes.index("merge_node") > nodes.index("neo4j_node")
    assert nodes.index("llm_node") > nodes.index("merge_node")
    assert deltas == ["答", "案"], deltas
    assert "reflection_node" not in nodes  # 流式路径跳过反思

    find = lambda p: next(i for i, e in enumerate(events) if p(e))
    merge_i = find(lambda e: e == ("node", "merge_node"))
    llm_i = find(lambda e: e == ("node", "llm_node"))
    first_delta = find(lambda e: e[0] == "delta")
    # token 是实时的：严格位于 merge 完成与 llm 完成之间
    assert merge_i < first_delta < llm_i
```

（移除了原版手搓的 `asyncio.Queue` / `call_soon_threadsafe` / `sink` / 自注入 `done` 哨兵；`done` 是 api 层概念，不属于图级测试。`test_non_stream_runs_full_pipeline_with_reflection` 不动。）

- [ ] **Step 7: 跑 test_graph 确认真实图流式契约成立**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest "D:\project\customer\AI\RagGraphSys\backend\tests\test_graph.py" -q
```
Expected: `2 passed`。

- [ ] **Step 8: 任务检查点 — 跑 nodes + graph 合并**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest "D:\project\customer\AI\RagGraphSys\backend\tests\test_nodes.py" "D:\project\customer\AI\RagGraphSys\backend\tests\test_graph.py" -q
```
Expected: 全部通过。此时 api.py 仍引用旧 sink，但 `api.py` 的旧 `chat_stream` 把 `sink` 放进 initial state——`GraphState` 已删 `sink` 字段，但 TypedDict `total=False` 不校验多余键，且旧 api 还未跑（下个任务才改）。全量 pytest 会留到 Task 4 之后。

---

## Task 4: P3b — api 层 SSE 改多模式消费

**Files:**
- Modify: `backend/api.py`
- Modify: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `compiled_graph.astream(initial, stream_mode=["updates","custom"])` 产出的 `(mode, payload)` 元组
- Produces: `/api/chat/stream` 的 SSE 行为契约不变（`node`/`delta`/`done`/`error` 帧）

- [ ] **Step 1: api.py — 重写 chat_stream，删除线程桥**

把 `backend/api.py` 中整个 `chat_stream` 函数（从 `@router.post("/chat/stream")` 到该函数末尾的 `return StreamingResponse(...)` 块）替换为：

```python
@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    """SSE 流：`node` 帧（updates 模式的流水线进度）与 `delta`（custom 模式的字符增量）交替输出。"""
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        raise HTTPException(status_code=503, detail="application not initialised")

    initial = {
        "question": payload.message,
        "history": _history_to_dicts(payload.history),
        "iterations": 0,
        "streaming": True,
    }

    async def event_stream():
        try:
            async for mode, data in graph.astream(initial, stream_mode=["updates", "custom"]):
                if mode == "updates":
                    for node, update in data.items():
                        yield _sse(
                            {"type": "node", "node": node, "update": _summarize_update(node, update)}
                        )
                elif mode == "custom":
                    # 节点通过 StreamWriter 写入的负载，形如 {"type": "delta", "text": ...}
                    yield _sse(data)
            yield _sse({"type": "done"})
        except Exception as exc:  # noqa: BLE001
            logger.exception("stream graph failed: %s", exc)
            yield _sse({"type": "error", "message": f"graph failed: {exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 禁用 nginx/代理缓冲
            "Connection": "keep-alive",
        },
    )
```

（删除了 `loop = asyncio.get_running_loop()`、`queue`、`run_graph`、`sink`、`call_soon_threadsafe`、后台 task 等。`_sse` / `_summarize_update` 保持不变。）

- [ ] **Step 2: api.py — 删除不再使用的 import asyncio**

把 `backend/api.py` 顶部：

```python
import asyncio
import json
```

改为：

```python
import json
```

（`asyncio` 此前仅用于被删除的线程桥。确认：grep `api.py` 应无其它 `asyncio.` 引用。）

- [ ] **Step 3: 更新 test_api 的 _MockStreamGraph 为新契约**

把 `backend/tests/test_api.py` 中 `test_chat_stream_sse_frames` 内的 `_MockStreamGraph`（约第 81-93 行）替换为：

```python
    class _MockStreamGraph:
        async def astream(self, initial, stream_mode=("updates",)):
            # 模拟真实图的多模式流：(mode, payload) 元组
            yield ("updates", {"router_node": {"needs_rag": True, "used_rag": True}})
            yield ("updates", {"merge_node": {"sources": [{"type": "qdrant", "content": "c", "score": 0.9, "source": "d"}], "used_rag": True, "context": "x"}})
            yield ("custom", {"type": "delta", "text": "你"})
            yield ("custom", {"type": "delta", "text": "好"})
            yield ("updates", {"llm_node": {"answer": "你好", "iterations": 1}})
```

`test_chat_stream_sse_frames` 的**断言部分（第 100-116 行）保持不变**——它们断言的是 SSE 行为契约，迁移后仍然成立。

- [ ] **Step 4: 删除 test_api 中不再使用的 import asyncio**

`backend/tests/test_api.py` 顶部：

```python
import asyncio
import json
```

改为：

```python
import json
```

（`asyncio.sleep(0)` 已随 mock 改写移除。）

- [ ] **Step 5: 跑 test_api 确认 SSE 契约不变**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest "D:\project\customer\AI\RagGraphSys\backend\tests\test_api.py" -q
```
Expected: 全部通过（含 `test_chat_stream_sse_frames`）。

---

## Task 5: 全量回归 + 清理

**Files:**
- 无新改动；仅运行校验

- [ ] **Step 1: 全量 pytest**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest "D:\project\customer\AI\RagGraphSys\backend" -q
```
Expected: `87 passed`（与重构前同数量；3 个流式相关测试为改写/改名，无增删）。

- [ ] **Step 2: 全量 pytest 打开弃用警告，确认无 langchain/langgraph 告警**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest "D:\project\customer\AI\RagGraphSys\backend" -q -W "default::DeprecationWarning"
```
Expected: `87 passed`；warnings summary 中**不应**出现 `langchain` / `langgraph` 相关的 DeprecationWarning（neo4j 的 session-close 告警可忽略，与本次无关）。

- [ ] **Step 3: 确认无残留 sink 引用**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -c "import pathlib; src=pathlib.Path(r'D:\project\customer\AI\RagGraphSys\backend').rglob('*.py'); hits=[(str(p),p.read_text(encoding='utf-8').count('sink')) for p in src if 'sink' in p.read_text(encoding='utf-8') and '.venv' not in str(p) and 'tests' not in str(p)]; print(hits)"
```
Expected: `[]`（`backend/` 源码——排除 .venv 与 tests——中不应再有任何 `sink` 字样）。

- [ ] **Step 4: 确认 import 干净（应用可导入）**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, r'D:\project\customer\AI\RagGraphSys\backend'); import main; print('main imported OK')"
```
Expected: `main imported OK`（无 ImportError；说明 embedding_service / nodes / api / graph 链路导入正常）。

- [ ] **Step 5: 任务检查点**

Step 1-4 全绿即视为本次重构完成。验收对照设计文档第 5 节「验收标准」逐条满足：requirements 一致、os.environ 已消除、GraphState 无 sink、全量测试通过。

---

## Self-Review

**1. Spec coverage:** P1 → Task 1；P2 → Task 2；P3 nodes → Task 3；P3 api → Task 4；回归 → Task 5。spec 第 5 节四条验收标准分别在 Task 1/Step2、Task 2/Step5、Task 3/Step2、Task 5/Step1 覆盖。无遗漏。

**2. Placeholder scan:** 全部 step 含完整代码或精确命令；无 TBD/TODO/"适当处理"。

**3. Type consistency:** `get_stream_writer()` 返回的 writer 在 nodes.py（`writer({"type":"delta","text":token})`）、test_nodes（fake 返回 `lambda payload: ...`）、test_graph（`payload.get("text")`）、api.py（`yield _sse(data)`）、test_api mock（`("custom", {"type":"delta","text":...})`）中 payload 结构一致 `{"type":"delta","text":...}`。`astream(stream_mode=["updates","custom"])` 在 Task 3（真实图）、Task 4（api）、test_graph、test_api mock 中均产出 `(mode, payload)` 元组，`mode` 取值 `"updates"`/`"custom"` 一致。
