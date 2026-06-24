## Task 8: 前端模式切换 + App.tsx 编排

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/ChatWindow.tsx`

**Interfaces:**
- Consumes: `ChatMode`、`MULTI_AGENT_PIPELINE`、`chatStream(..., mode)`（Task 7）、`HealthResponse.web_search`
- Produces: 全局 `mode` 状态；按模式选管线；`onNode` 把两个 agent 的原始回答写入消息；`ChatWindow` 渲染模式切换控件并把 `mode` + `webSearchAvailable` 上报。

- [ ] **Step 1: 读 App.tsx 与 ChatWindow.tsx 现状**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -c "print('see files')"
```
（占位——实际用 Read 工具读 `frontend/src/App.tsx` 全文与 `frontend/src/components/ChatWindow.tsx` 全文，确认 `handleSend`、`onNode`、消息构造、ChatWindow props 的现有写法，再据实改写下面的代码。下述代码以现有结构为前提；若 props 名有出入，按实际对齐。）

- [ ] **Step 2: App.tsx 引入 mode 状态**

打开 `frontend/src/App.tsx`。

(a) 顶部 import 增补：
```typescript
import type { ChatMode } from "./types";
import { MULTI_AGENT_PIPELINE, PIPELINE } from "./types";
```
（若已 import `PIPELINE`，改为同时引入 `MULTI_AGENT_PIPELINE` 与 `ChatMode`。）

(b) 在组件状态区（与 `messages` 同处）新增：
```typescript
const [mode, setMode] = useState<ChatMode>("rag");
const [webSearchAvailable, setWebSearchAvailable] = useState<boolean>(true);
```

(c) 在拉取 health 的 effect 里，更新 `webSearchAvailable`（找到 `fetchHealth` 调用处，在拿到 `h` 后加）：
```typescript
setWebSearchAvailable(h.web_search);
```

(d) 改造 `handleSend`（定位现有调用 `chatStream(...)` 处），关键三处改动：
  - 选管线：把构造 `steps` 时用的 `PIPELINE` 改为按模式选择：
    ```typescript
    const pipeline = mode === "multi" ? MULTI_AGENT_PIPELINE : PIPELINE;
    const steps = pipeline.map((p) => ({ ...p, status: "pending" as const }));
    ```
  - 在助手消息对象上加 `mode`：`mode,` 字段。
  - `chatStream` 调用加 `mode`：
    ```typescript
    await chatStream(text, history, {
      onNode: (node, update) => {
        setMessages((prev) => prev.map((m) => {
          if (m.id !== assistantId) return m;
          // 多智能体：把两个 agent 的原始回答 + 来源写入消息（供折叠面板/徽章）
          if (node === "rag_agent_node") {
            return { ...m, ragAgentAnswer: update.answer, sources: update.sources ?? m.sources, usedRag: update.used_rag };
          }
          if (node === "web_agent_node") {
            return { ...m, webAgentAnswer: update.answer, sources: [...(m.sources ?? []), ...(update.sources ?? [])], usedWeb: update.used_web };
          }
          // 通用步进器更新（RAG 与多智能体共用）
          const steps = m.steps?.map((s) =>
            s.key === node ? { ...s, status: "done" as const } : s
          );
          // 激活下一个未完成步骤
          const nextIdx = steps?.findIndex((s) => s.status === "pending");
          const finalSteps = steps?.map((s, i) =>
            i === nextIdx ? { ...s, status: "active" as const } : s
          );
          return { ...m, steps: finalSteps };
        }));
      },
      onDelta: (t) => { /* 现有：追加 token 到助手消息 content */ },
      onDone: () => { /* 现有：标记 streaming=false、所有 step=done */ },
      onError: (msg) => { /* 现有 */ },
    }, mode);  // ← 注意末尾传入 mode
    ```

> 上面 `onNode` 用「替换」语气给出目标逻辑；落地时**保留现有 `onNode/onDelta/onDone/onError` 的其余实现**，只新增 `rag_agent_node`/`web_agent_node` 两个分支并把 `mode` 透传。若现有步进器更新逻辑更复杂，按其结构合并，不要破坏 RAG 模式行为。

(e) 把 `mode`、`setMode`、`webSearchAvailable` 作为 props 传给 `<ChatWindow ...>`。

- [ ] **Step 3: ChatWindow.tsx 增加模式切换控件**

打开 `frontend/src/components/ChatWindow.tsx`。

(a) 在 props 类型里增加：
```typescript
mode: ChatMode;
onModeChange: (m: ChatMode) => void;
webSearchAvailable: boolean;
```
并在文件顶部 `import type { ChatMode } from "../types";`。

(b) 在输入框上方（或对话框顶部）渲染分段控件：
```tsx
<div className="mode-switch" role="tablist" aria-label="问答模式">
  <button
    type="button"
    role="tab"
    aria-selected={mode === "rag"}
    className={mode === "rag" ? "active" : ""}
    onClick={() => onModeChange("rag")}
  >
    RAG问答
  </button>
  <button
    type="button"
    role="tab"
    aria-selected={mode === "multi"}
    className={mode === "multi" ? "active" : ""}
    disabled={!webSearchAvailable}
    title={webSearchAvailable ? "多智能体：RAG + 联网 + 整合" : "未配置 TAVILY_API_KEY"}
    onClick={() => onModeChange("multi")}
  >
    多智能体
  </button>
</div>
```

- [ ] **Step 4: 验收检查点——构建**

Run:
```powershell
npm --prefix D:\project\customer\AI\RagGraphSys\frontend run build
```
Expected: tsc + vite build 通过。若有 TS 报错（如 ChatWindow 漏接 props、App 里 `steps` 类型不匹配），就地修正。

---

