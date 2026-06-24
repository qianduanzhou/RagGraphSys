# embedding_service.py 学习笔记

> 配套源文件：`../backend/services/embedding_service.py`
> 学习阶段：**第 2 步 · 向量化（Embedding）**（先读 01-llm_service-对话模型入门.md 再读这个，它更简单）

## 文件作用

「向量化」就是把一段文字变成一串数字（向量），让计算机能算「两段文字有多像」。这是 RAG（检索增强生成）能工作的基础——得先把文档和问题都变成向量，才能比较相似度。

这个文件用 LangChain 的 `OpenAIEmbeddings` 包装出一个 `EmbeddingService` 类，对外只给两个方法：

- `embed(text)` —— 一段文字 -> 一个向量
- `embed_batch(texts)` —— 多段文字 -> 多个向量（按顺序对应）

它和 `llm_service.py` 是一对：`ChatOpenAI` 负责「说人话」，`OpenAIEmbeddings` 负责「变数字」。

## 核心知识点

### 1. OpenAIEmbeddings —— 向量化对象

```python
from langchain_openai import OpenAIEmbeddings

self.embeddings = OpenAIEmbeddings(
    model=settings.embedding_model,   # 如 "embedding-3"
    api_key=settings.llm_api_key,     # 和 ChatOpenAI 用同一个 key
    base_url=settings.llm_base_url,   # 和 ChatOpenAI 用同一个地址
)
```

注意：它和 `ChatOpenAI` 用**完全相同的** `base_url` 和 `api_key`，因为智谱等厂商的「对话接口」和「向量化接口」在同一套服务下。项目特意直接以参数形式传入，避免往进程级环境变量写凭证（更安全、更可控）。

### 2. 两个方法：embed_query vs embed_documents

LangChain 的 Embeddings 对象约定了两个方法，本项目各包了一层：

```python
def embed(self, text):
    return self.embeddings.embed_query(text)          # 单条（常用于"问题"）

def embed_batch(self, texts):
    return self.embeddings.embed_documents(texts)     # 批量（常用于"文档"）
```

为什么要分两个名字？语义上有分工：
- `embed_query` —— 把用户的**问题**向量化；
- `embed_documents` —— 把**入库的文档**向量化。

有些模型对「查询」和「文档」会用略微不同的处理（不对称检索），所以分开。即使你用的模型不区分，养成这个习惯也是好的。

> 小白要点：向量是一串浮点数，本项目维度是 2048（`EMBEDDING_DIMENSION`）。两段文字的向量越接近（夹角越小），语义越相似。检索时就是用这个原理找最像的几段。

### 3. 依赖注入与维度属性

```python
def __init__(self, settings, embeddings=None):
    if embeddings is not None:
        self.embeddings = embeddings   # 测试时注入假对象
        return
    self.embeddings = OpenAIEmbeddings(...)   # 生产用真实对象

@property
def dimension(self):
    return self.settings.embedding_dimension
```

构造函数 `embeddings=None` 这个写法和 `llm_service.py` 一样：生产用真实的，测试注入假的（不联网）。这是本项目的通用模式，记住它。

## 重要对象、函数或配置项

| 名称 | 作用 |
|------|------|
| `OpenAIEmbeddings` | LangChain 向量化对象，接 OpenAI 兼容接口 |
| `Embeddings`（基类） | 所有向量化模型的基类，用于类型注解 |
| `embed_query(text)` | 单条文本 -> 向量 |
| `embed_documents(list)` | 批量文本 -> 向量列表 |
| `@timing` | 记录耗时（见 utils.py） |

配置项：`embedding_model` `embedding_dimension`（向量维度，如 2048）。注意：**维度必须和向量库 Qdrant 里建的集合维度一致**，否则写不进去。

## 关键流程

向量化在两个地方被用到：

- **入库时**（`rag_service.py`）：`rag.ingest_text` 把文档切块后，用 `embed_batch` 批量向量化，写入 Qdrant。
- **检索时**（`qdrant_store.py`）：用 `embed` 把用户问题向量化，再拿这个向量去 Qdrant 里找最像的文档块。

数据流：`文本 -> embed -> 向量(2048维) -> Qdrant 比较 -> 返回最像的几段`。

## 修改建议

- 换 embedding 模型：改 `.env` 的 `EMBEDDING_MODEL`；若新模型维度不同，**必须同时改 `EMBEDDING_DIMENSION` 并清空/重建 Qdrant 集合**，否则新旧向量维度不一致会冲突。
- 批量向量化很耗 token，`embed_batch` 适合分批调用；超大文档注意控制每批数量。

## 学习检查

1. 「向量化」在 RAG 里起什么作用？没有它，还能做语义检索吗？
2. 为什么 `embed_query` 和 `embed_documents` 要分两个方法？
3. 换了一个维度是 1024 的 embedding 模型，只改 `EMBEDDING_MODEL` 够吗？还差什么？
4. 这个文件的依赖注入写法和 `llm_service.py` 哪里一样？这种写法对测试有什么好处？

> 下一步看 `03-utils-文本切分.md`，理解文本怎么切分（切分后才会向量化）。
