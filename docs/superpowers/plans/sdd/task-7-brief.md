## Task 7: 前端类型 + 客户端

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`

**Interfaces:**
- Consumes: 后端新 SSE 契约（Task 6）
- Produces: `ChatMode` 类型、`MULTI_AGENT_PIPELINE`、扩展的 `SourceRef`/`NodeUpdate`/`ChatMessage`/`HealthResponse`；`chat`/`chatStream` 携带 `mode`。Task 8/9 的组件消费这些。

- [ ] **Step 1: 扩展 types.ts**

打开 `frontend/src/types.ts`。

(a) `SourceRef` 的 `type` 扩展（替换第 3-8 行）：
```typescript
export interface SourceRef {
  type: "qdrant" | "neo4j" | "web";
  content: string;
  score?: number;
  source?: string;
  // web 来源额外字段
  title?: string;
  url?: string;
}
```

(b) 在 `PIPELINE` 常量（第 19-25 行）**之后**新增模式类型与多智能体管线：
```typescript
export type ChatMode = "rag" | "multi";

/** 多智能体模式管线，key 与多智能体 LangGraph 节点名一一对应。 */
export const MULTI_AGENT_PIPELINE: ReadonlyArray<{ key: string; label: string }> = [
  { key: "dispatch_node", label: "调度" },
  { key: "rag_agent_node", label: "RAG智能体" },
  { key: "web_agent_node", label: "联网智能体" },
  { key: "integration_node", label: "整合" },
];
```

(c) `NodeUpdate` 增加 `answer` 与 `used_web`（替换第 27-35 行）：
```typescript
export interface NodeUpdate {
  needs_rag?: boolean;
  used_rag?: boolean;
  used_web?: boolean;
  hits?: number;
  sources?: SourceRef[];
  answer?: string; // 多智能体下两个 agent 的原始回答文本
  iterations?: number;
  passed?: boolean;
  feedback?: string;
}
```

(d) `ChatMessage` 增加 `mode` 与两个子答案（替换第 37-46 行）：
```typescript
export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  sources?: SourceRef[];
  usedRag?: boolean;
  usedWeb?: boolean;
  error?: boolean;
  streaming?: boolean;
  mode?: ChatMode;
  steps?: PipelineStep[];
  ragAgentAnswer?: string; // 多智能体：RAG 智能体原始回答（折叠面板）
  webAgentAnswer?: string; // 多智能体：联网智能体原始回答（折叠面板）
}
```

(e) `HealthResponse` 增加 `web_search`（替换第 97-105 行）：
```typescript
export interface HealthResponse {
  status: string;
  qdrant: boolean;
  neo4j: boolean;
  web_search: boolean;
  counts: {
    qdrant_points?: number;
    neo4j_entities?: number;
  };
}
```

- [ ] **Step 2: 扩展 client.ts 携带 mode**

打开 `frontend/src/api/client.ts`。

(a) 顶部 import 增加 `ChatMode`：
```typescript
import type {
  BatchIngestResponse,
  ChatHistoryItem,
  ChatMode,
  ChatResponse,
  DeleteDocResponse,
  HealthResponse,
  IngestResponse,
  NodeUpdate,
  SourceRef,
  StreamCallbacks,
  UploadedDoc,
} from "../types";
```

(b) `chat` 增加 `mode` 参数（替换第 25-36 行）：
```typescript
export async function chat(
  message: string,
  history: ChatHistoryItem[],
  mode: ChatMode = "rag"
): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history, mode }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
```

(c) `chatStream` 增加 `mode` 参数：把签名（第 109-113 行）
```typescript
export async function chatStream(
  message: string,
  history: ChatHistoryItem[],
  cb: StreamCallbacks
): Promise<void> {
```
改为：
```typescript
export async function chatStream(
  message: string,
  history: ChatHistoryItem[],
  cb: StreamCallbacks,
  mode: ChatMode = "rag"
): Promise<void> {
```
并把其内 `body: JSON.stringify({ message, history })` 改为 `body: JSON.stringify({ message, history, mode })`。

- [ ] **Step 3: 验收检查点——类型检查 + 构建**

Run:
```powershell
npm --prefix D:\project\customer\AI\RagGraphSys\frontend run build
```
Expected: `tsc -b` 与 `vite build` 均无错误。（此阶段 App.tsx 还没用到 `mode`，但类型导出应编译通过；若 App.tsx 报「chatStream 调用签名不匹配」，Task 8 会一并修复。）

---

