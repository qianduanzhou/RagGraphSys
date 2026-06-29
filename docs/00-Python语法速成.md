# Python 语法速成（结合本项目）

> 这一篇是给 **Python 新手** 的速查手册。和一般语法书不同：下面每一个语法点，都标注了它在本项目 [backend/](../backend/) 里的真实出处。看完这一篇，你不再需要为「这一行 Python 是什么意思」卡住。
>
> 配套：先读本篇打底，再按 [学习路线](学习路线.md) 读 01~08。

## 为什么要先学 Python 语法

本项目的后端完全用 Python 写。你会反复遇到这些写法：`Optional[str]`、`def f(x: List[Dict]) -> str`、`yield`、`async def`、`@timing`、`with ... as`、`raise ... from ...`。它们不是花哨技巧，而是这个项目的基本词汇。先认全它们，后面读代码会顺很多。

---

## 1. 虚拟环境、pip 与包

Python 项目习惯把依赖装在一个隔离的「虚拟环境」里，避免污染系统。本项目后端依赖装在 `backend\.venv\`。

```powershell
python -m venv .venv            # 创建虚拟环境
.\.venv\Scripts\Activate.ps1    # 激活（PowerShell）
pip install -r requirements.txt # 按清单装依赖
```

- **pip**：Python 的包管理器，`pip install langchain` 就是装一个第三方库。
- **包与 `__init__.py`**：一个文件夹里放一个 `__init__.py`，它就成了「包」（package），可以被跨文件导入。本项目每个子目录（`core/`、`services/`、`rag/`、`multiagent/`）都有 `__init__.py`。所以能写 `from services.llm_service import LLMService`。

---

## 2. 类型提示（Type Hints）

类型提示写在变量、参数、返回值后面，**不强制运行**，只是给人读、给编辑器/IDE 提示用的。本项目大量使用，先认全这些「容器类型」：

| 写法 | 含义 | 项目示例 |
|------|------|----------|
| `str` / `int` / `float` / `bool` | 字符串/整数/浮点/布尔 | `name: str` |
| `List[X]` | 存 X 的列表 | [embedding_service.py](../backend/services/embedding_service.py) `def embed_batch(texts: List[str]) -> List[List[float]]` |
| `Dict[str, Any]` | 字典，键是 str、值任意类型 | [api.py](../backend/api.py) `Dict[str, Any]` |
| `Optional[X]` | 可以是 X，也可以是 `None` | [llm_service.py](../backend/services/llm_service.py) `def __init__(..., llm: Optional[BaseChatModel] = None)` |
| `Tuple[str, str, str]` | 固定长度的元组，这里是三个 str | [neo4j_store.py](../backend/rag/neo4j_store.py) `triples: Sequence[Tuple[str, str, str]]` |
| `Sequence[X]` | 序列（列表/元组都算），只读不关心具体类型 | 同上 |
| `Literal["rag", "multi"]` | 只能取这几个字面值 | [api.py](../backend/api.py) `mode: Literal["rag", "multi"] = "rag"` |
| `Callable[..., T]` | 一个可调用对象（函数），返回 T | [utils.py](../backend/core/utils.py) `def timing(func: Callable[..., T])` |
| `TypeVar("T")` | 泛型占位符，表示「某种类型，前后保持一致」 | [utils.py](../backend/core/utils.py) `T = TypeVar("T")` |

> 关键心态：**类型提示不参与运行**。写错类型，程序照样能跑（除非用 mypy 之类工具检查）。它是「给读代码的人看的注释」。

---

## 3. TypedDict —— 带「字段类型」的字典

`TypedDict` 定义一个「规定了有哪些键、每个键什么类型」的字典。本项目用它定义 **LangGraph 的状态**：

```python
# nodes.py 里
class GraphState(TypedDict, total=False):
    question: str
    history: list[dict]
    qdrant_results: list
    ...
```

- `total=False` 表示这些键**都是可选的**——节点只返回自己关心的那几个键，LangGraph 会自动把它们合并进状态。
- `total=True`（默认）则要求所有键都必须出现。

详见 [04-nodes-LangGraph状态与节点](04-nodes-LangGraph状态与节点.md)。

---

## 4. Pydantic 的 BaseModel —— 带校验的数据模型

[api.py](../backend/api.py) 里每一个 HTTP 请求/响应的数据结构都是一个继承 `BaseModel` 的类。它和 `TypedDict` 像表亲，但更强：能自动校验、给默认值、自动生成文档。

```python
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)       # 必填，至少 1 个字
    history: Optional[List[ChatMessage]] = Field(default_factory=list)
    mode: Literal["rag", "multi"] = "rag"         # 默认 rag
```

- `Field(..., min_length=1)`：`...` 表示「必填」，`min_length=1` 表示至少一个字符。校验不通过 FastAPI 会自动返回 422 错误。
- `default_factory=list`：默认值用一个「新建空列表」，避免所有请求共享同一个列表（Python 里可变默认值的经典坑）。

---

## 5. f-string —— 把变量嵌入字符串

```python
logger.info("Merged %d triples into Neo4j (source=%s)", len(triples), source)  # % 占位
parts.append(f"[V{i}] (score={hit.get('score', 0):.3f}, src={hit.get('source')}) {hit['text']}")  # f-string
```

- `f"...{变量}..."`：花括号里直接放表达式，最常用。
- `{hit.get('score', 0):.3f}`：冒号后是**格式说明**，`.3f` 表示保留 3 位小数。
- 老写法 `"%d ... %s" % (a, b)` 或 `"...".format(...)` 在项目里也还看得到，认识即可。

---

## 6. 列表推导、字典推导、三元表达式

一行写完一个循环，本项目到处都是：

```python
# 列表推导：过滤掉空的实体（neo4j_store.py）
entities = [e for e in entities if e]

# 列表推导 + enumerate（rag_service.py）
metadatas = [{"source": source, "chunk_index": i} for i, c in enumerate(chunks)]

# 三元表达式：满足条件取左，否则取右
removed = (before - after) if (before >= 0 and after >= 0) else 0
```

- `[表达式 for 变量 in 可迭代 if 条件]` 是列表推导的固定骨架。
- `A if 条件 else B` 是三元，注意顺序：先条件后两个分支。

---

## 7. 生成器 yield —— 一段一段产出

普通函数 `return` 一次就结束；生成器函数用 `yield`，可以**一次吐一个值**，调用方边拿边用。本项目流式对话靠它：

```python
# llm_service.py
def chat_stream(self, messages, ...) -> Iterator[str]:
    model = self._model_with(...)
    for chunk in model.stream(_to_lc_messages(messages)):
        content = chunk.content
        if content:
            yield content          # 每来一段就吐一段，不攒着
```

- 返回类型标 `Iterator[str]`，说明这是个生成器。
- 调用方用 `for x in svc.chat_stream(...)` 或 SSE 端点逐段转发，实现「打字机」效果。

---

## 8. async / await —— 异步

异步函数用 `async def` 定义，里面用 `await` 等待慢操作（网络、文件）。本项目文件上传、SSE 流式接口、FastAPI 的生命周期都是异步的：

```python
# api.py
async def ingest_file(file: UploadFile = File(...), request: Request = None):
    raw = await file.read()        # await 一个慢操作
    ...
```

- `await` 只能用在 `async def` 里。
- `async def` 定义的函数返回的不是结果，而是一个「协程」对象，必须被 `await` 或放进事件循环跑。
- 不用死记原理：看到 `async def` 就知道「这是为并发设计的慢操作函数」，看到 `await` 就知道「这里在等」。

---

## 9. with —— 上下文管理器（自动收尾）

`with` 保证资源用完自动关闭（哪怕中途报错）。本项目连 FastAPI 启动都包在一个 `with` 里：

```python
# Neo4j：每次操作开一个会话，自动关闭
with self.driver.session() as session:
    session.execute_write(self._merge_triples, triples, source)

# zip：打开压缩包，自动关闭
with zf:
    for info in zf.infolist():
        ...
```

- `with X as y:` 的意思是「把 X 交给 y，用完自动调 X 的收尾逻辑」。
- [main.py](../backend/main.py) 的 `lifespan` 是 `@asynccontextmanager` 装饰的 `async with`，负责应用启动时建服务、停止时关连接。

---

## 10. 装饰器（decorator）

装饰器是「给函数套一层壳」的语法，写在 `@` 后面。本项目两个典型：

```python
# utils.py：timing 装饰器，记录函数耗时
@functools.wraps(func)
def wrapper(*args, **kwargs):
    start = time.perf_counter()
    try:
        return func(*args, **kwargs)
    finally:
        logger.info("%s executed in %.3fs", func.__qualname__, time.perf_counter() - start)
return wrapper
```

```python
# 用法（embedding_service.py）
@timing
def embed(self, text: str) -> List[float]:
    return self.embeddings.embed_query(text)
```

- `@timing` 让 `embed` 调用时自动走 `wrapper`：先记时、再调真函数、再记时。
- `@functools.wraps(func)` 让包装后的函数还「看起来像原函数」（保留名字、文档）。
- `@lru_cache`（[config.py](../backend/core/config.py) 的 `get_settings`）让函数只算一次，之后返回缓存——所以配置是「单例」。

`*args, **kwargs` 表示「接受任意个位置参数和关键字参数」，常用于装饰器，把参数原封不动转给被装饰函数。

---

## 11. 异常处理 try / except / raise ... from

```python
# api.py
try:
    stats = rag.ingest_text(payload.text, source=payload.source)
except Exception as exc:
    logger.exception("ingest failed")
    raise HTTPException(status_code=500, detail=f"ingest failed: {exc}") from exc
```

- `try` 块放可能出错的代码，`except Exception as exc` 捕获异常并取名为 `exc`。
- `raise ... from exc` 表示「抛一个新异常，并说明它由 `exc` 引起」，保留原始栈。
- `except (UnicodeEncodeError, LookupError):` 可以一次捕获多种异常。
- 项目很多 `except Exception as exc:  # noqa: BLE001` —— 注释 `# noqa` 是告诉代码检查工具「这里故意不细分异常，别报」。

---

## 12. pathlib.Path —— 路径对象

本项目不用字符串拼路径，而用 `pathlib.Path`，跨平台、更安全：

```python
# config.py
BASE_DIR: Path = Path(__file__).resolve().parent.parent   # 本文件上两级 = backend/
load_dotenv(BASE_DIR / ".env")                              # / 是路径拼接
```

- `Path(__file__)`：当前文件的路径。
- `.resolve()`：转成绝对路径。
- `/ ".env"`：`Path` 重载了除号，用来拼接路径（比字符串 `+ "\\"` 优雅）。
- `.read_text(encoding="utf-8")` / `.read_bytes()`：直接读文件内容。

---

## 13. @property —— 把方法当属性用

```python
# embedding_service.py
@property
def dimension(self) -> int:
    return self.settings.embedding_dimension

# config.py
@property
def cors_origins_list(self) -> list[str]:
    return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
```

- 加了 `@property`，调用时不用加括号：写 `emb.dimension` 而不是 `emb.dimension()`。
- 适合「看起来像属性，但需要一点计算」的场景。

---

## 14. from __future__ import annotations

你会发现几乎每个 `.py` 顶部都有：

```python
from __future__ import annotations
```

它的作用：让类型提示「延迟求值」（当成字符串处理）。好处是写 `list[dict]` 这种新语法、或类内部引用自己时不会报错。**新手可以无视它**，把它当成固定开头就行。

---

## 学习检查

1. `Optional[List[ChatMessage]]` 是什么意思？和 `List[ChatMessage]` 区别在哪？
2. `@timing` 装饰器是怎么做到「不修改原函数就能加计时」的？提示：看 `wrapper` 和 `return wrapper`。
3. 一个函数里出现 `yield`，它的返回值类型应该标成什么？调用方怎么拿到它的值？
4. `with self.driver.session() as session:` 这行，`with` 帮你省掉了哪一步手动操作？
5. `Field(..., min_length=1)` 里的 `...` 代表什么？
6. `from __future__ import annotations` 要不要删掉？为什么？

> 能答出大部分，就具备读本项目所有后端代码的 Python 基础了。读代码时再回头查这一篇即可。
