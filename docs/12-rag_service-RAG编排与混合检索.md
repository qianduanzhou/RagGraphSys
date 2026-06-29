# RAG 编排：把向量库和图谱库串起来（rag_service.py）

> [10 篇](10-neo4j_store-知识图谱.md) 讲了图谱库，[11 篇](11-qdrant_store-向量库.md) 讲了向量库。它们各自只会「存」和「查」。而真正把它们和 LLM 组织成「**导入一篇文档 → 向量+图谱双写**」「**问一个问题 → 双路检索 → 融合成上下文**」这套完整流程的，是 [rag/rag_service.py](../backend/rag/rag_service.py)。这一篇是承上启下的核心。

## 它扮演什么角色

```
        LLM(对话/抽取)        Qdrant(向量)        Neo4j(图谱)
            \                    |                    /
             \                   |                   /
              \_______ RagService _______/
                      编排层：导入、检索、融合、删除
                            ▲
                            │ 被谁调用
                  api.py 路由 + LangGraph 节点
```

`RagService` 是一个「门面（facade）」：上层（[api.py](../backend/api.py)、[nodes.py](../backend/nodes.py)）不用同时关心 Qdrant 和 Neo4j 两个对象，只调它一个。

---

## 构造：把三个底层服务装进来

```python
class RagService:
    def __init__(self, qdrant, neo4j, llm, settings):
        self.qdrant = qdrant
        self.neo4j = neo4j
        self.llm = llm
        self.settings = settings
```

注意它本身**不创建**这些服务，而是接收 [main.py lifespan](09-配置与启动-应用装配.md) 已经建好的实例。这是「依赖注入」，便于测试时替换成假的实现。

---

## 一、导入文档：ingest_text（双写）

这是「上传文档后发生了什么」的核心：

```python
def ingest_text(self, text, source="manual"):
    chunks = split_text(text, chunk_size, chunk_overlap)   # 1. 切块（见03篇）
    created_at = int(time.time())
    metadatas = [{"source": source, "chunk_index": i, "char_len": len(c),
                  "created_at": created_at} for i, c in enumerate(chunks)]

    upserted = self.qdrant.upsert(chunks, metadatas)       # 2. 向量库：每个块写一条

    triples = self.llm.extract_graph("\n\n".join(chunks[:6]))  # 3. LLM 抽三元组
    merged = self.neo4j.add_knowledge(...)                    # 4. 图谱库：写关系

    return {"chunks": upserted, "triples": merged}
```

四步连起来就是「一篇文档」的完整入库：

```
原文 ─► split_text 切成块 ─► Qdrant 存向量(每块一条)
                            │
                            └─ chunks[:6] 拼起来 ─► LLM.extract_graph 抽三元组
                                                  └─► Neo4j 存关系
```

几个设计要点：

- **`source` 贯穿始终**：同一个文件名一路传到 Qdrant 的 payload、Neo4j 的 `sources` 数组。这是「按文档删除」「按文档聚合」的基石。
- **图谱只抽前 6 块**：`chunks[:6]`。因为图谱抽取（LLM 调用）成本高，长文档全抽太贵；文档开头通常包含主要实体。这是成本与效果的权衡。
- **`created_at` 时间戳**：存进 payload，让 `/docs` 接口能按时间排序展示。
- 返回 `chunks`（向量条数）和 `triples`（关系条数）两个统计，给前端反馈。

---

## 二、检索：retrieve（双路并发）

```python
def retrieve(self, query, top_k=None):
    vector_hits = []
    graph_hits = []
    try:
        vector_hits = self.qdrant.search(query, top_k=limit)   # 向量路
    except Exception as exc:
        logger.exception("Qdrant retrieval failed: %s", exc)

    try:
        keywords = self.llm.extract_keywords(query) or [query[:32]]
        graph_hits = self.neo4j.search(keywords, limit=limit)  # 图谱路
    except Exception as exc:
        logger.exception("Neo4j retrieval failed: %s", exc)

    return {"qdrant": vector_hits, "neo4j": graph_hits}
```

两路各自独立，**互不阻塞**：

- **向量路**：问题直接变向量，cosine 检索（[11 篇](11-qdrant_store-向量库.md)）。
- **图谱路**：先用 LLM 从问题里抽关键词（[01 篇 extract_keywords](01-llm_service-对话模型入门.md)），再 Neo4j 一跳遍历（[10 篇](10-neo4j_store-知识图谱.md)）。
- **优雅降级**：任一路异常都 `try/except` 吞掉，只记日志，另一路照常返回。这样「Qdrant 挂了」也不至于整个问答崩溃。
- 抽不出关键词时回退 `query[:32]`，保证图谱路不至于空跑。

> 这里的 `retrieve` 和 LangGraph 里 `qdrant_node`+`neo4j_node` 并行检索是**同一逻辑的两种实现**：流式接口走 LangGraph 并行节点，非流式/复用走这里。结果格式一致。

---

## 三、融合：merge_results（统一上下文）

这是本文件最重要的函数，被两处共用（`GraphNodes.merge` 和 `RagService.build_context`），保证格式化逻辑只写一次：

```python
def merge_results(qdrant_hits, neo4j_hits, score_threshold=0.0):
    # 1. 相关度过滤：丢掉太低分的向量命中
    qdrant_hits = [h for h in qdrant_hits if float(h.get("score", 0.0)) >= score_threshold]

    parts = []
    sources = []

    # 2. 向量结果：带 score 和 source
    if qdrant_hits:
        parts.append("【向量检索结果 / Qdrant】")
        for i, hit in enumerate(qdrant_hits, 1):
            parts.append(f"[V{i}] (score={...}, src={...}) {hit['text']}")
            sources.append({"type": "qdrant", "content": hit["text"], "score": ..., "source": ...})

    # 3. 图谱结果：三元组，无 score
    if neo4j_hits:
        parts.append("\n【知识图谱关系 / Neo4j】")
        for hit in neo4j_hits:
            line = f"{hit['head']} -[{hit['rel']}]-> {hit['tail']}"
            parts.append(line)
            sources.append({"type": "neo4j", "content": line})

    return "\n".join(parts).strip(), sources
```

它产出两样东西：

- **`context` 字符串**：拼成「【向量检索结果】... 【知识图谱关系】...」的文本，最终塞进 LLM 的 prompt 作为参考资料。
- **`sources` 列表**：带 `type` 标签（`qdrant`/`neo4j`）的来源明细，回传给前端展示「这个答案来自哪些片段」（前端徽章用，见 [16 篇](16-frontend-前后端对接.md)）。

### score_threshold 只管向量

注意过滤只作用在 `qdrant_hits` 上：Neo4j 关系是关键词精确命中，没有分数，不参与过滤。所以阈值只挡掉「语义勉强沾边」的向量结果，不影响图谱。

### build_context：检索 + 融合一步到位

```python
def build_context(self, query, top_k=None):
    retrieved = self.retrieve(query, top_k=top_k)
    context, sources = merge_results(retrieved["qdrant"], retrieved["neo4j"],
                                     score_threshold=self.settings.qdrant_score_threshold)
    used_rag = bool(sources)     # 有任何来源才算「用到了知识库」
    return {"context": context, "sources": sources, "used_rag": used_rag}
```

- `used_rag`：一个布尔，告诉前端/LLM「这次回答有没有用到知识库」。闲聊类问题检索不到东西，`used_rag=False`，LLM 就当通用对话答（不强行套资料）。

---

## 四、删除：delete_document（双删）

```python
def delete_document(self, source):
    chunks = self.qdrant.delete_by_source(source)      # 删向量
    relations = self.neo4j.delete_by_source(source)    # 删关系
    return {"source": source, "chunks": chunks, "relations": relations}
```

导入时双写，删除时就要**双删**，否则删了文档内容却残留图谱关系。两个底层 store 都实现了各自的 `delete_by_source`（见 10、11 篇），这里只是依次调用并汇总统计。

`delete_documents(sources)` 是它的批量版：循环调用单项删除，单项失败用 `try/except` 兜住不中断整批，返回逐项 ok/failed 明细（和批量导入 `ingest_files` 对齐）。

---

## 五、它在整条链路里的位置（汇总）

```
导入：  api /ingest → rag.ingest_text → {split_text → qdrant.upsert}
                                          {llm.extract_graph → neo4j.add_knowledge}

检索：  LangGraph qdrant_node/neo4j_node ┐
        或 rag.retrieve / build_context  ┘ → merge_results → context → llm_node 生成

删除：  api /docs/delete → rag.delete_document → {qdrant.delete_by_source + neo4j.delete_by_source}
```

---

## 修改建议

- **加第三路检索**（比如全文搜索）：在 `retrieve` 里加一路，在 `merge_results` 里加一个 `if xxx_hits:` 分支拼进 `parts` 和 `sources`，记得给来源打 `type` 标签。
- **图谱抽取想覆盖更多块**：调大 `chunks[:6]` 里的 6，但注意成本。
- **`merge_results` 改格式时**：要同时意识到它被「非流式 LangGraph merge 节点」和「流式 build_context」共用，改一处影响两条路径。

---

## 学习检查

1. `ingest_text` 做了哪四件事？为什么图谱只抽 `chunks[:6]`？
2. `source`（文件名）从导入一直传到删除，它经过了哪几个地方？为什么必须贯穿？
3. `retrieve` 里两路检索为什么各自包 `try/except`？某一路挂了会怎样？
4. `merge_results` 产出 `context` 和 `sources` 两个东西，分别给谁用？
5. `score_threshold` 为什么只过滤 qdrant 不过滤 neo4j？
6. `used_rag` 是怎么算出来的？它 False 时对 LLM 的行为有什么影响？
7. 删除文档时只删了 Qdrant 没删 Neo4j，会出现什么问题？
