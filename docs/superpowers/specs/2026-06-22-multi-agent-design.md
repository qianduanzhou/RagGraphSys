# 多智能体问答（RAG + 联网 + 整合）设计

- **日期**: 2026-06-22
- **状态**: 已确认，待实现
- **范围**: backend (Python / FastAPI / LangGraph) + frontend (React / TypeScript)
- **搜索方式**: Tavily API
- **接入方式**: 新增可切换的「多智能体」模式，保留现有 RAG 问答为默认，向后兼容

---

## 1. 背景与动机

当前问答走单张固定拓扑的 LangGraph（`router → [qdrant, neo4j] → merge → llm →
(reflection | END)`），答案仅来自已上传的知识库（向量 + 图谱）。这覆盖了「文档内
问答」，但无法回答库外、时效性、通用外部信息类问题。

用户希望增加**多智能体联调**能力：问答期间由三个智能体协作——

1. **RAG 智能体**：检索已入库内容（向量 + 图谱），生成回答；
2. **联网智能体**：连接网络搜索（Tavily），生成回答；
3. **整合智能体**：综合前两者的回答，输出最终内容给用户。

项目当前**完全没有任何** agent / 联网搜索 / web search 的代码或依赖，本设计从零
搭建该能力，且不破坏现有 RAG 问答。

## 2. 目标与非目标

**目标**

- 新增一张独立的「多智能体」LangGraph：RAG 智能体与联网智能体**并行**产出各自回
  答，整合智能体综合后流式输出最终答案。
- 前端加模式切换（`RAG问答` / `多智能体`），默认仍为 RAG 问答，向后兼容。
- 联网搜索通过 **Tavily** 实现，封装在独立服务层，与 LLM 解耦（可移植）。
- 复用现有 `RagService` 检索与 `LLMService` 调用，零重复逻辑。
- 全链路优雅降级：Tavily 失败 / 无 key / RAG 无命中 / 两边皆空均有兜底。
- 两个子智能体的原始回答以**默认折叠**的面板呈现，整合答案进主气泡流式显示。

**非目标**

- 不改现有 RAG 图（`build_graph`）、不改进库逻辑、不改向量/图谱存储。
- 不引入 LangGraph checkpointer / 记忆持久化（会话仍靠传入 history）。
- 不做 ReAct 式工具循环调用（本设计是固定拓扑的并行-汇合图，非工具型 agent）。
- 不改反思循环（多智能体路径不触发反思，与现有流式路径一致直接 END）。

## 3. 现状摘要

| 文件 / 模块 | 现状 |
|---|---|
| `backend/graph.py` | `build_graph(llm, rag, settings)` 编译 RAG 图；`route_after_router` 返回 `["qdrant_node","neo4j_node"]` 实现并行扇出。 |
| `backend/nodes.py` | `GraphState` TypedDict + `GraphNodes`（router/qdrant/neo4j/merge/llm_generate/reflection）。`llm_generate` 用 `get_stream_writer()` 发 `delta`。 |
| `backend/services/llm_service.py` | 唯一 LLM 边界。`chat()` / `chat_stream()` / `extract_keywords()` / `extract_graph()` / `reflect()`。 |
| `backend/rag/rag_service.py` | `RagService.retrieve(question)` 一次完成向量+图谱混合检索并 `merge_results()`，返回 `(context, sources)`。 |
| `backend/api.py` | `/chat`（非流式）、`/chat/stream`（SSE：`node` 帧推进度、`delta` 帧出 token）。`_summarize_update()` 按节点名投影状态。`ChatRequest` 无 mode 字段。 |
| `backend/core/config.py` | `Settings`（pydantic-settings），无 Tavily 相关配置。 |
| `backend/main.py` | `lifespan` 构造各服务 + `build_graph`，挂 `app.state`。 |
| `frontend/src/types.ts` | 固定 `PIPELINE`（5 步）；`SourceRef.type` 仅 `"qdrant" \| "neo4j"`。 |
| `frontend/src/api/client.ts` | `chat` / `chatStream` 不携带 mode；SSE 帧按 `meta/node/delta/done/error` 分发。 |
| 依赖 | `requirements.txt` 无 `tavily-*`、`langchain-community` 等搜索/agent 包。 |

## 4. 架构方案：独立编译图 + 按模式分发

采用**方案 A（已确认）**：新建一张独立的多智能体编译图，与现有 RAG 图并存，API 层
按请求 `mode` 选择执行哪张图。理由：每张图拓扑简单、可独立测试、向后兼容零风险，与
项目既有「独立模块、互不侵入」风格一致。

### 4.1 多智能体图拓扑

```
START → dispatch_node ──(条件边返回 ["rag_agent_node","web_agent_node"])──┐
                                                                        │ 并行扇出
   rag_agent_node  ──┐                                                   │
                     ├─→ integration_node → END      ◄───────────────────┘
   web_agent_node  ──┘
```

- `dispatch_node`：轻量透传节点（记日志「启动多智能体联调」），用条件边返回两个
  agent 名实现并行扇出——写法与现有 `route_after_router` 返回两个检索节点完全一致。
- `integration_node` 是 join 汇合点：两个 agent 均边到它，LangGraph 自动当 barrier，
  两者都完成才执行。
- 流式走 `astream(stream_mode=["updates","custom"])` + `get_stream_writer()`，与现有
  RAG 流式路径同构；整合智能体 token 经 `delta` 帧输出，两个 agent 完成时各发一个
  `node` 帧报进度与来源。

### 4.2 并行执行说明

两个 agent 节点均为同步阻塞 I/O（LLM + Tavily）。在 `astream`（异步入口）下，
LangGraph 把同一 superstep 的并行节点交给线程池并发执行；即便退化为顺序，功能与汇
合语义不变。前端管线步进器按并行呈现（两步同时 active）。

## 5. 组件与文件改动

### 5.1 后端新增

- **`backend/services/web_search_service.py`**（新建）：唯一封装 Tavily 的边界，与
  `llm_service.py` / `embedding_service.py` 同属「模型边界」风格。
  - `class WebSearchService`：构造时读 `settings.tavily_api_key`、`tavily_max_results`。
  - 属性 `available: bool`（key 非空即 True）。
  - `search(query: str, max_results: int | None = None) -> List[Dict]`：返回归一化结果
    `[{title, url, content, score}]`；失败 / 不可用时捕获异常、记日志、返回 `[]`。
  - 内部用 `tavily-python` 的 `TavilyClient`（`search(query, max_results=..., ...)`
    取 `results`）。

- **`backend/multiagent/__init__.py`**（新建）：包初始化，导出 `build_multi_agent_graph`。

- **`backend/multiagent/nodes.py`**（新建）：`MultiAgentState`（TypedDict）+
  `MultiAgentNodes`。
  - `MultiAgentState` 字段：`question`, `history`, `rag_agent_answer`,
    `rag_agent_sources: List[Dict]`, `web_agent_answer`, `web_sources: List[Dict]`,
    `answer`（最终整合答案）, `used_rag`, `used_web`, `iterations`, `streaming`。
  - `MultiAgentNodes.__init__(llm, rag, web, settings)`。
  - `dispatch(state)`：透传，记日志。
  - `rag_agent(state)`：`retrieved = self.rag.build_context(question)`（复用现有混合检索：
    qdrant+neo4j+merge+阈值，零重复；返回 `{"context","sources","used_rag"}`）→ 用 RAG
    智能体 prompt 调 `llm.chat()` → 返回 `{rag_agent_answer, rag_agent_sources, used_rag}`。
    try/except 兜底（异常时 `rag_agent_answer="（知识库检索失败）"`、空来源）。
  - `web_agent(state)`：`results = self.web.search(question)` → 用联网智能体 prompt 调
    `llm.chat()` → 返回 `{web_agent_answer, web_sources, used_web}`。不可用 / 异常时
    `web_agent_answer="（联网搜索不可用）"`、空来源。
  - `integration(state)`：取两份答案+来源 → `llm.chat_stream()`（流式时经
    `get_stream_writer()` 发 `delta`，否则 `llm.chat()`）→ 返回 `{answer, iterations+1}`。
    LLM 调用失败兜底为错误文案（沿用现 `llm_node` 风格）。

- **`backend/multiagent/graph.py`**（新建）：`build_multi_agent_graph(llm, rag, web,
  settings) -> CompiledStateGraph`。注册四节点、连边、条件边扇出，编译返回。拓扑见 4.1。

### 5.2 后端修改

- **`backend/core/config.py`**：新增
  ```python
  tavily_api_key: str = ""
  tavily_max_results: int = 5
  ```

- **`backend/main.py`** `lifespan`：构造 `WebSearchService(settings)`；**始终**构建
    `build_multi_agent_graph(llm, rag, web, settings)`（web_agent 内部对无 key 优雅降级，
    无需因缺 key 而不建图）；挂到 `app.state.web`、`app.state.multi_agent_graph`。

- **`backend/api.py`**：
  - `ChatRequest` 增 `mode: Literal["rag", "multi"] = "rag"`。
  - `_select_graph(request, mode)`：按 mode 取 `graph`（`"rag"`）或
    `multi_agent_graph`（`"multi"`）；`multi_agent_graph` 属性缺失时返回 503 兜底
    （正常不会触发，因图始终构建）。无 Tavily key **不**报错——由 `web_agent` 内部降级。
  - `/chat`、`/chat/stream` 用 `payload.mode` 选图，其余不变。
  - `/health` 返回新增 `web_search: bool`（`web.available`）。
  - `_summarize_update()` 增 `dispatch_node` / `rag_agent_node` / `web_agent_node` /
    `integration_node` 的投影。两个 agent 节点的投影**必须携带原始回答文本**，供前端
    折叠面板渲染（整合答案才进 `delta` 流式，子答案非流式、经 `node` 帧完整下发）：
    - `rag_agent_node` → `{answer: rag_agent_answer, sources: rag_agent_sources, hits: len, used_rag}`
    - `web_agent_node` → `{answer: web_agent_answer, sources: web_sources, hits: len, used_web}`
    - `integration_node` → `{iterations}`
    - `dispatch_node` → `{}`（最小）

- **`backend/.env.example`**：新增 `TAVILY_API_KEY=` 占位与注释。
- **`backend/requirements.txt`**：新增 `tavily-python`（版本锁待实现时按当前稳定版填）。

### 5.3 前端修改

- **`frontend/src/types.ts`**：
  - `type ChatMode = "rag" | "multi"`。
  - 新增 `MULTI_AGENT_PIPELINE`（4 步：`dispatch_node` 调度 / `rag_agent_node` RAG智能体
    / `web_agent_node` 联网智能体 / `integration_node` 整合）。
  - `SourceRef.type` 扩展为 `"qdrant" | "neo4j" | "web"`，`web` 来源带 `title?`、`url?`。
  - `NodeUpdate` 增 `used_web?: boolean`、`answer?: string`（两 agent 节点的原始回答文本）；
    `web_sources` 复用 `sources`。
  - `ChatMessage` 增 `mode?: ChatMode`、`ragAgentAnswer?: string`、`webAgentAnswer?: string`。
  - `HealthResponse` 增 `web_search: boolean`。

- **`frontend/src/api/client.ts`**：`chat(message, history, mode)`、`chatStream(message,
  history, mode, cb)` 在请求体带 `mode`；`HealthResponse` 解析 `web_search`。

- **`frontend/src/App.tsx`**：持有 `mode: ChatMode`（默认 `"rag"`）；`handleSend` 按模式
  选 `MULTI_AGENT_PIPELINE` / `PIPELINE`，发送带 `mode`；`onNode` 命中
  `rag_agent_node`/`web_agent_node` 时把对应子答案/来源写入 message（供折叠面板用）。

- **`frontend/src/components/`**：
  - 模式切换控件（分段按钮 `RAG问答 | 多智能体`）置于聊天区顶部；`web_search=false`
    时「多智能体」置灰 + tooltip「未配置 TAVILY_API_KEY」。
  - `MessageBubble` 按消息 `mode` 渲染对应管线步进器；web 来源渲染为可点击链接徽章；
    多智能体消息下方加两个**默认折叠**面板「查看 RAG 原始回答」「查看联网原始回答」
    （点开展示对应子答案 + 其来源）。

## 6. 数据流（多智能体流式）

请求 `POST /api/chat/stream {message, history, mode:"multi"}`：

1. `_state` 选 `multi_agent_graph` → `astream(initial, stream_mode=["updates","custom"])`。
2. `dispatch_node` 完成 → `node` 帧（最小投影）。
3. `rag_agent_node` 与 `web_agent_node` 并行；各自完成 → 两个 `node` 帧，分别带
   `{answer, sources, hits, used_rag}`（RAG 原始回答 + 来源）与 `{answer, sources:
   [{title,url,...}], hits, used_web}`（联网原始回答 + 来源）。前端据此更新步进器状态，
   并把两份原始回答写入对应折叠面板。
4. `integration_node` 经 `llm.chat_stream()` 输出 → 逐 token `delta` 帧（即最终答案，
   进主气泡流式追加）。
5. `done`。

主气泡只显示**整合后的最终答案**。两个子 agent 原始回答进默认折叠面板；来源以徽章呈
现（RAG 文档徽章 + 网页链接徽章）。

## 7. Prompt 与整合逻辑

- **RAG 智能体**（`rag_agent`）：复用 `rag.build_context(question)`（内部 qdrant+neo4j+
  merge+阈值）拿 `{context, sources, used_rag}`，再 `llm.chat()`。System prompt 要点：
  > 你是知识库检索助手。仅根据下方「知识库资料」回答问题。若资料无关或不足以回答，
  > 明确回复「知识库中无相关内容」，不要编造。

- **联网智能体**（`web_agent`）：`web.search(question)` 拿结果，再 `llm.chat()`。System
  prompt 要点：
  > 你是联网搜索助手。根据下方「搜索结果」回答问题；结果来自网络未必准确，若无关回复
  > 「联网未找到相关结果」。用 `[标题](url)` 标注来源。

- **整合智能体**（`integration`）：取两份答案 + 两份来源，`llm.chat_stream()` 综合。
  System prompt 要点：
  > 你是整合助手，综合下方「知识库回答」与「联网回答」给出最终答案：涉及用户上传文档
  > 的内容以知识库回答为准，最新/外部/通用信息以联网回答为准；文档内容用 Markdown 引用
  > 块（每行 `> `）、网页用 `[标题](url)` 链接标注来源；若某一方明确表示无相关内容，则
  > 以另一方为主；不要重复赘述。回答简洁、准确、有条理。

## 8. 错误处理与优雅降级

| 情形 | 处理 |
|---|---|
| `tavily_api_key` 为空 | `web.available=false`；`/health` 报 `web_search:false`；前端禁用「多智能体」入口并提示。 |
| 运行时 Tavily 异常（网络/限流） | `web_agent` 捕获 → `web_agent_answer="（联网搜索失败）"`、`web_sources=[]`，整合照常。 |
| RAG 无命中 | `rag_agent_answer="（知识库中无相关内容）"`，整合偏向联网。 |
| 两边皆空 | 整合智能体回复「知识库与联网均未能找到相关内容」友好提示。 |
| 整合 LLM 调用失败 | 沿用现 `llm_node` 兜底：返回错误文案，不让请求崩。 |
| `mode="multi"` 但无 Tavily key | 图仍构建并执行；`web_agent` 降级为「（联网搜索不可用）」，整合偏向 RAG；前端禁用入口提示。仅当 `multi_agent_graph` 属性本身缺失（不应发生）才 503 兜底。 |

## 9. 测试

- **`tests/test_web_search_service.py`**：mock `TavilyClient.search` —— 正常结果（归一化
  字段）/ 空结果 / 抛异常 → 返回 `[]` / `available` 标志。
- **`tests/test_multiagent_nodes.py`**：mock `rag`+`llm`+`web` —— `rag_agent`（有 context
  / 无 context）、`web_agent`（有结果 / 不可用 / 异常）、`integration`（流式拼接、两边
  都空、仅一边有内容）。
- **`tests/test_multiagent_graph.py`**：全 mock 串跑 `build_multi_agent_graph`，断言
  `dispatch` 扇出到两 agent、`integration` 产出来自两方的综合答案、`answer` 非空。
- **`tests/test_api.py`** 扩展：`mode:"multi"` 路由到 `multi_agent_graph`；`mode:"rag"`
  仍走原 `graph`；`/chat/stream` 的 SSE `node` 帧含新节点名；`/health` 含 `web_search`；
  `mode:"multi"` 但无 key → 503。
- 现有 RAG 全部测试须仍通过（无回归）。

## 10. 配置与文档

- `.env.example` 增 `TAVILY_API_KEY=` 及说明。
- `requirements.txt` 增 `tavily-python`。
- `README.md` 增补：多智能体模式说明、Tavily 配置、新 SSE 帧字段、模式开关用法。

## 11. 风险与备注

- **Tavily 依赖外部 key 与网络**：免费额度有限，需在 README 注明；离线 / 无 key 时自动
  降级，不影响 RAG 问答。
- **GLM 可移植性**：联网搜索经独立 `WebSearchService` 实现，不绑定具体 LLM，换任何
  OpenAI 兼容模型均可。
- **并行执行**：依赖 LangGraph 在 `astream` 下对同步节点的线程池调度；即使退化为顺序，
  汇合与最终答案语义不变。
- **token 成本**：多智能体一次问答触发 3 次 LLM 调用（两 agent 各一次 + 整合一次，外加
  RAG 检索内的关键词抽取），成本约为现有 RAG 问答的 ~2–3 倍，由用户按需切换承担。
