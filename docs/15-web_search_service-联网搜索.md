# 联网搜索：Tavily 封装与优雅降级（web_search_service.py）

> 多智能体模式（见 [08 篇](08-multiagent-graph-多智能体构图.md)）里有一个「联网智能体」会查实时信息。它调用的就是 [services/web_search_service.py](../backend/services/web_search_service.py)。这一篇很短，重点理解一个模式：**「模型边界服务」+「不可用时优雅降级」**。

## 它扮演什么角色

本项目有三个「模型边界服务」，写法几乎一模一样，都是「把外部依赖封一层、业务代码只调方法」：

| 服务 | 边界 | 文件 |
|------|------|------|
| LLM | 对话模型 | [llm_service.py](../backend/services/llm_service.py)（[01 篇](01-llm_service-对话模型入门.md)） |
| Embedding | 向量化模型 | [embedding_service.py](../backend/services/embedding_service.py)（[02 篇](02-embedding_service-向量化.md)） |
| WebSearch | 联网搜索 | [web_search_service.py](../backend/services/web_search_service.py)（本篇） |

好处：多智能体图代码（[multiagent/nodes.py](../backend/multiagent/nodes.py)）只调 `web.search(...)`，不直接碰 Tavily 客户端。换搜索提供商只改这一个文件。

---

## 构造：可选依赖 + 优雅降级

```python
class WebSearchService:
    def __init__(self, settings, client=None):
        self.settings = settings
        self._max_results = settings.tavily_max_results
        if client is not None:
            self._client = client                      # 测试/自定义可注入
        elif settings.tavily_api_key:
            try:
                from tavily import TavilyClient        # lazy import
                self._client = TavilyClient(api_key=settings.tavily_api_key)
            except Exception as exc:
                self._client = None                    # 初始化失败也不崩
        else:
            self._client = None                        # 没配 key，不可用
```

关键点：

- **`client=None` 参数**：和 LLMService/EmbeddingService 一样，留一个注入点，测试时传假的 client（不真发请求）。这是「依赖注入」。
- **`tavily_api_key` 为空就 `_client = None`**：Tavily 是可选功能。`.env` 没配 `TAVILY_API_KEY`，联网搜索整个关闭，但**应用照常启动**（前端会把「多智能体」按钮置灰）。
- **初始化异常也降级**：连 `TavilyClient(...)` 本身失败都包了 try/except，绝不因为搜索挂掉而连累主服务。

### available 属性

```python
@property
def available(self) -> bool:
    return self._client is not None
```

用 `@property`（[00 篇](00-Python语法速成.md#13-property--把方法当属性用)）暴露一个布尔。[/health 接口](13-api-FastAPI框架详解.md)会读它，告诉前端「联网搜索可用吗」；前端据此决定多智能体按钮能不能点（见 [16 篇](16-frontend-前后端对接.md)）。

---

## search：归一化结果，异常返回空

```python
def search(self, query, max_results=None):
    if not self.available or not query:
        return []                                     # 不可用/空 query 直接返回空
    try:
        resp = self._client.search(query=query, max_results=limit, search_depth="basic")
    except Exception as exc:
        return []                                     # 异常也返回空，不抛错
    results = resp.get("results", [])
    return [
        {"title": ..., "url": ..., "content": ..., "score": ...}
        for r in results if isinstance(r, dict)
    ]
```

三个要点：

- **永远不抛错**：不可用、空 query、Tavily 异常，统统返回 `[]`。上层 agent 拿到空列表就自己降级（联网没结果就用已有知识答）。
- **归一化**：把 Tavily 返回的原始结构映射成统一的 `{title, url, content, score}`，让 agent 不用关心 Tavily 的字段细节。
- **`isinstance(r, dict)` 防御**：跳过意外的非字典元素。

---

## 它在多智能体里的位置

```
多智能体图
   ├─ rag_agent_node  ─► RAGService.retrieve（向量+图谱）
   └─ web_agent_node  ─► WebSearchService.search（本篇）─► [{title,url,content}]
                                                          └─► integration 整合两路答案
```

联网不可用（没配 key）时，`web_agent_node` 拿到空结果、自然降级；同时前端检测到 `/health` 的 `web_search=false`，把「多智能体」按钮禁用，从入口就避免用户触发。

---

## 修改建议

- **换搜索提供商**（如 SerpAPI）：只改这个文件的 `__init__` 和 `search`，对外接口 `available`/`search` 不变，调用方零改动。这就是「模型边界服务」抽象的价值。
- 别让 `search` 抛异常——上层依赖它「最坏返回空」的契约。

---

## 学习检查

1. 三个「模型边界服务」（LLM/Embedding/WebSearch）的写法有什么共同点？为什么要这么封装？
2. `tavily_api_key` 留空时，应用还能启动吗？前端怎么知道联网不可用？
3. `search` 为什么任何情况都不抛异常，而是返回 `[]`？
4. `available` 为什么用 `@property` 而不是普通方法？
5. 想把 Tavily 换成另一个搜索 API，需要改哪些地方？
