# Task 8 报告：前端模式切换 + App.tsx 编排

## 状态
完成。`npm run build` 通过（tsc -b + vite build，无 TS 报错，3.34s，2041 模块）。

## 改动文件
- `frontend/src/App.tsx`
- `frontend/src/components/ChatWindow.tsx`

（仅这两个文件；MessageBubble 未改，留给 Task 9；未改任何 CSS 文件。）

## 对现有 handleSend / onNode 的具体合并

### App.tsx
保留的既有逻辑（结构未变）：
- `patch(updater)` 局部更新助手气泡的写法保留。
- 通用步进器推进逻辑**逐字保留**：先 `findIndex` 命中节点置 `done`，相邻下一步 `pending`→`active`，并保留 `idx+1 < length` 与 `status !== "done"` 的判断。
- `onDelta` / `onDone` / `onError` 实现未变；`finally { setStreaming(false); refreshHealth(); }` 未变。
- 通用分支里 `if (update.sources) next.sources = update.sources;` 与 `used_rag` 透传逻辑保留——RAG 模式行为与改动前一致。

新增：
- 顶部 import 增补 `MULTI_AGENT_PIPELINE`、`type ChatMode`。
- 新增状态 `mode: ChatMode`（默认 `"rag"`）与 `webSearchAvailable`（默认 `true`）。
- `useEffect` 拉取 health 处与 `refreshHealth` 回调里，都新增 `setWebSearchAvailable(h.web_search)`（保持两处一致，避免刷新后状态漂移）。
- `handleSend` 顶部按模式选管线：
  ```ts
  const pipeline = mode === "multi" ? MULTI_AGENT_PIPELINE : PIPELINE;
  ```
  助手消息对象新增 `mode` 字段；`steps` 由 `pipeline` 派生。
- `onNode` 在通用步进器分支**之前**新增两个早期返回分支：
  - `node === "rag_agent_node"`：写入 `ragAgentAnswer: update.answer`，按需合并 `sources`、记录 `usedRag`。
  - `node === "web_agent_node"`：写入 `webAgentAnswer: update.answer`，**合并**来源 `sources: [...(m.sources ?? []), ...(update.sources ?? [])]`（避免覆盖 RAG 来源），记录 `usedWeb`。
  两个 agent 分支都 `return next`，不再落入通用步进器分支（这两个 key 本就不在通用步进器 `steps` 里，故 RAG 模式行为不变）。
- `chatStream(...)` 末尾传入 `mode`；`useCallback` 依赖数组追加 `mode`。

### ChatWindow.tsx
- props 类型新增 `mode: ChatMode` / `onModeChange: (m: ChatMode) => void` / `webSearchAvailable: boolean`，并在函数签名解构；import 增加 `type ChatMode`。
- 在 `.composer` 内、`.composer-box` 之前渲染 `role="tablist"` 的分段控件：RAG问答 / 多智能体；多智能体按钮 `disabled={!webSearchAvailable}` 并带 `title` tooltip（可用时「多智能体：RAG + 联网 + 整合」，不可用时「未配置 TAVILY_API_KEY」）。
- App.tsx 中已把 `mode` / `onModeChange={setMode}` / `webSearchAvailable` 透传给 `<ChatWindow>`。

## 构建输出摘要
```
> hybrid-rag-frontend@1.0.0 build
> tsc -b && vite build
vite v5.4.21 building for production...
✓ 2041 modules transformed.
dist/index.html                  0.87 kB │ gzip:   0.48 kB
dist/assets/index-VNpwTo2Z.css  16.17 kB │ gzip:   4.06 kB
dist/assets/index-BLofI2N4.js  497.69 kB │ gzip: 154.79 kB
✓ built in 3.34s
```
tsc 与 vite 均无报错。

## 疑虑
- `.mode-switch` 分段控件**未配套 CSS**（brief 限定只改两个文件，未在清单里列 ChatWindow.css）。控件能正常渲染与交互，active/disabled 由类名/属性承担，但视觉上目前是浏览器默认按钮样式。若后续需要与现有暗色主题一致的外观，建议在 Task 9 或单独样式任务里补 `.mode-switch` 相关样式（参考 `.composer-box` 的 `--panel`/`--border`/`--accent` 变量）。
- `web_agent_node` 的 `sources` 采用合并而非替换；若后端在 `web_agent_node` 帧里携带的来源与 `rag_agent_node` 重复，UI 上可能重复展示。当前按 brief 目标逻辑（`[...(m.sources ?? []), ...(update.sources ?? [])]`）实现，去重留给 Task 9 的 MessageBubble 渲染层处理更合适。
