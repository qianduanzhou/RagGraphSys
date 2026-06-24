# multiagent/graph.py 学习笔记（多智能体图构建）

> 配套源文件：`../backend/multiagent/graph.py`
> 学习阶段：**第 8 步 · 多智能体构图（并行 fan-out + 汇合）**（学习路径的终点）

## 文件作用

这个文件把上一份 `nodes.py` 的四个节点连成一张「并行-汇合」图，编译好。它和根目录的 `graph.py` 用的是**完全相同的 LangGraph API**（StateGraph / add_node / add_edge / add_conditional_edges / compile），区别只在拓扑形状。

如果你已经读过 `05-graph-LangGraph构图与编译.md`，这个文件几乎是复习——重点体会「并行扇出」和「汇合等待」是怎么用同一套 API 表达的。

## 核心知识点

### 1. 整张图的拓扑

```
START
  |
  v
dispatch_node
  |
  conditional (route_after_dispatch 返回 ["rag_agent_node","web_agent_node"])
  |
  fan-out 并行
  ┌──────────┴──────────┐
rag_agent_node        web_agent_node    ← 两个助手同时跑
  |                        |
  └──────────┬─────────────┘
             fan-in 汇合（两条边都指向 integration，LangGraph 等两者都完成）
             |
             v
        integration_node    ← 整合两份答案，流式输出
             |
             v
            END
```

和单路图的对比：

- 单路图：router 并行扇出到 qdrant/neo4j，汇合到 merge，再生成；
- 多智能体图：dispatch 并行扇出到 rag_agent/web_agent，汇合到 integration 整合。

**模式完全一样**，只是把「检索节点」升级成了「带回答能力的智能体」。

### 2. 关键 API（和单路图一致）

```python
from langgraph.graph import END, START, StateGraph

nodes = MultiAgentNodes(llm=llm, rag=rag, web=web, settings=settings)
graph = StateGraph(MultiAgentState)

graph.add_node("dispatch_node", nodes.dispatch)
graph.add_node("rag_agent_node", nodes.rag_agent)
graph.add_node("web_agent_node", nodes.web_agent)
graph.add_node("integration_node", nodes.integration)

graph.add_edge(START, "dispatch_node")
graph.add_conditional_edges("dispatch_node", route_after_dispatch)  # fan-out
graph.add_edge("rag_agent_node", "integration_node")   # 汇合边 1
graph.add_edge("web_agent_node", "integration_node")   # 汇合边 2
graph.add_edge("integration_node", END)

compiled = graph.compile()
```

### 3. fan-out 是怎么实现的

```python
def route_after_dispatch(state):
    return ["rag_agent_node", "web_agent_node"]   # 返回列表 = 并行
```

路由函数返回**列表**，dispatch 后就同时走向两个 agent。这是 LangGraph 表达并行的方式（在单路图的 `route_after_router` 里见过同样的招）。

### 4. fan-in（汇合等待）是怎么实现的

注意这两行：

```python
graph.add_edge("rag_agent_node", "integration_node")
graph.add_edge("web_agent_node", "integration_node")
```

两个 agent 都连到 integration。LangGraph 的规则：**当一个节点有多条入边时，会等所有上游都到达才触发它**。所以 integration 一定会等 RAG 助手和联网助手都跑完，拿到两份答案，再整合。这就是「并行检索 → 汇合整合」的天然实现，不用写任何等待逻辑。

### 5. 没有反思循环（与单路图的区别）

多智能体图没有 reflection 节点。整合后的答案直接 END。设计取舍：多智能体本身已经是「两路交叉验证 + 整合」，再套反思会增加延迟和复杂度，所以省了。流式也直接在 integration 里逐字输出。

## 重要对象、函数或配置项

| 名称 | 作用 |
|------|------|
| `StateGraph(MultiAgentState)` | 以多智能体状态创建图 |
| `route_after_dispatch` | 返回列表触发并行 fan-out |
| 两条汇合边 | 触发 fan-in 等待 |
| `compile()` | 编译成可运行图 |
| `build_multi_agent_graph()` | 主函数，返回编译图 |

调用方式（见 `06-api-运行图同步流式.md`）：`_select_graph` 在 `mode="multi"` 时选这张图，之后 `.invoke()` / `.astream()` 用法完全相同。SSE 帧里会出现 `dispatch_node`/`rag_agent_node`/`web_agent_node`/`integration_node` 这些节点名，两个 agent 帧还额外带 `answer`（原始回答，供前端折叠展示）。

## 运行方式或使用方式

`build_multi_agent_graph()` 在 `main.py` 启动时调用一次，存到 `app.state.multi_agent_graph`。前端切到「多智能体」模式时，请求带 `mode="multi"` 就走这张图。测试见 `../backend/tests/test_multiagent_graph.py` 和 `../backend/tests/test_multiagent_nodes.py`。

## 修改建议

- 想加第三个助手（比如「数据库 SQL 助手」）：写个新 agent 节点 → 加进 `route_after_dispatch` 的返回列表 → 加一条到 integration 的汇合边。三步搞定。
- 注意所有 agent 的输出字段命名要唯一（`xxx_answer` / `xxx_sources`），integration 才能各取所需。
- integration 的 system 提示词是「综合质量」的关键，调它比调拓扑更影响效果。
- 并行 agent 不要有共享可变状态，各写各的字段，靠状态合并机制汇合。

## 学习检查

1. 多智能体图的 fan-out 和 fan-in，分别用哪两段代码实现的？
2. `integration_node` 为什么一定会等两个 agent 都完成？这个「等待」是谁负责的？
3. 这张图和单路图用了哪些**相同**的 LangGraph API？
4. 多智能体图为什么没有反思循环？这样设计的好处和代价是什么？
5. 如果要再加一个「SQL 助手」并行，需要改哪几处？

---

## 学习路径总结

恭喜走完整条路径！按顺序回顾你掌握了什么：

| 步骤 | 文件 | 掌握的 LangChain/LangGraph 概念 |
|------|------|-------------------------------|
| 1 | services/llm_service.py | ChatOpenAI、消息对象、invoke/stream、bind |
| 2 | services/embedding_service.py | OpenAIEmbeddings、embed_query/documents |
| 3 | core/utils.py | RecursiveCharacterTextSplitter、Document |
| 4 | nodes.py | TypedDict 状态、节点方法、get_stream_writer、路由函数 |
| 5 | graph.py | StateGraph、add_node/edge/conditional_edges、compile、并行与循环 |
| 6 | api.py | invoke 同步、astream 双流（updates+custom）、SSE |
| 7 | multiagent/nodes.py | 智能体=工具+LLM、双 agent 对称设计 |
| 8 | multiagent/graph.py | fan-out/fan-in 并行汇合、与单路图同构 |

接下来可以做的练习：
- 自己加一个新节点（比如「查询改写」节点放在 router 之前），跑通整条链路。
- 把反思逻辑也接到流式路径上，体会「流式反思」的难点。
- 给多智能体图加第三个助手，验证 fan-out/fan-in 的扩展性。
- 读 `tests/` 下的测试，看怎么用 mock 验证图的行为——这是把「会用」变成「会调」的关键。
