# 前后端对接：前端怎么调后端、SSE 怎么消费（client.ts + App.tsx + types.ts）

> 这是学习的「出口」：把前面所有后端能力（FastAPI 接口、RAG、流式）和前端（React + TypeScript）连起来。读完后端接口清单（[13 篇](13-api-FastAPI框架详解.md)）再看这一篇，你会明白「一次问答，数据是怎么从浏览器流到后端、又流回来的」。

## 技术栈速览

- **React**：前端 UI 框架。用「组件 + 状态」组织界面。
- **TypeScript**：带类型的 JS，本项目的 `types.ts` 就是前后端契约。
- **Vite**：前端开发服务器，`npm run dev` 起在 `localhost:5173`。
- **lucide-react**：图标库（如 `SendHorizontal` 发送按钮）。
- 浏览器原生 `fetch` API：本项目**不用 axios**，直接用 `fetch` + 流式 `ReadableStream`。

---

## 一、目录与职责

| 文件 | 职责 |
|------|------|
| [src/types.ts](../frontend/src/types.ts) | 前后端**类型契约**（请求/响应/SSE 帧的字段定义） |
| [src/api/client.ts](../frontend/src/api/client.ts) | 所有 HTTP 调用 + SSE 解析，后端接口在这里一对一映射 |
| [src/App.tsx](../frontend/src/App.tsx) | 顶层组件，编排状态与回调 |
| [src/components/ChatWindow.tsx](../frontend/src/components/ChatWindow.tsx) | 聊天主区（输入框、消息列表、模式切换） |
| [src/components/Sidebar.tsx](../frontend/src/components/Sidebar.tsx) | 侧栏（上传、文档列表、健康状态） |
| [src/components/MessageBubble.tsx](../frontend/src/components/MessageBubble.tsx) | 单条消息气泡（含来源徽章、步进器） |
| [src/chat-history.ts](../frontend/src/chat-history.ts) | 历史消息处理 |

---

## 二、types.ts：前后端的「共同语言」

前后端用不同语言（Python/TS），但传的 JSON 必须字段一致。`types.ts` 就是这份契约，和后端 `api.py` 的 Pydantic 模型（[13 篇](13-api-FastAPI框架详解.md)）一一对应：

```ts
// 请求（对应后端 ChatRequest）
interface ChatHistoryItem { role: string; content: string; }
type ChatMode = "rag" | "multi";

// 响应（对应后端 ChatResponse）
interface ChatResponse {
  answer: string;
  sources: SourceRef[];
  used_rag: boolean;
  iterations: number;
}

// 来源（向量/图谱/联网三类，带 type 标签区分）
interface SourceRef {
  type: "qdrant" | "neo4j" | "web";
  content: string;
  score?: number;
  source?: string;
  title?: string;  // web 来源额外
  url?: string;
}
```

> **改接口时的纪律**：后端 Pydantic 改了字段，前端 `types.ts` 必须同步改，否则 `res.json()` 解析出来的数据对不上类型，运行时全是 undefined。

### SSE 回调契约

流式对话用回调对象，而不是 Promise（因为要持续接收多帧）：

```ts
interface StreamCallbacks {
  onMeta?: (sources, usedRag) => void;   // 元信息
  onNode?: (node, update) => void;       // 节点进度
  onDelta?: (text) => void;             // 字符增量（打字机）
  onDone?: () => void;                   // 完成
  onError?: (message) => void;          // 出错
}
```

### 管线常量

`PIPELINE` 和 `MULTI_AGENT_PIPELINE` 定义了「步进器」要展示哪些节点，**key 与后端 LangGraph 节点名一一对应**：

```ts
export const PIPELINE = [
  { key: "router_node",  label: "路由" },
  { key: "qdrant_node",  label: "向量" },
  { key: "neo4j_node",   label: "图谱" },
  { key: "merge_node",   label: "融合" },
  { key: "llm_node",     label: "生成" },
];
```

后端发来的 `node` 帧的 `node` 字段，正好能匹配这里某个 `key`，前端据此把对应步骤点亮成 done。这是前后端耦合最紧的一处。

---

## 三、client.ts：每个后端接口的镜像

这个文件几乎就是后端接口清单的 TS 版。以普通问答和上传为例：

### 普通 POST（JSON）

```ts
export async function chat(message, history, mode = "rag"): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history, mode }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
```

- `BASE = "/api"`，靠 Vite 代理转 `localhost:8000`（见文末「跨域/代理」）。
- `!res.ok` 时 `throw`，调用方用 try/catch 处理。

### 文件上传（FormData）

```ts
export async function ingestFiles(files: File[]): Promise<BatchIngestResponse> {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);   // 多文件逐个 append
  const res = await fetch(`${BASE}/ingest/files`, { method: "POST", body: form });
  // ... 错误处理
  return res.json();
}
```

- 文件上传用 `FormData`，**不要手动设 `Content-Type`**（浏览器会自动加带 boundary 的 multipart 头）。这和 JSON 接口（显式设 `application/json`）正相反。
- 对应后端 `files: List[UploadFile] = File(...)`（[13 篇](13-api-FastAPI框架详解.md)）。

### 统一错误解析

```ts
async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    return data.detail || data.message || `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}: ${res.statusText}`;
  }
}
```

后端的 `HTTPException(detail=...)` 会让响应体是 `{detail: "..."}`。这里读 `data.detail`，把后端的错误信息原样展示给用户。

---

## 四、SSE 消费：手写解析（重点）

浏览器没有现成的 SSE 客户端（`EventSource` 只支持 GET）。本项目用 `fetch` + `ReadableStream` 手动解析 POST 的 SSE 流。这是全篇最值得细读的部分：

```ts
const res = await fetch(`${BASE}/chat/stream`, { /* ... */ });
const reader = res.body?.getReader();      // 1. 拿到字节流读取器
const decoder = new TextDecoder();          // 2. 字节 → 文本
let buffer = "";                            // 3. 缓冲区（可能半帧）

for (;;) {
  const { value, done } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });

  let sep;
  while ((sep = buffer.indexOf("\n\n")) !== -1) {   // 4. SSE 帧以空行分隔
    const frame = buffer.slice(0, sep);
    buffer = buffer.slice(sep + 2);
    const dataLine = frame.split("\n").find(l => l.startsWith("data:"));
    if (!dataLine) continue;
    const obj = JSON.parse(dataLine.slice(5).trim()); // 5. 取 data: 后的 JSON
    switch (obj.type) {                               // 6. 按帧类型分发回调
      case "node":  cb.onNode?.(obj.node, obj.update);  break;
      case "delta": cb.onDelta?.(obj.text);             break;
      case "done":  cb.onDone?.();                      break;
      case "error": cb.onError?.(obj.message);          break;
    }
  }
}
```

几个关键理解点：

1. **为什么要缓冲区 `buffer`**：网络是一段段来的，一次 `read()` 可能只收到半帧，也可能收到一帧半。所以先攒进 `buffer`，再按 `\n\n`（SSE 帧分隔符）切成整帧。
2. **`\n\n` 是帧分隔**：后端每帧是 `data: {...}\n\n`（两个换行结尾，见 [13 篇](13-api-FastAPI框架详解.md)）。前端就是靠它切帧。
3. **`data:` 前缀**：SSE 协议规定每行数据以 `data: ` 开头，取 `slice(5)` 去掉前缀才是真正的 JSON。
4. **按 `type` 分发**：帧有 `node`（节点进度）、`delta`（字符增量）、`done`、`error` 四种，正好对应后端 `_summarize_update` 和 StreamWriter 写出的内容。

### 三种帧的用途

| 帧 | 内容 | 前端怎么用 |
|----|------|------------|
| `node` | `{node, update}` 节点完成事件 | 点亮对应步进器步骤（`PIPELINE` 匹配） |
| `delta` | `{text}` 一个字/词 | 追加到正在流式的消息内容（打字机效果） |
| `done` | — | 结束流式状态 |
| `error` | `{message}` | 把气泡标红、显示错误 |

> `delta` 对应后端 custom 流（字符增量），`node` 对应 updates 流（节点进度）——正好是 [06 篇](06-api-运行图同步流式.md) 讲的双流在前端的落地。

---

## 五、App.tsx：把回调接到状态上

`chatStream` 只负责收帧；帧到了怎么影响界面，在 [App.tsx](../frontend/src/App.tsx) 的 `handleSend` 里用 React 状态实现：

```ts
const patch = (updater) => setMessages(prev =>
  prev.map(m => (m.id === assistantId ? updater(m) : m)));   // 只改正在流式的那个气泡

await chatStream(text, history, {
  onNode: (node, update) => patch(m => {
    // 把对应步骤置 done、点亮下一步；多智能体并行点两个 agent
  }),
  onDelta: (delta) => patch(m => ({ ...m, content: m.content + delta })),  // 追加文字
  onDone:  () => patch(m => ({ ...m, streaming: false })),
  onError: (msg) => patch(m => ({ ...m, streaming: false, error: true, content: msg })),
}, mode);
```

- **`patch` 只更新那一条「正在流式」的助手消息**（用 `assistantId` 定位），不动其它消息。这是 React 列表更新的常见技巧（不可变更新）。
- `onDelta` 把增量 `+` 到内容上 → 打字机效果。
- `onNode` 推进步进器：单路图串行（当前 done→点亮下一个）；多智能体并行（dispatch 后同时点亮两个 agent）。

### history 的细节

```ts
const history = buildHistory(messages);
```

只把**历史**对话传给后端，本轮问题单独走 `message` 字段。注释里特别说明：**不把本轮 userMsg 算进 history**，否则大模型会收到两遍同一问题。这是真实踩过的坑。

---

## 六、ChatWindow：交互入口

[ChatWindow.tsx](../frontend/src/components/ChatWindow.tsx) 是纯展示组件（不自己发请求，靠 props 回调）：

- 模式切换（RAG / 多智能体）用两个按钮，多智能体按钮的 `disabled` 绑定 `webSearchAvailable`（来自后端 `/health`，见 [15 篇](15-web_search_service-联网搜索.md)）。
- `Enter` 发送、`Shift+Enter` 换行。
- 自动滚动到底（`useEffect` 监听 `messages`/`streaming`）。

---

## 七、跨域与 Vite 代理

后端跑 `localhost:8000`，前端跑 `localhost:5173`，不同源。两种处理方式本项目都用了：

1. **后端 CORS 中间件**（见 [09 篇](09-配置与启动-应用装配.md)）：`allow_origins` 放行前端地址。
2. **前端 `BASE="/api"` 相对路径**：开发时 Vite 把 `/api/*` 代理到后端（见 [vite.config.ts](../frontend/vite.config.ts)）。这样浏览器看到的是同源请求，绕开跨域。

> 真机联调时报 CORS 错误，多半是后端 `cors_origins` 没包含前端的地址，或没走代理。

---

## 一次问答的完整时序

```
1. 用户输入 → ChatWindow.submit → App.handleSend
2. App 往消息列表加 userMsg + 空的 assistantMsg(streaming=true)
3. client.chatStream POST /api/chat/stream
4. 后端 astream 双流：node 帧(进度) + delta 帧(文字) + done
5. 每帧 client 解析后调对应回调 → App.patch 更新那一条消息
   - onNode：点亮步进器；onDelta：追加文字(打字机)
6. onDone → 消息停止流式；onError → 气泡标红
7. finally：刷新 /health（更新数量/状态）
```

---

## 修改建议

- **加一个新接口**：后端加路由（[13 篇](13-api-FastAPI框架详解.md)）→ `types.ts` 加对应 interface → `client.ts` 加一个调用函数 → 组件里调用。
- **改节点结构**：改了 LangGraph 节点名，必须同步 `PIPELINE`/`MULTI_AGENT_PIPELINE` 的 key，否则步进器永远点不亮。
- **改字段**：前后端两边类型定义必须同步，养成改一处立刻改另一处的习惯。

---

## 学习检查

1. `types.ts` 和后端的 Pydantic 模型是什么关系？为什么必须同步？
2. 文件上传用 `FormData`，为什么不能像 JSON 接口那样手动设 `Content-Type`？
3. SSE 消费里，`buffer` 和 `\n\n` 分别起什么作用？为什么不能直接 `JSON.parse` 每次读到的东西？
4. `node` 帧和 `delta` 帧分别对应后端 astream 的哪两种 stream_mode？
5. `patch` 函数为什么要用 `map` 按 `assistantId` 定位，而不是直接改那条消息？
6. 多智能体按钮为什么会被禁用？这个状态从哪个接口来？
7. 后端报 415 错误时，前端 `parseError` 怎么拿到提示文字的？
