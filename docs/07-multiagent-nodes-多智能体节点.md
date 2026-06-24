# multiagent/nodes.py 学习笔记（多智能体节点）

> 配套源文件：`../backend/multiagent/nodes.py`
> 学习阶段：**第 7 步 · 多智能体节点**（先把前面 1~6 步的单路图吃透，再读这个）

## 文件作用

这是「多智能体模式」的节点定义，和根目录 `nodes.py` 是平行的两套。单路图是「检索→融合→生成」一条龙；多智能体图换了个思路：**派两个助手并行去查，再由一个整合助手把两份答案合成一份**。

三个角色（节点）：

- `rag_agent`（知识库助手）—— 用 RAG 检索本地知识库后回答；
- `web_agent`（联网助手）—— 用 Tavily 搜互联网后回答；
- `integration`（整合助手）—— 综合两份答案，给出最终回答（流式逐字输出）；
- 外加一个 `dispatch`（调度）—— 几乎不干活，只为日志和「起点」而设。

## 核心知识点

### 1. 又一个状态：MultiAgentState

```python
class MultiAgentState(TypedDict, total=False):
    question: str
    history: list
    rag_agent_answer: str          # RAG 助手的回答
    rag_agent_sources: list
    web_agent_answer: str          # 联网助手的回答
    web_sources: list
    used_rag: bool
    used_web: bool
    answer: str                    # 整合后的最终回答
    iterations: int
    streaming: bool
```

结构和单路图的 `GraphState` 同构（都 `total=False`、都有 question/answer/streaming），只是多了「两个 agent 各自的产物」。这种「每个 agent 写自己专属字段、最后整合到公共字段」的设计，是多智能体协作的典型写法。

### 2. rag_agent —— 检索 + 回答

```python
def rag_agent(self, state):
    retrieved = self.rag.build_context(question)   # 复用单路图的检索逻辑！
    context = retrieved.get("context", "")
    system = "你是知识库检索助手。仅根据知识库资料回答……"
    answer = self.llm.chat([...])
    return {"rag_agent_answer": answer, "rag_agent_sources": sources, "used_rag": used_rag}
```

注意它调的是 `self.rag.build_context()`——这是 `rag_service.py` 里和单路图 merge 节点**同一套**的检索逻辑（见 `04-nodes-LangGraph状态与节点.md`/`05-graph-LangGraph构图与编译.md`）。**两套图复用同一套检索，格式零重复**，这是项目的工程亮点。区别是：单路图把检索拆成 qdrant/neo4j/merge 三个节点，多智能体图把它打包进一个 agent 里。

### 3. web_agent —— 联网搜索 + 回答

```python
def web_agent(self, state):
    results = self.web.search(question)            # Tavily 搜索
    sources = [{"type":"web","title":...,"url":...,"content":...} for r in results]
    system = "你是联网搜索助手。根据搜索结果回答……用 [标题](url) 标注来源"
    answer = self.llm.chat([...])
    return {"web_agent_answer": answer, "web_sources": sources, "used_web": used_web}
```

结构和 rag_agent 几乎一样，只是数据源换成了 `WebSearchService`（Tavily）。来源类型标成 `"web"`，和 RAG 的 `"qdrant"`/`"neo4j"` 区分，前端能显示不同徽章。

> 小白要点：这两个 agent 写法高度对称——「检索/搜索 → 拿到资料 → 让 LLM 基于资料回答 → 返回答案+来源」。理解一个就理解了另一个。这正是「智能体（Agent）= 工具调用 + LLM 推理」的最朴素形态。

### 4. integration —— 整合两份答案（流式在这里发生）

```python
def integration(self, state):
    system = "你是整合助手。综合知识库回答与联网回答……"
    system += f"\n\n知识库回答：\n{rag_answer}"
    system += f"\n\n联网回答：\n{web_answer}"
    if state.get("streaming"):
        writer = get_stream_writer()              # 和单路图 llm_generate 一样的流式套路
        buffer = []
        for token in self.llm.chat_stream(messages):
            buffer.append(token)
            writer({"type": "delta", "text": token})
        answer = "".join(buffer)
    else:
        answer = self.llm.chat(messages)
    return {"answer": answer, "iterations": iterations + 1}
```

整合助手把两个 agent 的答案塞进 system 提示，让它综合出最终答案。流式部分和 `04-nodes-LangGraph状态与节点.md` 的 `llm_generate` **一模一样**的 writer 双轨写法。这再次说明：学透单路图，多智能体图几乎免费就懂了。

### 5. 优雅降级

两个 agent 都包了 `try/except`：Tavily 没配 key 或失败、知识库连不上，都不会让整张图崩——失败的那个返回空，整合助手照样能用另一个的结果回答。这和单路图 qdrant/neo4j 节点的容错思路一致。

## 重要对象、函数或配置项

| 名称 | 作用 |
|------|------|
| `MultiAgentState` | 多智能体图的状态 |
| `MultiAgentNodes` | 持有依赖（llm/rag/web/settings） |
| `rag_agent` / `web_agent` | 两个并行助手（检索/搜索 + 回答） |
| `integration` | 整合两份答案，流式输出 |
| `dispatch` | 调度起点（记日志） |
| `route_after_dispatch` | 返回 `["rag_agent_node","web_agent_node"]` 触发并行 |
| `self.rag.build_context()` | 复用单路图的检索逻辑 |
| `self.web.search()` | Tavily 联网搜索 |

## 学习检查

1. 多智能体图和单路图，在「检索」这一步有什么设计差异？为什么说它们复用了同一套逻辑？
2. `rag_agent` 和 `web_agent` 为什么写得这么对称？这种对称性给你什么启发？
3. `integration` 节点流式输出的写法，和单路图哪个节点几乎一样？
4. 如果 Tavily 没配 key，多智能体图会崩溃吗？为什么？
5. `MultiAgentState` 为什么也要 `total=False`？

> 节点看懂后，最后看 `08-multiagent-graph-多智能体构图.md`，看这两个 agent 是怎么「并行扇出 → 汇合整合」的。
