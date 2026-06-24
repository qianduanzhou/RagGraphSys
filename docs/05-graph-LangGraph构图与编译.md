# graph.py 学习笔记（单路 RAG 图构建）

> 配套源文件：`../backend/graph.py`
> 学习阶段：**第 5 步 · LangGraph 构图与编译**（读完 04-nodes-LangGraph状态与节点.md 再读这个，它把节点连起来）

## 文件作用

上一个文件 `../backend/nodes.py` 定义了「每一步做什么」。这个文件负责把这些步骤**用边连起来、编译成一个可运行的图**。它很短，但包含了 LangGraph 构图的全部核心 API。

读它之前，先在脑子里回顾 `04-nodes-LangGraph状态与节点.md` 的六个节点：`router / qdrant / neo4j / merge / llm / reflection`，以及三个路由函数。

## 核心知识点

### 1. StateGraph —— 创建一张图

```python
from langgraph.graph import END, START, StateGraph

graph = StateGraph(GraphState)   # 传入状态类型，告诉图"数据长这样"
```

`StateGraph(GraphState)` 创建一张以 `GraphState` 为状态的图。状态类型决定所有节点共享的数据结构（见 `04-nodes-LangGraph状态与节点.md`）。

`START` 和 `END` 是两个特殊标记：`START` 表示图的入口，`END` 表示流程结束（从 `langgraph.graph` 导入）。

### 2. add_node —— 注册节点

```python
nodes = GraphNodes(llm=llm, rag=rag, settings=settings)

graph.add_node("router_node", nodes.router)
graph.add_node("qdrant_node", nodes.qdrant)
graph.add_node("neo4j_node", nodes.neo4j)
graph.add_node("merge_node", nodes.merge)
graph.add_node("llm_node", nodes.llm_generate)
graph.add_node("reflection_node", nodes.reflection)
```

`add_node("名字", 函数)`：第一个参数是节点的**名字（字符串）**，路由时用它来引用；第二个参数是节点方法（来自 `GraphNodes`）。注意名字和方法名不必一样，但建议对应，少踩坑。

### 3. add_edge —— 固定边（A 完了一定去 B）

```python
graph.add_edge(START, "router_node")       # 入口先到 router
graph.add_edge("qdrant_node", "merge_node") # qdrant 完了去 merge
graph.add_edge("neo4j_node", "merge_node")  # neo4j 完了也去 merge
graph.add_edge("merge_node", "llm_node")    # merge 完了去 llm
```

固定边 = 不用判断，A 跑完一定去 B。

**这里藏着并行的秘密**：`qdrant_node` 和 `neo4j_node` 都指向 `merge_node`。LangGraph 看到两条边都汇入同一节点，会**等两条都完成**才触发 merge——这就是「双路并行检索、汇合融合」。fan-out（扇出）由路由函数返回列表实现，fan-in（汇合）就是这样「多条边指向同一节点」自然形成的。

### 4. add_conditional_edges —— 条件边（根据状态动态选路）

```python
graph.add_conditional_edges("router_node", route_after_router)
graph.add_conditional_edges("llm_node", route_after_llm)
graph.add_conditional_edges("reflection_node", make_route_after_reflection(settings.max_reflection_iterations))
```

`add_conditional_edges("起点", 路由函数)`：起点跑完后，调用路由函数，**用它的返回值决定去哪**。路由函数怎么写、返回什么，在 `04-nodes-LangGraph状态与节点.md` 里讲过——返回字符串走单边、返回列表并行、返回 `END` 结束。

回顾三个条件边的路由逻辑：

- `router_node` 后：`needs_rag` 为真就并行去 `[qdrant, neo4j]`，否则直接去 `llm`；
- `llm_node` 后：流式就直接 `END`，否则进 `reflection`；
- `reflection_node` 后：通过或超次数就 `END`，否则回 `llm` 重试（形成反思循环）。

### 5. compile() —— 编译成可执行图

```python
compiled = graph.compile()
```

连好所有节点和边后，调用 `compile()` 把它**编译成一个可运行的对象**（`CompiledStateGraph`）。编译后就不能再改结构了。之后无论是同步 `.invoke()` 还是异步流式 `.astream()`，用的都是这个 `compiled`。

## 关键流程

完整的图拓扑（把 04-nodes-LangGraph状态与节点.md 的数据流翻译成连线）：

```
START
  |
  v
router_node ──conditional──┐
   needs_rag=True            needs_rag=False
       |                         |
   fan-out                      直接汇入
   ┌───┴───┐                     |
 qdrant  neo4j                   |
   └───┬───┘                     |
     fan-in                      |
       v                         v
   merge_node ──────────────→ llm_node
                                 |
                          conditional
                          流式→END  非流式→reflection_node
                                              |
                                        conditional
                                        通过/超限→END
                                        不通过→回 llm_node
```

记住三件事：路由函数返回列表=并行；多边指向同一节点=汇合等待；条件边指回上游=循环。

## 重要对象、函数或配置项

| 名称 | 作用 |
|------|------|
| `StateGraph(GraphState)` | 以某状态类型创建图 |
| `START` / `END` | 图的入口/出口标记 |
| `add_node(名字, 方法)` | 注册节点 |
| `add_edge(A, B)` | 固定边：A 完了一定去 B |
| `add_conditional_edges(起点, 路由函数)` | 条件边：按返回值选路 |
| `compile()` | 编译成可运行图 |
| `CompiledStateGraph` | 编译产物，支持 `.invoke()` / `.astream()` |
| `build_graph()` | 本文件的主函数，组装并返回编译图 |

文件底部还有个 `run_graph()` 便捷函数，是「问一句就跑」的简单封装，主要给脚本/测试用。Web 服务里走的是 `api.py` 的流式调用。

## 运行方式或使用方式

`build_graph()` 在 `main.py` 启动时被调用一次，把编译好的图存到 `app.state.graph`。之后每个用户请求复用这同一个编译图（图是无状态的，每次请求传自己的初始状态）。想单独测构图，看 `../backend/tests/test_graph.py`，它验证节点拓扑顺序、流式 token 交错。

## 修改建议

- 加节点三步走：`GraphState` 加字段 → `GraphNodes` 加方法 → 本文件 `add_node` + 连边。
- 想加并行分支：让某路由函数返回列表（fan-out），再用多条边指向下一节点（fan-in）。
- 想加自检/循环：用条件边指回上游节点，**务必加计数上限**防止死循环（参考反思节点的写法）。
- `compile()` 之后就别再 `add_node`，要改结构请在编译前改。

## 学习检查

1. `StateGraph(GraphState)` 里的 `GraphState` 起什么作用？
2. 固定边 `add_edge` 和条件边 `add_conditional_edges` 什么时候各用哪个？
3. `qdrant` 和 `neo4j` 两条边都连到 `merge_node`，LangGraph 怎么知道要等两个都完成？这种模式叫什么？
4. 路由函数返回 `["A","B"]` 和返回 `"A"` 对图的执行有什么区别？
5. 为什么 `compile()` 之后不能改图结构？图被多次请求复用，会不会串数据？
6. 反思循环是靠哪个 API 实现的？为什么不会无限转下去？

> 图建好、编译完之后，下一步看 `06-api-运行图同步流式.md`，理解这个编译好的图怎么被「同步调用」和「异步流式调用」。
