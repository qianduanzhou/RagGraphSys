# FastAPI 框架详解：路由、模型、依赖、上传与流式（api.py）

> [06 篇](06-api-运行图同步流式.md) 讲了「图怎么被同步/流式调用」。这一篇换个角度，专门讲 **FastAPI 框架本身**：怎么定义接口、怎么校验数据、怎么接收上传文件、怎么做依赖、怎么做 SSE。看完你就能自己加一个新接口。

## 它扮演什么角色

FastAPI 是后端的「门面」：它把外面的 HTTP 请求（前端、curl）路由到对应的 Python 函数，再把结果变成 HTTP 响应。

```
前端 fetch ─► FastAPI ─► 找到匹配的路由函数 ─► 跑函数(校验/取服务/调逻辑)
                                                              │
            ◄────────── JSON / SSE 响应 ◄─────────────────────┘
```

本项目所有接口集中在 [api.py](../backend/api.py)，在 [main.py](../backend/main.py) 里用 `app.include_router(api_router, prefix="/api")` 挂到 `/api` 前缀下。比如 `@router.get("/health")` 实际地址是 `/api/health`。

---

## 一、APIRouter：把接口分组

```python
router = APIRouter()

@router.get("/health")
def health(request: Request) -> Dict[str, Any]:
    ...

@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    ...
```

- `@router.get(...)` / `@router.post(...)`：装饰器把函数注册成接口，路径就是参数。
- 函数参数 = **请求输入**，返回值 = **响应输出**。
- 返回普通 dict/对象，FastAPI 自动转成 JSON（`response_model` 还会做一次校验/文档）。

### 接口清单

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/api/health` | 健康检查 + 依赖状态 + 数量 |
| POST | `/api/chat` | 同步问答（跑图，一次性返回） |
| POST | `/api/chat/stream` | 流式问答（SSE，逐字返回） |
| POST | `/api/ingest` | 导入原始文本 |
| POST | `/api/ingest/file` | 导入单个文件 |
| POST | `/api/ingest/files` | 批量导入文件/文件夹/zip |
| GET | `/api/docs` | 已入库文档列表 |
| POST | `/api/docs/delete` | 删除单个文档 |
| POST | `/api/docs/delete/batch` | 批量删除 |
| GET | `/api/stats` | 数量统计 |

> FastAPI 会**自动**根据这些装饰器生成交互文档：启动后访问 `http://localhost:8000/docs`（Swagger UI）即可在线试接口。

---

## 二、Pydantic 模型：请求/响应的数据契约与校验

这是 FastAPI 最舒服的地方。每个接口的入参/出参都是一个 `BaseModel` 子类：

```python
class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    history: Optional[List[ChatMessage]] = Field(default_factory=list)
    mode: Literal["rag", "multi"] = "rag"

class ChatResponse(BaseModel):
    answer: str
    sources: List[Dict[str, Any]]
    used_rag: bool
    iterations: int
```

FastAPI 拿到请求后**自动**做这些事：

1. 把 JSON body 按 `ChatRequest` 解析。
2. 校验：`message` 必填且非空（`Field(..., min_length=1)`）；`mode` 只能是 rag/multi（`Literal`）。**不符合直接返回 422**，不用自己写校验。
3. 校验通过才把 `payload: ChatRequest` 传进函数。
4. 返回时再按 `ChatResponse` 序列化成 JSON。

> 嵌套也行：`ChatRequest.history` 是 `List[ChatMessage]`，FastAPI 会递归校验每一层。

### 一个技巧：用请求体而不是路径参数传文件名

```python
class DeleteDocRequest(BaseModel):
    source: str = Field(..., min_length=1)

@router.post("/docs/delete", response_model=DeleteDocResponse)
def delete_doc(payload: DeleteDocRequest, ...):
```

删除文档的 `source`（文件名）可能含 `.` / 空格 / 中文，放 URL 路径里（`/docs/delete/{source}`）会很麻烦。放进请求体 JSON 更稳。

---

## 三、依赖注入：从 Request 拿服务

前面 [09 篇](09-配置与启动-应用装配.md) 说服务都挂在 `app.state`。路由怎么拿到？通过函数参数里的 `request: Request`：

```python
def _state(request: Request):
    graph = getattr(request.app.state, "graph", None)
    rag = getattr(request.app.state, "rag", None)
    if graph is None or rag is None:
        raise HTTPException(status_code=503, detail="application not initialised")
    return graph, rag

@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    graph = _select_graph(request, payload.mode)
    ...
```

- `request.app.state.xxx` 取出 lifespan 里挂上的单例（graph、rag）。
- 没有（没初始化好）就 `raise HTTPException(503)`，FastAPI 自动把这个异常变成 503 响应。

> FastAPI 还有更正式的「依赖」写法（`Depends`），本项目用 `request.app.state` 这种轻量方式，原理一样：**把已经建好的对象交给请求，而不是在请求里新建**。

### HTTPException：控制错误状态码

```python
raise HTTPException(status_code=415, detail="unsupported file type")
```

这是 FastAPI 抛业务错误的标准方式，`status_code` 决定 HTTP 状态码，`detail` 进响应体。本项目用了 400（参数错）、415（类型不支持）、503（服务没准备好）、500（内部错）。

---

## 四、上传文件：UploadFile + File

```python
@router.post("/ingest/file", response_model=IngestResponse)
async def ingest_file(file: UploadFile = File(...), request: Request = None):
    name = file.filename or "upload"
    raw = await file.read()          # 读出字节（异步）
    text = parse_upload(name, raw)   # 交给文件解析层（见14篇）
    stats = rag.ingest_text(text, source=name)
    ...
```

- `file: UploadFile = File(...)`：声明这是一个文件上传参数。前端用 `FormData` 发（见 [16 篇](16-frontend-前后端对接.md)）。
- `await file.read()` 把文件内容读成 bytes，**要异步**（文件上传是慢操作）。
- 批量上传 `ingest_files(files: List[UploadFile] = File(default_factory=list))`：一次接多个文件。

---

## 五、SSE 流式响应：StreamingResponse

这是「逐字输出」的关键。FastAPI 普通 `return` 要等函数跑完才发；SSE 则是**边产生边发**：

```python
@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    async def event_stream():
        async for mode, data in graph.astream(initial, stream_mode=["updates", "custom"]):
            if mode == "updates":
                for node, update in data.items():
                    yield _sse({"type": "node", "node": node, "update": ...})
            elif mode == "custom":
                yield _sse(data)
        yield _sse({"type": "done"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
```

- `event_stream()` 是一个 **异步生成器**（`async def` + `yield`），每 `yield` 一帧就推给前端。
- `_sse(obj)` 把字典格式化成 SSE 帧：`data: {...json...}\n\n`（注意结尾两个换行，这是 SSE 协议规定）。
- `media_type="text/event-stream"`：告诉浏览器这是 SSE。
- `Cache-Control: no-cache` + `X-Accel-Buffering: no`：禁用缓存和 nginx 缓冲，保证实时推送（不加会被代理攒一段才发）。

> 双流（updates/custom）各自的含义见 [06 篇](06-api-运行图同步流式.md)。本篇只关注「FastAPI 怎么把生成器变成 SSE」。

---

## 六、健壮性：统一异常兜底

本项目每个接口都用 `try/except` 包住核心逻辑，把异常转成 HTTP 错误：

```python
try:
    result = graph.invoke({...})
except Exception as exc:
    logger.exception("graph invocation failed")
    raise HTTPException(status_code=500, detail=f"graph failed: {exc}") from exc
```

好处：后端崩了前端也能拿到一个清晰的 500 + 提示，而不是连接挂起或堆栈泄漏。`from exc` 保留原始异常链。

---

## 七、从模型到响应的完整流程（以 /chat 为例）

```
1. 前端 POST /api/chat  body={"message":"...", "mode":"rag"}
2. FastAPI 按 ChatRequest 解析 + 校验（不过则 422）
3. 调 chat(payload, request)
4. _select_graph(request, mode) 从 app.state 取已编译的图
5. graph.invoke({question, history, iterations}) 跑图（见05/06篇）
6. 拿到 {answer, sources, used_rag, iterations}
7. 包装成 ChatResponse（再校验一次）
8. 序列化成 JSON 返回 200
```

---

## 加一个新接口的步骤

假设要加「`GET /api/ping` 返回一个固定字符串」：

1. 在 [api.py](../backend/api.py) 加：
   ```python
   @router.get("/ping")
   def ping():
       return {"pong": True}
   ```
2. （需要服务就加 `request: Request` 参数，从 `app.state` 取）。
3. 重启后端，访问 `/api/ping` 或 `/docs` 测试。

复杂点的接口再补：`BaseModel` 定义入参出参、`Field` 加校验、`HTTPException` 处理错误、需要流式就 `StreamingResponse`。

---

## 学习检查

1. 一个 `BaseModel` 字段写成 `Field(..., min_length=1)` 和 `= "rag"`，分别表示什么？
2. 接口函数里 `request: Request` 这个参数是谁传进来的？它用来做什么？
3. 想返回 415 错误，应该用什么语句？
4. SSE 帧的格式长什么样？为什么结尾要两个换行？
5. `StreamingResponse` 接收的 `event_stream()` 是普通函数还是生成器？为什么它必须是生成器？
6. 删除文档的 `source` 为什么放在请求体而不是路径里？
7. 自动生成的接口文档在哪个地址？
