# 向量库：Qdrant 怎么存、怎么检索（qdrant_store.py）

> 本项目「向量」这一路，全部由 [rag/qdrant_store.py](../backend/rag/qdrant_store.py) 承担。它把文本块变成向量后存进 Qdrant，问答时按相似度检索。这一篇讲清楚：集合、向量、payload、cosine 检索是怎么一回事。

## 先搞懂：为什么需要向量库

传统数据库（MySQL）靠「精确匹配」找：`WHERE name = '苹果'`。但用户问「苹果公司出了什么手机」，文档里写的是「Apple 发布了 iPhone」，**字面完全不同**，精确匹配找不到。

向量库解决「**意思相近**」的检索：先把每段文本变成一个「向量」（一串数字，见 [02 篇向量化](02-embedding_service-向量化.md)），意思越近的文本向量越接近。检索时把问题也变成向量，找「向量距离最近」的文档。

Qdrant 就是一个专门存向量、算相似度的数据库。

---

## 核心概念：4 个词记住就够

| 概念 | 类比 | 本项目 |
|------|------|--------|
| **集合 collection** | 数据库里的一张表 | `rag_documents` |
| **点 point** | 表里的一行 | 一个文本块 |
| **向量 vector** | 这行的「特征列」 | 一串 2048 维浮点数 |
| **payload** | 这行的其它字段 | `{text, source, chunk_index, created_at}` |

一个点 = 一个 id + 一个向量 + 一个 payload。向量用来算相似度，payload 用来存原文和元信息、还能当过滤条件。

---

## 文件结构与核心方法

| 方法 | 方向 | 作用 |
|------|------|------|
| `ensure_collection()` | 管理 | 集合不存在就按维度创建 |
| `upsert(texts, metadatas)` | 写 | 向量化后连同 payload 存入 |
| `search(query, top_k)` | 读 | cosine 相似度检索 |
| `count()` | 读 | 点总数 |
| `delete_by_source(source)` | 删 | 按 payload 的 source 删 |
| `scan_all()` | 读 | 翻页扫描所有点（聚合文档列表用） |

---

## 一、连接与集合

```python
self.client = QdrantClient(url=settings.qdrant_url)   # http://localhost:6333
```

```python
def ensure_collection(self):
    dim = self.embedding.dimension                    # 2048
    if self.client.collection_exists(self.collection):
        return                                        # 已存在不动
    self.client.create_collection(
        collection_name=self.collection,
        vectors_config=models.VectorParams(
            size=dim,
            distance=models.Distance.COSINE,          # 距离算法：余弦
        ),
    )
```

### 维度必须一致（重点）

- 集合建好后，**所有存进去的向量必须都是 `dim` 维**（本项目 2048）。维度和 embedding 模型绑定，换模型就要换维度，通常得重建集合。
- `ensure_collection` 在 [main.py lifespan](09-配置与启动-应用装配.md) 启动时调用一次。

### 为什么用 cosine

距离算法有几种：`COSINE`（余弦，看方向）、`DOT`（点积）、`EUCLID`（欧氏，看绝对距离）。文本语义检索几乎都用 **cosine**，因为它只关心向量「方向」是否一致，不受文本长短影响。结果 score 在 0~1，越接近 1 越相似。

---

## 二、写入：upsert

```python
def upsert(self, texts, metadatas=None):
    metadatas = metadatas or [{} for _ in texts]
    vectors = self.embedding.embed_batch(texts)       # 批量向量化（见02篇）
    points = [
        models.PointStruct(
            id=str(uuid.uuid4()),                      # 每个点一个唯一 id
            vector=vector,
            payload={**meta, "text": text},            # payload = 元信息 + 原文
        )
        for text, vector, meta in zip(texts, vectors, metadatas)
    ]
    self.client.upsert(collection_name=self.collection, points=points)
```

要点：

- **先向量化再存**：`qdrant_store` 不自己算向量，而是调 `embedding_service`（职责分离）。所以 [main.py](../backend/main.py) 里 `QdrantStore(settings, embedding)` 要把 embedding 传进去。
- **payload 一定带原文 `text`**：向量本身只是一串数字，**无法还原回文本**。所以检索时只能拿到「向量相似 + payload 里的 text」，payload 丢了 text 就等于丢了内容。
- **payload 还带来源 `source`**（文件名），这是后面「按文档删除」「按文档聚合」的基础。
- `{**meta, "text": text}` 是字典展开：把 meta 里的键值都展开，再补一个 `text`。
- `upsert` = insert or update：id 相同就更新，不同就插入。这里每次用新 uuid，等于纯插入。

> 写入时机：在 [rag_service.ingest_text](12-rag_service-RAG编排与混合检索.md) 里，文本被 [split_text](03-utils-文本切分.md) 切成块后，每个块调 `upsert`。

---

## 三、检索：search

```python
def search(self, query, top_k=None):
    limit = top_k or self.settings.qdrant_top_k        # 默认 5 条
    query_vector = self.embedding.embed(query)         # 把问题也变成向量
    response = self.client.query_points(
        collection_name=self.collection,
        query=query_vector,
        limit=limit,
        with_payload=True,
    )
    results = []
    for point in response.points:
        payload = point.payload or {}
        results.append({
            "text": payload.get("text", ""),
            "score": float(point.score) if point.score is not None else 0.0,
            "source": payload.get("source", "unknown"),
            "payload": payload,
        })
```

核心流程就一句：**「问题变向量 → Qdrant 算 cosine → 返回最相似的 top_k 条」**。每条结果带回：原文 `text`、相似度 `score`（0~1）、来源 `source`。

### score_threshold：相关度过滤

注意 score 不是这里过滤的。本项目在 [12 篇 merge_results](12-rag_service-RAG编排与混合检索.md) 里用 `qdrant_score_threshold`（默认 0.35）丢弃太低分的结果。这样闲聊（「你好」）自然不命中，context 为空，由 LLM 当通用对话处理。

---

## 四、按文档删除：用 payload 过滤

```python
def delete_by_source(self, source):
    self.client.delete(
        collection_name=self.collection,
        points_selector=models.Filter(
            must=[models.FieldCondition(
                key="source",
                match=models.MatchValue(value=source),
            )]
        ),
    )
```

- payload 的 `source` 字段此时成了**过滤条件**：删掉所有 `source == 某文件名` 的点。
- 删除条数用「删之前 count - 删之后 count」估算（`count` 返回 -1 时不可用）。

> 这正是为什么写入时一定在 payload 带上 `source`——为了能精确、整文档地清理。

---

## 五、扫描全表：scroll 翻页

```python
def scan_all(self, batch_size=256):
    points = []
    offset = None
    while True:
        records, next_offset = self.client.scroll(
            collection_name=self.collection,
            limit=batch_size,
            offset=offset,
            with_payload=True, with_vectors=False,
        )
        for record in records:
            points.append({"id": ..., "payload": ...})
        if next_offset is None:
            break
        offset = next_offset
```

- `scroll` 返回 `(这一页, 下一页的游标)`，`next_offset is None` 表示到末尾。
- 用途：[api.py](../backend/api.py) 的 `/docs` 接口扫所有点，按 `source` 聚合成「文档列表」展示给前端。
- `with_vectors=False`：扫描时不带向量（向量很大、用不到，省内存）。

---

## 它在整条链路里的位置

```
导入文档：
   RagService.ingest_text ─► split_text(切块)
                          └► QdrantStore.upsert(向量+payload)   ◄── 本篇

问答检索（qdrant_node）：
   用户问题 ─► EmbeddingService.embed(问题向量化)
            └► QdrantStore.search(cosine检索) ─► [{text,score,source}]  ◄── 本篇
                          └► 和 Neo4j 结果一起交给 merge_node 融合
```

---

## 修改建议

- **想换 embedding 模型**：改 `.env` 的 `EMBEDDING_MODEL` 和 `EMBEDDING_DIMENSION`，**必须同时**删掉旧集合（维度不同，旧数据作废）。
- **想改检索条数**：调 `QDRANT_TOP_K`。
- **想加 payload 过滤**（比如只搜某个文档）：在 `search` 的 `query_points` 里加 `query_filter=models.Filter(...)`。
- **千万不要让 payload 丢了 `text`**，否则检索出命中却拿不到内容。

---

## 学习检查

1. 向量、payload、点 三者什么关系？为什么 payload 里必须存 `text`？
2. 问题和文档都变成向量后，Qdrant 靠什么判断「相似」？cosine 在看什么？
3. 集合的 `size=2048` 是怎么定的？换 embedding 模型时它要跟着改吗？
4. 「按文档删除」是靠什么字段实现的？写入时哪一步保证了这个字段存在？
5. `score_threshold` 在哪个文件、哪个函数生效？为什么不放在 qdrant_store 里？
6. `scan_all` 为什么要翻页（`scroll`）而不是一次 `get` 全部？
