# LangChain / LangGraph 用法清单

> 本文档把本项目用到的 **每一个** LangChain / LangGraph API 列出来，标注它的作用、出现在哪个源文件、对应学习第几步。配合 [学习路线](学习路线.md) 一起看。
> 想看某个 API「为什么这么用」，点进对应笔记的源码对照即可。

## 1. 版本说明

版本取自 [../backend/requirements.txt](../backend/requirements.txt)：

| 包 | 版本 | 作用 |
|----|------|------|
| `langchain` | 1.3.9 | LangChain 主包（本项目主要用它的消息/工具类型） |
| `langchain-core` | 1.4.7 | 核心抽象（`BaseChatModel`/`Embeddings`/消息对象/`Document`） |
| `langchain-openai` | 1.3.2 | OpenAI 兼容实现（`ChatOpenAI`/`OpenAIEmbeddings`） |
| `langchain-text-splitters` | 1.1.2 | 文本切分器（`RecursiveCharacterTextSplitter`） |
| `langgraph` | 1.2.5 | 图编排引擎（`StateGraph`/节点/边/流式） |

> 小白要点：`langchain`、`langchain-core`、`langchain-openai` 是三个不同的包。`core` 定义抽象接口，`openai` 提供具体实现，主包 `langchain` 做高层集成。你 `pip install langchain langchain-openai` 时它们会被一起装上。

---

## 2. LangChain 组件（对话模型 / 向量化 / 文本切分）

### 2.1 对话模型：ChatOpenAI 与消息对象

| API | 作用 | 源文件 | 第几步 |
|-----|------|--------|--------|
| `from langchain_openai import ChatOpenAI` | 导入 OpenAI 兼容的对话模型类 | services/llm_service.py | 1 |
| `ChatOpenAI(model, api_key, base_url, temperature, max_tokens, timeout)` | 初始化对话模型，指向任意 OpenAI 兼容端点 | services/llm_service.py | 1 |
| `from langchain_core.language_models import BaseChatModel` | 抽象基类，用于类型注解（方便测试注入假模型） | services/llm_service.py | 1 |
| `from langchain_core.messages import SystemMessage` | 系统提示消息（设定助手人设/规则） | services/llm_service.py | 1 |
| `from langchain_core.messages import HumanMessage` | 用户消息 | services/llm_service.py | 1 |
| `from langchain_core.messages import AIMessage` | 助手消息（历史对话里的模型回复） | services/llm_service.py | 1 |
| `_to_lc_messages(messages)` | 自写工具函数：把 `{role, content}` 字典列表转成 LangChain 消息对象 | services/llm_service.py | 1 |
| `.bind(temperature=..., max_tokens=...)` | 返回一个「临时改了参数」的模型副本，不改原对象 | services/llm_service.py | 1 |
| `.invoke(消息列表)` | 非流式调用，返回一个完整响应对象 | services/llm_service.py | 1 |
| `.stream(消息列表)` | 流式调用，逐块 yield 响应 chunk | services/llm_service.py | 1 |
| `resp.content` | 从响应对象取文本内容（非流式） | services/llm_service.py | 1 |
| `chunk.content` | 从流式 chunk 取文本内容（流式） | services/llm_service.py | 1 |

> 核心范式（一句话）：`{role, content} 字典 → _to_lc_messages → [SystemMessage, HumanMessage, ...] → model.invoke/stream → resp.content / chunk.content`。

### 2.2 向量化：OpenAIEmbeddings

| API | 作用 | 源文件 | 第几步 |
|-----|------|--------|--------|
| `from langchain_openai import OpenAIEmbeddings` | 导入 OpenAI 兼容的向量模型类 | services/embedding_service.py | 2 |
| `OpenAIEmbeddings(model, api_key, base_url)` | 初始化向量模型（与 ChatOpenAI 同源） | services/embedding_service.py | 2 |
| `from langchain_core.embeddings import Embeddings` | 抽象基类，用于类型注解（注入假实现） | services/embedding_service.py | 2 |
| `.embed_query(text)` | 把**单条**文本变成向量（用于查询/检索） | services/embedding_service.py | 2 |
| `.embed_documents(texts)` | 把**多条**文本批量变成向量（用于入库） | services/embedding_service.py | 2 |

> 对比记忆：检索时用 `embed_query`（一条），入库时用 `embed_documents`（一批）。本项目把 `embed` 封装成 `embed_query`、`embed_batch` 封装成 `embed_documents`。

### 2.3 文本切分：RecursiveCharacterTextSplitter

| API | 作用 | 源文件 | 第几步 |
|-----|------|--------|--------|
| `from langchain_text_splitters import RecursiveCharacterTextSplitter` | 导入递归字符切分器 | core/utils.py | 3 |
| `RecursiveCharacterTextSplitter(chunk_size, chunk_overlap, separators)` | 创建切分器：块大小、重叠、分隔符优先级表 | core/utils.py | 3 |
| `separators=["\n\n", "\n", "。", ...]` | 分隔符按优先级从长到短尝试，中文项目用「段落→行→句号」 | core/utils.py | 3 |
| `.create_documents([text])` | 把文本切成一组 `Document` 对象 | core/utils.py | 3 |
| `doc.page_content` | 取出切分后每个块的纯文本 | core/utils.py | 3 |

> 小白要点：`chunk_size`（每块最多多少字符）+ `chunk_overlap`（相邻块重叠多少字符，保证上下文不断）。本项目默认 500 / 80。

---

## 3. LangGraph 引擎（状态机 / 节点 / 边 / 流式）

### 3.1 构图 API

| API | 作用 | 源文件 | 第几步 |
|-----|------|--------|--------|
| `from langgraph.graph import StateGraph` | 导入状态图构建器 | graph.py、multiagent/graph.py | 5、8 |
| `from langgraph.graph import START` | 图的虚拟起点节点 | graph.py、multiagent/graph.py | 5、8 |
| `from langgraph.graph import END` | 图的虚拟终点节点 | graph.py、nodes.py、multiagent/graph.py | 4、5、8 |
| `StateGraph(GraphState)` | 以状态类为模板创建一张空图 | graph.py、multiagent/graph.py | 5、8 |
| `.add_node("节点名", 节点函数)` | 注册一个节点（函数接收 state，返回 dict 增量） | graph.py、multiagent/graph.py | 5、8 |
| `.add_edge(A, B)` | 加一条确定边：A 完成后一定走 B | graph.py、multiagent/graph.py | 5、8 |
| `.add_edge(START, "router_node")` | 起点 → 第一个节点 | graph.py、multiagent/graph.py | 5、8 |
| `.add_edge("integration_node", END)` | 最后一个节点 → 终点 | graph.py、multiagent/graph.py | 5、8 |
| `.add_conditional_edges("节点名", 路由函数)` | 加条件边：由路由函数的返回值决定下一步走哪 | graph.py、multiagent/graph.py | 5、8 |
| `.compile()` | 把图编译成可运行对象（`CompiledStateGraph`） | graph.py、multiagent/graph.py | 5、8 |
| `from langgraph.graph.state import CompiledStateGraph` | 编译后图的类型，用于函数返回值注解 | graph.py、multiagent/graph.py | 5、8 |

### 3.2 运行 API

| API | 作用 | 源文件 | 第几步 |
|-----|------|--------|--------|
| `compiled.invoke(初始状态字典)` | 同步运行整张图，返回最终状态 | graph.py（run_graph）、api.py（/chat） | 5、6 |
| `compiled.astream(初始状态, stream_mode=[...])` | 异步流式运行，逐事件 yield | api.py（/chat/stream） | 6 |
| `stream_mode=["updates", "custom"]` | 同时消费两种流：updates=节点完成进度，custom=节点内自定义增量 | api.py | 6 |

### 3.3 流式写入 API

| API | 作用 | 源文件 | 第几步 |
|-----|------|--------|--------|
| `from langgraph.config import get_stream_writer` | 在节点函数里拿到「写流器」 | nodes.py、multiagent/nodes.py | 4、7 |
| `writer({"type": "delta", "text": token})` | 往 custom 流里写一帧，被 `astream(stream_mode="custom")` 消费 | nodes.py、multiagent/nodes.py | 4、7 |

### 3.4 节点与路由的「约定」（不是函数，是写法）

| 约定 | 说明 | 源文件 | 第几步 |
|------|------|--------|--------|
| `class GraphState(TypedDict, total=False)` | 用 `TypedDict` 定义状态，`total=False` 允许部分字段缺失 | nodes.py | 4 |
| 节点函数签名 `def router(self, state) -> dict` | 接收当前 state，返回一个 **dict（只含要更新的字段）**，LangGraph 自动合并 | nodes.py、multiagent/nodes.py | 4、7 |
| 路由函数返回 `["a", "b"]`（列表） | = **并行扇出**，同时走向多个节点 | nodes.py、multiagent/nodes.py | 5、8 |
| 路由函数返回 `"node"`（字符串） | = 走单个下一节点 | nodes.py、multiagent/nodes.py | 4、5 |
| 路由函数返回 `END` | = 结束这张图 | nodes.py | 4、5 |
| 多条边指向同一节点 = 汇合等待 | LangGraph 会等所有上游节点都完成才触发它（fan-in） | graph.py、multiagent/graph.py | 5、8 |

---

## 4. 检索链路 → API 对照

把整个 RAG 流水线的每个环节，对应到用到的 API 和学习第几步：

| 环节 | 做什么 | 用到的 LangChain/LangGraph API | 对应步骤 |
|------|--------|-------------------------------|----------|
| **路由** | 判断是否需要检索 | 节点函数 `router`、`add_edge(START, "router_node")` | 4、5 |
| **扇出** | 同时走向量检索 + 图谱检索 | 路由函数返回 `["qdrant_node","neo4j_node"]`（列表） | 5 |
| **向量检索** | 用问题向量查 Qdrant | `OpenAIEmbeddings.embed_query` | 2、4 |
| **图谱检索** | 抽关键词查 Neo4j | `ChatOpenAI.extract_keywords`（用 `.invoke` + `SystemMessage`） | 1、4 |
| **汇合融合** | 把两路结果拼成 context | `add_edge` 两条边都指向 merge_node（fan-in） | 5 |
| **生成** | LLM 基于资料回答 | `ChatOpenAI.invoke` / `.stream`、`SystemMessage`/`HumanMessage` | 1、4 |
| **流式** | 逐字输出回答 | `get_stream_writer()` + `writer({"type":"delta",...})`、`astream(stream_mode=["updates","custom"])` | 4、6 |
| **反思** | 审核答案是否合格 | 节点 `reflection`、`add_conditional_edges`（pass→END / fail→回 llm_node） | 4、5 |
| **整合（多智能体）** | 综合两份答案 | 节点 `integration`、fan-out + fan-in | 7、8 |

---

## 5. 一句话区分 LangChain 与 LangGraph

- **LangChain** 提供组件：对话模型（`ChatOpenAI`）、向量模型（`OpenAIEmbeddings`）、文本切分器（`RecursiveCharacterTextSplitter`）、消息对象（`SystemMessage`/`HumanMessage`/`AIMessage`）。它回答「怎么调大模型、怎么切文本、怎么转向量」。
- **LangGraph** 提供编排：用 `StateGraph` 把一堆节点函数连成一张带状态的图，支持并行、循环、条件分支、流式。它回答「怎么把这些组件按顺序/并行地组织起来，让数据在节点间流动」。

> 本项目的分工：第 1~3 步全是 LangChain（单组件），第 4 步起 LangGraph 登场，把前面的组件串进图里跑。