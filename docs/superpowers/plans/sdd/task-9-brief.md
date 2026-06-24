## Task 9: 前端 MessageBubble 多智能体渲染 + 折叠面板

**Files:**
- Modify: `frontend/src/components/MessageBubble.tsx`
- Modify: `frontend/src/components/MessageBubble.css`

**Interfaces:**
- Consumes: 消息的 `mode`、`steps`、`ragAgentAnswer`、`webAgentAnswer`、`sources`（含 `type:"web"`）
- Produces: 多智能体消息渲染多智能体管线步进器、web 来源链接徽章、默认折叠的「RAG 原始回答」「联网原始回答」面板。

- [ ] **Step 1: 读 MessageBubble 现状**

用 Read 工具读 `frontend/src/components/MessageBubble.tsx` 与 `MessageBubble.css` 全文，确认：管线步进器渲染、`sources` 徽章渲染、Markdown 渲染的现有结构。下面的改动据实合并。

- [ ] **Step 2: web 来源徽章**

在 `MessageBubble.tsx` 的来源徽章渲染处（现有按 `source.type === "qdrant" | "neo4j"` 分支处），新增 web 分支：web 徽章渲染为可点击链接：
```tsx
{source.type === "web" ? (
  <a
    key={i}
    className="source-badge web"
    href={source.url}
    target="_blank"
    rel="noopener noreferrer"
    title={source.title || source.url}
  >
    🔗 {source.title || source.url}
  </a>
) : (
  /* 现有的 qdrant / neo4j 徽章渲染 */
)}
```

- [ ] **Step 3: 默认折叠的原始回答面板**

在助手消息气泡内、最终答案**之下**，当 `message.mode === "multi"` 且存在子答案时，渲染两个折叠面板（默认收起）：
```tsx
{message.mode === "multi" && (message.ragAgentAnswer || message.webAgentAnswer) && (
  <div className="agent-panels">
    {message.ragAgentAnswer && (
      <details className="agent-panel">
        <summary>📄 查看 RAG 智能体原始回答</summary>
        <div className="agent-panel-body">{message.ragAgentAnswer}</div>
      </details>
    )}
    {message.webAgentAnswer && (
      <details className="agent-panel">
        <summary>🌐 查看联网智能体原始回答</summary>
        <div className="agent-panel-body">{message.webAgentAnswer}</div>
      </details>
    )}
  </div>
)}
```
> `<details>` 原生默认折叠，无需 JS 状态；`<summary>` 为可点击标题。

- [ ] **Step 4: 步进器对多智能体管线的兼容**

确认 `MessageBubble` 渲染 `message.steps` 时是**按 message 自带的 steps 数组**渲染（而非硬编码 `PIPELINE`）。若现有代码硬编码了 5 步，改为读 `message.steps ?? []`（App.tsx 已按模式填充了对应 steps）。这样 RAG 消息显示 5 步、多智能体消息显示 4 步。

- [ ] **Step 5: 补充样式（MessageBubble.css）**

在 `MessageBubble.css` 末尾追加：
```css
.mode-switch {
  display: inline-flex;
  gap: 0;
  border: 1px solid var(--border, #d0d7de);
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 8px;
}
.mode-switch button {
  padding: 6px 14px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 13px;
  color: inherit;
}
.mode-switch button.active {
  background: var(--accent, #2563eb);
  color: #fff;
}
.mode-switch button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.source-badge.web {
  color: var(--link, #2563eb);
  text-decoration: none;
  border: 1px solid var(--border, #d0d7de);
  border-radius: 6px;
  padding: 2px 8px;
  font-size: 12px;
}
.source-badge.web:hover {
  background: rgba(37, 99, 235, 0.08);
}

.agent-panels {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.agent-panel {
  border: 1px solid var(--border, #d0d7de);
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 13px;
}
.agent-panel summary {
  cursor: pointer;
  color: var(--muted, #57606a);
  user-select: none;
}
.agent-panel-body {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed var(--border, #d0d7de);
  white-space: pre-wrap;
  line-height: 1.6;
}
```

- [ ] **Step 6: 验收检查点——构建**

Run:
```powershell
npm --prefix D:\project\customer\AI\RagGraphSys\frontend run build
```
Expected: 通过。

- [ ] **Step 7: 端到端手动验证（需后端 + Tavily key）**

启动后端与前端（用户提供 Tavily key 写入 `backend/.env` 的 `TAVILY_API_KEY`）：
```powershell
# 后端
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" D:\project\customer\AI\RagGraphSys\backend\main.py
# 前端（另一终端）
npm --prefix D:\project\customer\AI\RagGraphSys\frontend run dev
```
验证清单：
- [ ] `/api/health` 返回 `web_search: true`。
- [ ] 切到「多智能体」模式，提问一个知识库里没有、但网络能查到的问题（如「今天日期」「某最新新闻」）。
- [ ] 管线步进器依次点亮 调度→RAG智能体/联网智能体（并行）→整合。
- [ ] 主气泡流式出现整合后的答案；答案里有 `[标题](url)` 网页链接。
- [ ] 来源区出现 web 链接徽章，可点击跳转。
- [ ] 答案下方两个折叠面板默认收起，点开分别能看到 RAG 与联网的原始回答。
- [ ] 切回「RAG问答」模式，行为与改动前一致（无回归）。
- [ ] 无 Tavily key 时：「多智能体」按钮置灰、tooltip 提示；强行发 mode=multi 不报 500，联网降级。

---

