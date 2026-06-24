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

