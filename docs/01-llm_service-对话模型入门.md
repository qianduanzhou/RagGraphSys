# llm_service.py 学习笔记

> 配套源文件：`../backend/services/llm_service.py`
> 学习阶段：**第 1 步 · LangChain 对话模型入门**（整个项目的起点，建议从这里读）

## 文件作用

这是整个后端**唯一和大模型（LLM）打交道的层**。它把 LangChain 的 `ChatOpenAI` 包装成一个 `LLMService` 类，对外暴露几个好用的方法：

- `chat()` —— 一次性问、一次性答（非流式）
- `chat_stream()` —— 一个字一个字往外吐（流式）
- `extract_keywords()` —— 从问题里抽关键词，给 Neo4j 图谱检索用
- `extract_graph()` —— 从文本里抽实体关系三元组，给知识图谱入库
- `reflect()` —— 让模型审核自己上一条回答够不够好

为什么要包一层？因为图编排代码（`nodes.py`）只想调用「生成回答」「抽关键词」这种**业务方法**，不想关心 HTTP、重试、token 拼接这些**技术细节**。这一层就是隔离带——也是后面节点代码能保持干净的的关键。

## 核心知识点

### 1. ChatOpenAI —— LangChain 的对话模型对象

```python
from langchain_openai import ChatOpenAI

self.llm = ChatOpenAI(
    model=settings.llm_model,           # 用哪个模型，如 "glm-5.2"
    api_key=settings.llm_api_key,       # 你的密钥
    base_url=settings.llm_base_url,     # 接口地址（关键！指向任意 OpenAI 兼容服务）
    temperature=settings.llm_temperature,
    max_tokens=settings.llm_max_tokens,
    timeout=settings.llm_request_timeout,
)
```

这是整个 LangChain 学习里最重要的一行：`base_url + api_key`。只要某厂商提供「OpenAI 兼容接口」（请求/返回格式跟 OpenAI 一样），你**不用换 SDK**，改这两个参数就能接智谱、DeepSeek、本地 vLLM 等任何服务。这也是本项目能任意替换模型的原因。

`temperature`（0~2）控制随机性：0 最确定、越高越发散；`max_tokens` 限制回答长度。

### 2. 消息对象 SystemMessage / HumanMessage / AIMessage

大模型对话是「有角色」的，LangChain 用三个类表示：

```python
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

SystemMessage(content="你是一个助手")      # 设定人设/规则
HumanMessage(content="你好")               # 用户说的话
AIMessage(content="你好！有什么可以帮你")  # 模型之前的回答（历史）
```

文件里的 `_to_lc_messages()` 就是把「字典列表」翻译成 LangChain 消息对象：

```python
def _to_lc_messages(messages):
    for m in messages:
        if role == "system":    result.append(SystemMessage(content=content))
        elif role == "assistant": result.append(AIMessage(content=content))
        else:                   result.append(HumanMessage(content=content))
```

> 小白要点：`{role, content}` 字典是人类最好读的格式；LangChain 消息对象是库要的格式。这个函数是两者之间的「翻译官」。后续代码统一用字典，只在最后一刻翻译。

### 3. .invoke() 同步调用 vs .stream() 流式调用

```python
# 非流式：等模型全部说完，一次性拿到完整回答
resp = model.invoke(messages)
return resp.content      # resp 是 AIMessage 对象，.content 才是文本！

# 流式：模型说一个字返回一个 chunk，循环里逐个拿
for chunk in model.stream(messages):
    content = chunk.content
```

注意 `invoke` 返回的是**对象**（`AIMessage`），不是字符串！要取 `.content` 才是文字。这是新手最容易踩的坑。

### 4. .bind() —— 绑定参数，不改原对象

```python
def _model_with(self, temperature, max_tokens):
    kwargs = {}
    if temperature is not None: kwargs["temperature"] = temperature
    if max_tokens is not None:  kwargs["max_tokens"] = max_tokens
    return self.llm.bind(**kwargs) if kwargs else self.llm
```

`.bind()` 返回一个**带固定参数的新对象**，原来的 `self.llm` 不受影响。好处：同一个底层模型，抽关键词时用「低温度 0.0（求准）」，聊天时用默认温度，互不干扰。

## 代码结构

```
_to_lc_messages()        # 翻译官：字典列表 -> LangChain 消息对象
class LLMService:
    __init__             # 创建 ChatOpenAI（或接收测试注入的假对象）
    _model_with()        # 临时绑定 temperature/max_tokens
    chat()               # 非流式对话（被 @timing 装饰）
    chat_stream()        # 流式对话，逐 token yield
    extract_keywords()   # 抽关键词（给图谱检索）
    extract_graph()      # 抽三元组（给图谱入库）
    reflect()            # 审核答案质量
```

## 关键流程

### 流式对话 chat_stream 是怎么工作的（流式的灵魂）

```python
def chat_stream(self, messages, ...):
    model = self._model_with(temperature, max_tokens)
    for chunk in model.stream(_to_lc_messages(messages)):
        content = chunk.content
        if not content:
            continue
        if isinstance(content, str):
            yield content          # 普通文本，直接吐出
        else:
            # 多模态时 content 可能是 list[dict]，只取文字部分
            yield "".join(p.get("text","") for p in content)
```

`yield` 是 Python 的「生成器」语法：函数遇到 `yield` 就**暂停并返回一个值**，下次被调用时从暂停处继续。所以调用方 `for token in chat_stream(...)` 能一个字一个字收到。**这一句是流式的灵魂**，记牢它。

还有个细节：`content` 有时是字符串、有时是 list（多模态/分块），所以做了 `isinstance` 判断归一，新手容易忘这种容错。

### extract_graph / extract_keywords 的套路

两个方法用同一模式——**让模型输出 JSON，再解析**：

1. 写 system 提示词，规定「只输出 JSON，不要额外文字」；
2. 用低 `temperature`（0.0~0.1）保证稳定；
3. 拿到文本回答后，用 `extract_json()`（见 `core/utils.py`）把 JSON 字符串还原成 Python 对象；
4. 包一层 `try/except`，模型没按规定输出也不崩溃，降级返回空。

> 小白要点：「让 LLM 输出结构化数据」是 LangChain 的常见任务。这里没用复杂的 Output Parser，而是手写提示词 + JSON 提取，依赖少、可控。学到这里你会理解为什么需要 `temperature=0`（求稳定可解析）。

## 重要对象、函数或配置项

| 名称 | 作用 |
|------|------|
| `ChatOpenAI` | LangChain 对话模型，接 OpenAI 兼容接口 |
| `BaseChatModel` | 所有对话模型的基类，用于类型注解/依赖注入 |
| `SystemMessage/HumanMessage/AIMessage` | 三种角色的消息对象 |
| `.invoke()` / `.stream()` | 同步/流式调用 |
| `.bind(**kwargs)` | 绑定参数得到新对象 |
| `@timing` | 装饰器，记录函数耗时（见 utils.py） |
| `extract_json()` | 从文本提取 JSON（见 utils.py） |

配置项都来自 `.env`：`llm_model` `llm_api_key` `llm_base_url` `llm_temperature` `llm_max_tokens` `llm_request_timeout`。改模型只动 `.env`，代码不用改。

## 运行方式或使用方式

这个文件本身不直接运行。它被 `main.py` 启动时实例化，挂到 `app.state.llm`，再传给图的节点。想单独测它，看 `../backend/tests/test_llm_service.py`——里面注入假模型（mock）来测，不联网、不花钱。

调用链：`main.py` 实例化 -> 传给 `GraphNodes`（`nodes.py`）-> 节点调用 `self.llm.chat()` 或 `self.llm.chat_stream()`。

## 修改建议

- 想换模型厂商：只改 `.env` 的 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`，**不要**在代码里硬编码。
- 想加新的「LLM 能力」（翻译、摘要等）：在 `LLMService` 里加方法，遵循「system 提示词 + 低温度 + JSON 提取 + try/except」套路，别把 LLM 调用散到图代码里。
- 想加超时重试：在 `_model_with` 或 `chat` 层统一处理，保持业务方法干净。
- `reflect()` 失败时默认 `pass=True`（通过），是为了防止反思失败导致无限重试——改这行要谨慎。

## 学习检查

1. 为什么 `chat()` 返回值要写 `resp.content` 而不是直接返回 `resp`？
2. `_to_lc_messages` 解决了什么问题？不写它会怎样？
3. `.bind(temperature=0)` 之后，原来的 `self.llm` 温度变了吗？为什么这样设计？
4. `chat_stream` 里的 `yield` 和普通 `return` 有什么区别？为什么流式非它不可？
5. `extract_keywords` 为什么用 `temperature=0.0`，而聊天用 `0.6`？
6. 如果要接入一个**非** OpenAI 兼容的模型，需要改这个文件的哪些地方？

> 读懂后，下一步看 `02-embedding_service-向量化.md`（向量化，结构几乎一样，更简单），再看 `03-utils-文本切分.md`（文本切分），就能掌握本项目的全部 LangChain 用法了。
