# api.py 学习笔记（图的同步与流式调用）

> 配套源文件：`../backend/api.py`
> 学习阶段：**第 6 步 · 运行编译好的图（同步 + 流式）**（前面 5 步的总结，也是 SSE 流式的核心）

## 文件作用

前面 `graph.py` 编译好了图。这个文件是 FastAPI 的接口层，负责**接收 HTTP 请求、调用图、把结果返回给前端**。它定义了 `/api/chat`（同步）、`/api/chat/stream`（流式）等接口。

对学 LangGraph 来说，重点是两个调用方式：同步的 `.invoke()` 和异步流式的 `.astream()`。流式那段是全项目最精彩、也最值得反复读的代码。

## 核心知识点

### 1. 同步调用 .invoke() —— 一次性跑完整张图

```python
@router.post("/chat")
def chat(payload, request):
    graph = _select_graph(request, payload.mode)   # 选单路图或多智能体图
    result = graph.invoke({
        "question": payload.message,
        "history": _history_to_dicts(payload.history),
        "iterations": 0,
    })
    return ChatResponse(
        answer=result.get("answer", ""),
        sources=result.get("sources", []),
        used_rag=result.get("used_rag", False),
        iterations=result.get("iterations", 0),
    )
```

`.invoke(初始状态)`：传一个初始状态字典进去，图从 START 跑到 END，**全部跑完**后返回最终状态。`/api/chat` 走的是非流式路径，会经过反思循环（反思逻辑见 `04-nodes-LangGraph状态与节点.md`）。

调用图的套路就两步：①构造初始状态 → ②`.invoke()` 拿最终状态。

### 2. 异步流式 .astream(stream_mode=[...]) —— 同时听两种事件流（核心！）

这是全项目最难也最妙的一段，慢慢读：

```python
@router.post("/chat/stream")
async def chat_stream(payload, request):
    graph = _select_graph(request, payload.mode)
    initial = {"question": ..., "history": ..., "iterations": 0, "streaming": True}

    async def event_stream():
        async for mode, data in graph.astream(initial, stream_mode=["updates", "custom"]):
            if mode == "updates":
                for node, update in data.items():
                    yield _sse({"type": "node", "node": node, "update": _summarize_update(node, update)})
            elif mode == "custom":
                yield _sse(data)
        yield _sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={...})
```

逐层拆解：

**(a) 异步函数 + async for**：`async def` + `async for` 是 Python 的异步语法。`.astream()` 是个「异步迭代器」，每产生一个事件就交还一次控制权。Web 框架正好需要异步，这样流式时不会卡住整个服务器。

**(b) stream_mode 同时订阅两种流**——这是关键：

- `"updates"`：**节点级事件**。每有一个节点跑完，就发一条，里面带节点名 + 它更新的字段。前端用它点亮「路由→向量→图谱→复合→生成」的进度条。
- `"custom"`：**节点内自定义事件**。还记得 `04-nodes-LangGraph状态与节点.md` 里 `llm_generate` 用 `get_stream_writer()` 写的 `{"type":"delta","text":...}` 吗？那些就通过 `"custom"` 流出来，正是逐字 token。

所以 `async for mode, data` 每次拿到的 `mode` 告诉你「这是哪种事件」，`data` 是事件内容。两种事件通过**同一个循环交错输出**，不需要手搓线程或队列桥接——这是 LangGraph 流式的优雅之处。

**(c) yield + StreamingResponse**：`event_stream()` 是个异步生成器，每 `yield` 一条 SSE 帧。FastAPI 的 `StreamingResponse` 把它转成持续的 HTTP 响应流。前端用浏览器原生 `fetch` 读这个流，边读边显示。

### 3. SSE 帧格式 _sse()

```python
def _sse(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"
```

SSE（Server-Sent Events）是一种服务器单向推送协议。每条消息格式是 `data: JSON内容\n\n`（两个换行结尾）。`ensure_ascii=False` 保证中文不被转义成 `\uXXXX`。

三种帧类型：
- `{"type":"node",...}` —— 进度（来自 updates 流）
- `{"type":"delta","text":"你"}` —— 逐字（来自 custom 流）
- `{"type":"done"}` / `{"type":"error",...}` —— 结束/出错

### 4. _summarize_update —— 把节点更新"瘦身"

节点返回的 update 可能很大（比如完整的检索结果），全发给前端太重。`_summarize_update` 按节点类型只挑关键字段：

```python
def _summarize_update(node, update):
    if node == "qdrant_node":  return {"hits": len(update.get("qdrant_results") or [])}
    if node == "merge_node":   return {"sources": ..., "used_rag": ...}
    ...
```

这是个体贴的设计：前端只需知道「命中几条」「有哪些来源」，不需要完整内容。多智能体模式下还会额外带上 `answer`，供前端折叠展示每个 agent 的原始回答。

### 5. 流式路径跳过反思

```python
# 在 nodes.py
def route_after_llm(state):
    if state.get("streaming"):
        return END          # 流式直接结束
    return "reflection_node"
```

注意：流式请求会设 `"streaming": True`，于是图走到 llm 后直接 END，**跳过反思**。为什么？因为反思要重新生成，会和逐字输出打架、还增加延迟。流式优先「快」，非流式优先「稳」。这是同一张图支持两种行为的巧妙设计。

### 6. 双图切换 _select_graph

```python
def _select_graph(request, mode):
    if mode == "multi":
        return request.app.state.multi_agent_graph   # 多智能体图
    return request.app.state.graph                    # 单路 RAG 图
```

请求体带 `mode` 字段：`"rag"`（默认）走单路图，`"multi"` 走多智能体图。两套图共用同一套调用代码（invoke / astream），只是图不同。

## 关键流程

流式请求的端到端数据流：

```
浏览器 POST /api/chat/stream {mode:"rag"}
   |
   v
api.chat_stream:  graph.astream(initial, stream_mode=["updates","custom"])
   |
   ├──updates流──→ 节点完成 ──→ _summarize_update ──→ {"type":"node",...} ──┐
   |                                                                        |
   └──custom 流──→ llm节点 writer 推 token ──→ {"type":"delta",...} ─────────┤
   |                                                                        v
   └─结束──→ {"type":"done"}                                    StreamingResponse → 浏览器(SSE)
```

对照 `04-nodes-LangGraph状态与节点.md`：节点内的 `writer(...)` = 这里的 custom 流；节点完成 = 这里的 updates 流。两个文件在这里「对上了」。

## 重要对象、函数或配置项

| 名称 | 作用 |
|------|------|
| `graph.invoke(初始状态)` | 同步跑完整张图，返回最终状态 |
| `graph.astream(初始, stream_mode=[...])` | 异步流式，按事件模式产出 |
| `stream_mode="updates"` | 节点完成事件 |
| `stream_mode="custom"` | 节点内 writer 写的事件 |
| `StreamingResponse` | FastAPI 流式响应 |
| `_sse()` | 把对象格式化成 SSE 帧 |
| `_summarize_update()` | 给节点更新瘦身 |
| `_select_graph()` | 按 mode 选单路/多智能体图 |

## 运行方式或使用方式

启动后端（`python ../backend/main.py`）后，接口文档见 http://localhost:8000/docs 。流式接口可用 `curl` 试：

```bash
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"你好\",\"mode\":\"rag\"}"
```

`-N` 关闭缓冲，能看到逐帧到达。测试见 `../backend/tests/test_api.py`，用 FastAPI `TestClient` 真打 SSE 端点。

## 修改建议

- 加新节点事件：节点内用 `get_stream_writer()` 写，这里在 `custom` 分支自然就收到（保持 `{"type":...}` 格式约定即可）。
- 改进度展示：调 `_summarize_update`，让它吐前端需要的字段，别把大块原始数据直接发出去。
- 流式接口务必保留响应头 `X-Accel-Buffering: no`、`Cache-Control: no-cache`，否则 Nginx 会缓冲、逐字变成一次性吐出。
- 加新 `mode`：在 `_select_graph` 增分支，并在 `../backend/main.py` 把新图挂到 `app.state`。

## 学习检查

1. `.invoke()` 和 `.astream()` 的本质区别是什么？各适合什么场景？
2. `stream_mode=["updates","custom"]` 同时订阅两种流，分别对应什么？前端各用来做什么？
3. 节点里 `writer({"type":"delta",...})` 写出的东西，是怎么跑到浏览器的？追踪整条链路。
4. 为什么要 `_summarize_update` 给数据瘦身？不瘦身会怎样？
5. 流式路径为什么跳过反思？如果流式也走反思，会有什么麻烦？
6. `async def event_stream` 里的 `yield` 和 `04-nodes-LangGraph状态与节点.md` 里 `chat_stream` 的 `yield`，各起什么作用？

> 至此单路 RAG 图从定义到调用已经完整。最后一步看 `07-multiagent-nodes-多智能体节点.md（同级见 08-multiagent-graph-多智能体构图.md）`，它是同样的套路，但多了「并行多智能体」这张图。
