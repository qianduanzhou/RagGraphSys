# nodes.py 学习笔记（单路 RAG 图）

> 配套源文件：`../backend/nodes.py`
> 学习阶段：**第 4 步 · LangGraph 状态与节点**（从这里进入 LangGraph，是全项目的核心，慢慢读）

## 文件作用

这个文件定义了单路 RAG 流水线的「灵魂」：

1. **状态**（`GraphState`）—— 数据在图里怎么流动；
2. **6 个节点**（router / qdrant / neo4j / merge / llm / reflection）—— 每一步干什么；
3. **路由函数** —— 决定下一步走哪。

它和 `graph.py` 是一对：`nodes.py` 说「每步做什么」，`graph.py` 说「这些步骤怎么连起来」。本文件先理解「状态」和「节点」，连线留给 `graph.py`。

## 核心知识点

### 1. 用 TypedDict 定义图状态（最重要！）

```python
from typing import TypedDict

class GraphState(TypedDict, total=False):
    question: str
    history: list
    needs_rag: bool
    used_rag: bool
    qdrant_results: list
    neo4j_results: list
    context: str
    sources: list
    answer: str
    reflection_passed: bool
    reflection_feedback: str
    iterations: int
    streaming: bool
```

这是 LangGraph 最关键的概念：**状态（State）是一个在所有节点之间流动的字典**。每个节点读它需要的字段、改它负责的字段，改完的值会自动传给下游节点。

两个小白要点：

- `TypedDict` 只是**类型提示**，运行时它就是个普通字典，不会校验类型。它的作用是让你（和编辑器）一眼看清「这个图里有哪些数据」。
- `total=False` **必须写**！它的意思是「这些字段都是可选的」。因为节点每次只更新部分字段（比如 qdrant 节点只写 `qdrant_results`，不碰别的）。如果没有 `total=False`，就要求每个字段都必须有值，节点会报错。

> 把 `GraphState` 想象成一根「公共数据管道」：问题从一端流进，沿途每个节点往里加自己的产物（检索结果、上下文、答案……），最后从另一端流出完整结果。

### 2. 节点就是「接收状态、返回部分更新」的普通方法

节点不需要继承任何类，签名就是 `state -> dict`：

```python
def qdrant(self, state: GraphState) -> dict:
    results = self.rag.qdrant.search(state["question"], self.settings.qdrant_top_k)
    return {"qdrant_results": results}     # 只返回要更新的字段！
```

关键约定：**节点返回的字典，就是要「合并进」状态的更新**。你不必返回整个状态，只返回改动的字段，LangGraph 会自动帮你合并（reducer）。这个「只返回增量」的设计，是 LangGraph 节点写法的核心。

节点们都被组织在一个 `GraphNodes` 类里，构造时把 `llm`/`rag`/`settings` 传进来（依赖注入）：

```python
class GraphNodes:
    def __init__(self, llm, rag, settings):
        self.llm = llm
        self.rag = rag
        self.settings = settings
```

### 3. 六个节点各自做什么

| 节点 | 读 | 写（返回） | 干什么 |
|------|----|-----------|--------|
| `router` | question | `needs_rag`, `used_rag` | 判断要不要检索（本项目固定为 True） |
| `qdrant` | question | `qdrant_results` | 向量语义检索 |
| `neo4j` | question | `neo4j_results` | 图谱关系检索 |
| `merge` | qdrant_results, neo4j_results | `context`, `sources`, `used_rag` | 融合两路结果成上下文 |
| `llm_generate` | question, context, history | `answer`, `iterations` | 生成回答（流式时逐字吐） |
| `reflection` | question, answer, context | `reflection_passed`, `reflection_feedback` | 审核回答质量 |

注意 merge 节点：它把两路检索结果融合成一个字符串 `context`，并标注来源 `sources`。`used_rag` 表示「这次到底有没有用上检索结果」——它由 `bool(sources)` 决定，过滤掉无关结果后可能为 False。

### 4. get_stream_writer() —— 节点内主动推流（流式核心）

这是 LangGraph 流式输出的关键，在 `llm_generate` 节点里：

```python
from langgraph.config import get_stream_writer

def llm_generate(self, state):
    if state.get("streaming"):
        writer = get_stream_writer()
        buffer = []
        for token in self.llm.chat_stream(messages):
            buffer.append(token)
            writer({"type": "delta", "text": token})   # 边生成边往外推
        answer = "".join(buffer)                         # 同时累积完整答案
    else:
        answer = self.llm.chat(messages)
    return {"answer": answer, "iterations": iterations + 1}
```

理解这一段，就理解了项目「逐字输出」的全部原理：

- `get_stream_writer()` 拿到一个「写入器」，节点内部可以随时往里写自定义事件；
- 每生成一个 token，就 `writer({...})` 推一份出去——前端通过 SSE 实时收到；
- 同时把 token 累积进 `buffer`，最后拼成完整 `answer` 返回给状态。

**一边推流给前端、一边累积给状态**，这就是流式的双轨设计。这些 `writer(...)` 写出的东西，最终会被 `api.py` 里 `astream(stream_mode=["custom"])` 接住（第 6 步会讲）。

### 5. 路由函数 —— 决定下一步走哪

```python
def route_after_router(state) -> list | str:
    if state.get("needs_rag", True):
        return ["qdrant_node", "neo4j_node"]   # 返回列表 = 同时走两条路（并行！）
    return "llm_node"                           # 返回字符串 = 走一条路

def route_after_llm(state) -> str:
    if state.get("streaming"):
        return END                              # 流式直接结束
    return "reflection_node"                    # 非流式进反思

def make_route_after_reflection(max_iterations):
    def _route(state):
        if state.get("reflection_passed", True):
            return END
        if state.get("iterations", 0) >= max_iterations:
            return END                          # 到次数上限，强制结束
        return "llm_node"                       # 回头重新生成
    return _route
```

路由函数的规则很简单：**返回下一个（或几个）节点名**。

- 返回字符串 -> 走单条边；
- 返回列表 -> 并行扇出（fan-out），同时走多条边；
- 返回 `END`（从 `langgraph.graph` 导入）-> 流程结束。

**反思循环的秘密**：`make_route_after_reflection` 让边指回 `llm_node`，就形成了「生成 -> 反思 -> 不合格就再生成」的循环。`iterations` 计数 + `max_iterations` 上限，是为了**防止无限循环**——这个保护新手一定要加。

`make_route_after_reflection` 用了「工厂函数」：外层接收 `max_iterations`，返回内层真正的路由函数。这样图编译时能把不同配置固化进去。

## 关键流程

单路 RAG 图的数据流（配合 `graph.py` 的连线看）：

```
question 流入
   |
router  ──写 needs_rag──┐
   |                     |
   ├(needs_rag=True)─→ [qdrant, neo4j] 并行检索
   |                        |
   |                   merge 融合 → context, sources
   |                        |
   └(needs_rag=False)────→ llm_generate 生成 answer
                               |
                      流式→END / 非流式→reflection
                               |
                      reflection─(不通过)─→ 回 llm_generate
                               └(通过/超限)→ END
```

流式时 `llm_generate` 内部还会通过 `writer` 实时推 delta 事件给前端。

## 重要对象、函数或配置项

| 名称 | 作用 |
|------|------|
| `GraphState` | 图状态定义（TypedDict, total=False） |
| `GraphNodes` | 持有依赖、对外暴露节点方法 |
| 节点方法 | `router/qdrant/neo4j/merge/llm_generate/reflection` |
| `get_stream_writer()` | 节点内推自定义事件（流式用） |
| 路由函数 | `route_after_router`（返回 list=并行）/ `route_after_llm` / `make_route_after_reflection` |
| `END` | 流程结束标记（从 langgraph.graph 导入） |
| `merge_results()` | 融合两路检索（在 `backend/rag/rag_service.py`） |

## 运行方式或使用方式

本文件定义逻辑，不直接运行。`graph.py` 把这些节点和路由连起来编译成图；`api.py` 再调用图。测试见 `../backend/tests/test_nodes.py`（注入假的 llm/rag，不联网）。

## 修改建议

- 加新节点：在 `GraphState` 加字段、在 `GraphNodes` 加方法、在 `graph.py` 注册——三步，不用动现有节点。
- 节点内尽量 `try/except` 把异常兜住返回空（像 qdrant/neo4j 那样），避免单点故障拖垮整张图。
- 改反思逻辑注意 `max_iterations`：去掉上限会无限循环；`reflect()` 失败默认通过，别轻易改成不通过。
- 流式节点的 `writer` 事件类型要和 `api.py` 的消费逻辑（`_summarize_update`）对齐。

## 学习检查

1. `GraphState` 为什么必须加 `total=False`？去掉会怎样？
2. 节点方法为什么只返回「要更新的字段」而不是整个状态？
3. `router` 返回 `["qdrant_node","neo4j_node"]`（列表）和返回 `"llm_node"`（字符串），对图的走法有什么不同？
4. `llm_generate` 里「同时 buffer 累积 + writer 推流」为什么两件事都要做？
5. 反思循环是怎么避免无限循环的？去掉 `iterations` 上限会发生什么？
6. `get_stream_writer()` 写出去的事件，最终被谁接住？（提示：第 6 步见 06-api-运行图同步流式.md）

> 读懂状态和节点后，看 `05-graph-LangGraph构图与编译.md`，把这些节点和路由「连起来、编译成图」。
