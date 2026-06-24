# utils.py 学习笔记

> 配套源文件：`../backend/core/utils.py`
> 学习阶段：**第 3 步 · 工具函数与文本切分**（LangChain 的文本切分器在这里用到）

## 文件作用

这是项目的「公共工具箱」，放了几个到处都会用的小函数：

- `split_text()` —— 把长文档切成带重叠的小块（**用 LangChain 的切分器**）
- `extract_json()` —— 从模型输出里提取 JSON
- `sanitize_relation_type()` —— 把自由文本的关系标签清洗成合法的 Neo4j 关系类型
- `truncate()` —— 截断超长文本（只用于日志显示）
- `timing()` —— 装饰器，记录函数耗时

对学 LangChain 来说，最重要的是 `split_text()`。

## 核心知识点

### 1. RecursiveCharacterTextSplitter —— 递归文本切分器

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(
    chunk_size=chunk_size,         # 每块最大字符数，如 500
    chunk_overlap=chunk_overlap,   # 相邻块的重叠字符数，如 80
    separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""],
)
return [doc.page_content for doc in splitter.create_documents([text])]
```

这是 RAG 入库前最关键的一步。为什么要切？因为大模型上下文有限，而且一篇长文档整段塞进去检索效果差。把文档切成小块（chunk），每块单独向量化、单独检索，命中率更高。

**三个参数怎么理解：**

- `chunk_size`：每块最多多少字符。太大塞不下、检索不精准；太小信息不完整。500 是个常用起点。
- `chunk_overlap`：相邻两块重叠多少字符。**这是新手最容易忽略但极重要的参数**：如果切在一个句子中间，重叠能让上下文延续，避免把语义切断。
- `separators`：切分点的优先级表。

**separators 的「递归」是什么意思？** 这个名字来自它的工作方式：它**按顺序**尝试这些分隔符——

1. 先试着按段落切（`\n\n`），尽量不破坏段落；
2. 某块还是太长，就降级按换行（`\n`）切；
3. 还太长，就按中文句号（`。`）切；
4. 依此类推，最后才按空格、甚至按字符硬切。

`["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""]` 这个列表把**中文标点排在英文标点前面**，对中文文档友好，保证尽量切在中文句末而不是词中间。

### 2. create_documents 的返回值

```python
docs = splitter.create_documents([text])   # 注意传的是列表 [text]
return [doc.page_content for doc in docs]
```

`create_documents` 返回的是 LangChain 的 `Document` 对象列表，每个对象有 `.page_content`（文本内容）和 `.metadata`（元数据）。本项目只取文本，所以用列表推导取出 `.page_content`。

> 小白要点：LangChain 很多地方返回 `Document` 对象。记住 `.page_content` 取正文、`.metadata` 取元数据，这是个反复出现的套路。

### 3. extract_json —— 从模型输出里抢救 JSON

```python
def extract_json(text):
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)   # 去掉开头的 ```json
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)          # 去掉结尾的 ```
    try:
        return json.loads(cleaned)                        # 直接解析
    except Exception:
        pass
    match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)  # 退而求其次，正则抓 JSON
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            return None
    return None
```

为什么需要它？因为让 LLM「只输出 JSON」，它经常不听话——会多嘴加 ```` ```json ```` 代码块标记、或者前后多一句解释。`extract_json` 先把这些干扰去掉再解析，实在不行用正则把 `{...}` 或 `[...]` 抠出来。这是配合 `llm_service.py` 的 `extract_graph`/`extract_keywords` 的关键工具。

### 4. sanitize_relation_type —— 防注入的清洗

```python
def sanitize_relation_type(rel, fallback="RELATES_TO"):
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", rel or "").strip("_").upper()
    return cleaned[:48] or fallback
```

Neo4j 的关系类型会被**直接拼进 Cypher 语句**（无法参数化），所以只能含字母数字下划线。这个函数把中文、空格、特殊符号全换成 `_`，再截断到 48 字符。这是防数据库注入/报错的安全措施。如果你以后要改图谱入库逻辑，别忘了用这个清洗。

### 5. timing 装饰器

```python
def timing(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            logger.info("%s executed in %.3fs", func.__qualname__, time.perf_counter() - start)
    return wrapper
```

`llm_service.py` 的方法都加了 `@timing`，于是每次调模型都会自动打日志记录耗时。`@functools.wraps(func)` 是装饰器的礼貌写法——让包装后的函数保留原来的名字，日志里才显示对的名字。

## 关键流程

入库时的切分链路（在 `rag_service.py` 里）：

```
原始长文档
   |  split_text(text, chunk_size=500, chunk_overlap=80)
   v
[块1, 块2, 块3, ...]   <- 带重叠的文本块
   |  embedding_service.embed_batch()  (见 02-embedding_service-向量化.md)
   v
[向量1, 向量2, ...]
   |  qdrant_store.upsert()
   v
存入 Qdrant 向量库
```

## 重要对象、函数或配置项

| 名称 | 作用 |
|------|------|
| `RecursiveCharacterTextSplitter` | LangChain 递归切分器（按分隔符优先级逐级降级） |
| `create_documents([text])` | 切分文本，返回 Document 列表 |
| `chunk_size` / `chunk_overlap` | 块大小 / 重叠（来自 `.env` 的 `CHUNK_SIZE`/`CHUNK_OVERLAP`） |
| `extract_json()` | 从 LLM 输出提取 JSON |
| `sanitize_relation_type()` | 清洗关系标签（防注入） |
| `timing()` | 耗时记录装饰器 |

## 修改建议

- 调检索效果时，`chunk_size` 和 `chunk_overlap` 是重要旋钮：块太大检索不精、太小信息碎片化；重叠太小会切断语义，通常取 `chunk_size` 的 10%~20%。
- 换语言/文档类型，调整 `separators` 顺序（如英文文档可把 `.` 前移）。
- `extract_json` 失败返回 `None`，调用方（`llm_service.py`）已据此降级，不要单独改它的返回约定。

## 学习检查

1. 为什么要把文档切分？整篇直接向量化有什么坏处？
2. `chunk_overlap` 起什么作用？设成 0 会怎样？
3. `separators` 列表的顺序为什么重要？「递归」体现在哪？
4. `create_documents` 返回的对象，怎么取到正文文本？
5. 为什么关系类型要先过 `sanitize_relation_type` 才能写进 Neo4j？
6. `@timing` 装饰器是怎么做到「不改变函数行为、又加上耗时日志」的？

> 读到这里，你已经掌握本项目的全部 LangChain 用法（模型对话 + 向量化 + 文本切分）。下一步进入 **LangGraph** 的世界：先看 `04-nodes-LangGraph状态与节点.md`（状态与节点）。
