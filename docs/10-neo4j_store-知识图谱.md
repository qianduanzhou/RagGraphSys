# 知识图谱：Neo4j 怎么存、怎么查（neo4j_store.py）

> 本项目的「图谱」这一路，全部由 [rag/neo4j_store.py](../backend/rag/neo4j_store.py) 一个文件承担。它负责把文本里抽出来的「实体 + 关系」存进 Neo4j 图数据库，并在问答时按关键词一跳遍历找关系。这一篇把它讲透。

## 先搞懂：什么是知识图谱

向量库（[11 篇](11-qdrant_store-向量库.md)）擅长「语义相似」——「苹果公司」和「Apple」意思相近能召回。但它不擅长「关系推理」：比如「张三在哪上班」「乔布斯创办了什么」。

知识图谱专门存**关系**。它的最小单元是**三元组**：

```
(头实体) -[关系]-> (尾实体)

乔布斯 -[创办]-> 苹果公司
苹果公司 -[生产]-> iPhone
```

把这些三元组画出来，就是一张「实体为点、关系为边」的图。问答时，沿着边一跳就能找到关联实体，比向量相似更精准。

本项目是 **向量 + 图谱混合检索**：两个一起召回，结果融合（见 [12 篇](12-rag_service-RAG编排与混合检索.md)）。

---

## Neo4j 与 Cypher 速览

Neo4j 是图数据库。它的查询语言叫 **Cypher**，用括号表示节点、箭头表示关系，非常直观：

```cypher
MATCH (a:Entity {name: "乔布斯"})-[:创办]->(b)
RETURN b.name
```

- `(a:Entity)`：变量 `a`，标签 `Entity`（类似「类型」）。
- `{name: "乔布斯"}`：按属性精确匹配。
- `-[r:创办]->`：一条类型为 `创办` 的关系。
- 本项目所有实体都打 `:Entity` 标签、带 `name` 属性；关系类型是动态的（如 `WORKS_FOR`、`FOUNDED`）。

---

## 文件结构与核心方法

| 方法 | 方向 | 作用 |
|------|------|------|
| `__init__` | — | 建驱动连接（`bolt://`） |
| `verify()` | — | 启动时连通性检查 |
| `add_knowledge(triples, source)` | 写 | 把三元组合并写入图 |
| `delete_by_source(source)` | 删 | 按文档来源精确清理 |
| `search(entities, limit)` | 读 | 按关键词一跳检索关系 |
| `count_entities()` | 读 | 实体总数（健康检查用） |

---

## 一、连接：GraphDatabase.driver

```python
from neo4j import GraphDatabase

class Neo4jStore:
    def __init__(self, settings: Settings):
        self.driver = GraphDatabase.driver(
            settings.neo4j_uri,                          # bolt://localhost:7687
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
```

- `driver` 是一个连接池，**长期持有**（建在 `lifespan`，应用停止才 `close()`）。
- 每次操作开一个 `session`，用完自动关（`with self.driver.session() as session`，见 [00 篇 with](00-Python语法速成.md#9-with--上下文管理器自动收尾)）。

---

## 二、写入：三元组 → MERGE（去重合并）

这是最关键的部分。一条三元组 `(head, rel, tail)` 怎么入库：

```python
cypher = (
    "MERGE (a:Entity {name: $head}) "      # 头实体不存在则创建
    "MERGE (b:Entity {name: $tail}) "      # 尾实体不存在则创建
    "MERGE (a)-[r:{rel_type}]->(b) "       # 关系不存在则创建
    "SET r.sources = CASE ... END"         # 给关系打上来源标记
)
tx.run(cypher, head=head, tail=tail, source=source)
```

### MERGE vs CREATE（重点）

- `CREATE`：永远新建，会重复（同一个「苹果公司」会出现好多次）。
- `MERGE`：**「没有就建，有就复用」**，天然去重。本项目全程用 MERGE，保证一个实体只有一份。

### 参数化 vs 字符串拼接（安全重点）

注意 `$head`、`$tail` 是用**参数**传的（`tx.run(cypher, head=head, ...)`），这是防注入的安全写法。

但关系类型 `{rel_type}` 是**直接拼进字符串**的——因为 Cypher 不支持把关系类型当参数。所以 [utils.py](../backend/core/utils.py) 的 `sanitize_relation_type` 先把它清洗成只含 `[A-Za-z0-9_]`：

```python
def sanitize_relation_type(rel: str, fallback="RELATES_TO") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", rel or "").strip("_").upper()
    return cleaned[:48] or fallback
```

> 关系类型来自 LLM 抽取（见 [01 篇 extract_graph](01-llm_service-对话模型入门.md)），可能是中文、带特殊符号。不清洗直接拼进 Cypher 会导致语法错误甚至注入。这段清洗是**必须的**。

### execute_write：事务

```python
with self.driver.session() as session:
    session.execute_write(self._merge_triples, triples, source)
```

`execute_write` 把这批写入包成一个**事务**：要么全成功，要么全回滚。`_merge_triples` 是个被它调用的静态方法（注意第一个参数是 `tx`，事务对象，由 Neo4j 自动传入）。

---

## 三、按文档来源删除（为什么能精确清理）

文档会被删、会被重新导入。本项目实现了一套**「关系带来源标记」**的删除机制，靠的是给每条关系维护一个 `sources` 数组：

```cypher
-- 写入时：给关系打来源
SET r.sources = CASE
  WHEN r.sources IS NULL THEN [$source]
  WHEN $source IN r.sources THEN r.sources
  ELSE r.sources + $source END
```

意思是：一条关系可能被多个文档贡献（A 文档说乔布斯创办苹果，B 文档也这么说）。`sources` 数组累加去重存所有贡献它的文件名。

删除某文档时（`delete_by_source`）：

```cypher
MATCH (a)-[r]->(b)
WHERE $source IN r.sources
WITH r, [s IN r.sources WHERE s <> $source] AS rest   -- 从数组移除该来源
SET r.sources = rest
WITH r WHERE size(r.sources) = 0                       -- 数组空了才删
DELETE r
```

这样**多文档共享的关系不会误删**：只有当所有贡献文档都被删，关系才消失。最后还清理变孤立的实体节点：

```cypher
MATCH (n:Entity) WHERE NOT (n)--() DELETE n
```

> 历史数据（没有 `sources` 属性）不会被误删，因为 `$source IN NULL` 在 Cypher 里是假。

---

## 四、检索：模糊一跳遍历

```python
def search(self, entities, limit=5):
    cypher = (
        "MATCH (a:Entity) "
        "WHERE ANY(e IN $entities WHERE "
        "  toLower(a.name) CONTAINS toLower(e) OR toLower(e) CONTAINS toLower(a.name)) "
        "MATCH (a)-[r]-(b) "
        "WITH a, r, b WHERE a.name <> b.name "
        "RETURN a.name AS head, type(r) AS rel, b.name AS tail "
        "LIMIT toInteger($limit)"
    )
```

逐行拆解：

1. `entities` 是从用户问题里抽出的关键词（见 [01 篇 extract_keywords](01-llm_service-对话模型入门.md)）。
2. `WHERE ANY(e IN $entities WHERE ... CONTAINS ...)`：找名字里**包含**任一关键词（或被关键词包含）的实体。`toLower` 做大小写无关匹配。这是**模糊匹配**，容错关键词不完全等于实体名。
3. `MATCH (a)-[r]-(b)`：从命中的实体出发，找它**一步以内**的所有关系（注意是 `-[r]-`，方向无关，即出边和入边都算）。
4. `a.name <> b.name`：排除自环（实体连自己）。
5. `RETURN head, rel, tail`：返回成三元组，再在 Python 里去重（`seen` 集合）。

返回格式统一是 `[{"head", "rel", "tail"}, ...]`，方便和向量结果一起融合。

---

## 五、它在整条链路里的位置

```
导入文档：
   RagService.ingest_text ─► LLM.extract_graph(抽三元组)
                          └► Neo4jStore.add_knowledge(写图)   ◄── 本篇

问答检索（neo4j_node）：
   用户问题 ─► LLM.extract_keywords(抽关键词)
            └► Neo4jStore.search(一跳遍历) ─► [{head,rel,tail}]  ◄── 本篇
                            └► 和 Qdrant 结果一起交给 merge_node 融合
```

注意：Neo4j 这一路**不带分数**（关键词精确命中，不像向量有相似度分），所以在融合时不参与 `score_threshold` 过滤（见 [12 篇 merge_results](12-rag_service-RAG编排与混合检索.md)）。

---

## 修改建议

- **想改成多跳检索**（找朋友的朋友）：把 `MATCH (a)-[r]-(b)` 改成 `MATCH (a)-[*1..2]-(b)`，但注意多跳会指数级变慢，配合 `limit` 控制。
- **想提高召回**：放宽 `search` 的 `CONTAINS` 匹配，或先对关键词做归一化（去停用词）。
- **关系类型一定要先 `sanitize_relation_type`**：任何把外部字符串拼进 Cypher 关系类型的地方，都必须清洗，否则是安全漏洞。

---

## 学习检查

1. 为什么写入用 `MERGE` 而不是 `CREATE`？如果用 `CREATE` 会出什么问题？
2. 关系类型 `{rel_type}` 为什么不能像 `$head` 那样用参数传？项目怎么保证它安全？
3. 删除文档 A 时，A 和 B 共同贡献的关系会被删吗？为什么？
4. `search` 是「精确匹配」还是「模糊匹配」？`CONTAINS` 起了什么作用？
5. Neo4j 结果为什么没有 score 字段？这和向量库结果有什么不同？
6. `session.execute_write(...)` 比直接 `tx.run` 多保证了什么？
