# langchain / langgraph 升级到最新版 + 用法排查与重构

- 日期：2026-06-17
- 范围：`backend/`（Python），P1 + P2 + P3 全做
- 状态：设计已批准

## 1. 背景与现状（实测）

`requirements.txt` 仍 pin 在 langchain 0.3.x / langgraph 0.2.x 旧版，但 `backend/.venv` 实际已是各包 PyPI 最新版（2026-06-12 发布）。两者严重脱节，且 `langchain-openai` 未被显式声明（仅靠传递依赖存在）。

实测结论（探测脚本已运行后删除）：

| 包 | requirements.txt | venv 实际 | PyPI 最新 |
|---|---|---|---|
| langchain | 0.3.4 | 1.3.9 | 1.3.9 |
| langchain-core | 0.3.12 | 1.4.7 | 1.4.7 |
| langchain-openai | **缺失** | 1.3.2 | 1.3.2 |
| langchain-text-splitters | 0.3.0 | 1.1.2 | 1.1.2 |
| langgraph | 0.2.39 | 1.2.5 | 1.2.5 |

- 全部代码导入正常；**87 个测试全过**；无 langchain/langgraph 弃用警告。
- 因此真正问题不在 venv，而在依赖声明脱节 + 若干脆弱用法。

## 2. 排查出的具体问题

- **P1（严重）**：`requirements.txt` 版本脱节，且漏列 `langchain-openai`。全新 `pip install -r requirements.txt` 会装回旧版导致项目崩。
- **P2（中等）**：`services/embedding_service.py` 在构造时 `os.environ["OPENAI_API_KEY"]/["OPENAI_BASE_URL"] = ...`，把智谱 key 写入整个进程环境变量（污染、顺序依赖、脆弱）。实测确认 langchain-openai 1.x 下直接传 `api_key`/`base_url` 可免污染构造。
- **P3（现代化）**：流式输出把 callable `sink` 和 `streaming` 标志塞进 `GraphState`，再在 `api.py` 用 `loop.call_soon_threadsafe` 手搓线程桥。langgraph 1.x 提供原生 `get_stream_writer()` + `stream_mode="custom"`，可干净地移除 sink 污染。

## 3. 设计

### 3.1 P1 — requirements.txt

AI/RAG 段改为：

```
langchain==1.3.9
langchain-core==1.4.7
langchain-openai==1.3.2
langchain-text-splitters==1.1.2
langgraph==1.2.5
```

### 3.2 P2 — embedding_service.py

放弃 `langchain.embeddings.init_embeddings("openai:...")` 间接层（拉入庞大 `langchain` 顶层包），改用与 `ChatOpenAI` 同源的 `OpenAIEmbeddings`，直接传参：

```python
from langchain_openai import OpenAIEmbeddings

self.embeddings = OpenAIEmbeddings(
    model=settings.zhipu_embedding_model,
    api_key=settings.zhipu_api_key,
    base_url=settings.zhipu_base_url,
)
```

删除 `import os` 及两行 `os.environ[...]`。注入式构造函数（测试用）保持不变。

### 3.3 P3 — 流式迁移到原生 StreamWriter

实测验证（关键事实）：

- `from langgraph.config import get_stream_writer`、`from langgraph.types import StreamWriter` 可用。
- sync 节点内 `writer({...})` 经 `astream(stream_mode=["updates","custom"])` 消费，yield 元组 `(mode, payload)`；**custom 的 delta 先于 updates 帧到达**（符合期望）。
- 普通 `graph.invoke()`（非流式）路径下，节点内 `get_stream_writer()` 返回 no-op 且不报错。
- `get_stream_writer()` 在「无可运行上下文」时会抛 `RuntimeError`。

设计决策：`get_stream_writer` 在 `nodes.py` 中**模块级导入**（非函数内惰性导入），这样单测可通过 `monkeypatch.setattr("nodes.get_stream_writer", ...)` 替换 writer，保留对 `llm_generate` 流式分支的隔离单测。

#### nodes.py

- 模块级 `from langgraph.config import get_stream_writer`（便于单测 patch）。
- `GraphState` 删除 `sink: Any`，保留 `streaming: bool`（仍控制节点行为与 `route_after_llm` 路由）。
- `llm_generate`：`streaming` 为真时 `writer = get_stream_writer()`，对每个 token 调 `writer({"type":"delta","text":token})`（替代 `sink(token)`），累积完整 answer；非流式仍走 `self.llm.chat(messages)`。

#### api.py — `/chat/stream`

删除 `run_graph` / `event_stream` 的双协程 + `asyncio.Queue` + `loop.call_soon_threadsafe` 手搓线程桥。改为直接 async 消费多模式流：

```python
async def event_stream():
    initial = {"question":..., "history":..., "iterations":0, "streaming":True}  # 不再有 sink
    try:
        async for mode, payload in graph.astream(initial, stream_mode=["updates","custom"]):
            if mode == "updates":
                for node, update in payload.items():
                    yield _sse({"type":"node","node":node,"update":_summarize_update(node,update)})
            elif mode == "custom":
                yield _sse(payload)  # {"type":"delta","text":...} 原样转发
        yield _sse({"type":"done"})
    except Exception as exc:
        logger.exception("stream graph failed: %s", exc)
        yield _sse({"type":"error","message":f"graph failed: {exc}"})
```

**SSE 线上格式零变化**：`data:{...}\n\n`、frame 类型 `node/delta/done/error`、delta 先于 llm_node-complete —— **前端无需改动**。

## 4. 测试影响

P3 实际影响 3 个测试文件（精读测试后确认）：

- `tests/test_nodes.py::test_llm_node_stream_uses_sink`：当前用 `sink: captured.append` 隔离测流式节点。改为 `monkeypatch.setattr("nodes.get_stream_writer", ...)` 注入伪 writer，断言 `captured == ["答","案"]` 不变（行为契约相同，仅投递方式从 sink 换成 writer）。函数名改为 `test_llm_node_stream_emits_via_writer`。非流式用例 `test_llm_node_non_stream` 零改动。
- `tests/test_graph.py::test_streaming_pipeline_events_and_live_tokens`：当前用 sink + `asyncio.Queue` + `stream_mode="updates"` 驱动真实图。改为 `stream_mode=["updates","custom"]` 消费 `(mode, payload)` 元组，custom 帧记录 delta；initial 不再含 `sink`。拓扑/时序断言保留（router 首、llm 尾、merge 在两个检索之后、`deltas==["答","案"]`、`merge_i < first_delta < llm_i`）。`done` 哨兵是 api.py 层概念，从该图级测试中移除。`test_non_stream_runs_full_pipeline_with_reflection` 零改动。
- `tests/test_api.py::test_chat_stream_sse_frames` 的 `_MockStreamGraph`：改为 `astream(initial, stream_mode=["updates","custom"])` yield 元组 `(mode, payload)`，分别 yield updates/custom。**行为断言不变**（router/merge/llm 的 node 帧、"你""好" delta、done 收尾、merge 暴露 sources）。
- `test_embedding_service`、`test_llm_service` 预期零改动继续通过（注入式构造未变）。

## 5. 验收标准

- 全量 `pytest` 通过（流式 mock 同步更新后），数量不少于现状 87。
- `requirements.txt` 与 venv 实际一致，且显式包含 `langchain-openai`。
- `embedding_service.py` 不再写 `os.environ`。
- `GraphState` 不再含 callable `sink`。

## 6. 非目标

- 不改 SSE 线上协议、不改前端、不引入新依赖。
- 不重构与 langchain/langgraph 无关的代码。
