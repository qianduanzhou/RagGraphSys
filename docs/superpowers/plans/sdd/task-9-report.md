# Task 9 报告：前端 MessageBubble 多智能体渲染 + 折叠面板

## 状态
完成。`npm run build` 通过。

## 改动文件（仅 2 个，符合任务约束）
- `frontend/src/components/MessageBubble.tsx`
- `frontend/src/components/MessageBubble.css`

## 对 MessageBubble.tsx 的具体改动

### 1. web 来源徽章（Step 2）
现有来源徽章通过 `<SourceBadge>` 组件渲染（仅处理 qdrant / neo4j）。为不引入对第三个文件的改动，在 `MessageBubble.tsx` 的 `sources.map(...)` 处加内联分支：`source.type === "web"` 渲染为可点击链接徽章，否则回落到既有的 `<SourceBadge>`。关键片段：

```tsx
{sources.map((s, i) =>
  s.type === "web" ? (
    <a
      key={i}
      className="source-badge web"
      href={s.url}
      target="_blank"
      rel="noopener noreferrer"
      title={s.title || s.url}
    >
      {"🔗 "}
      {s.title || s.url}
    </a>
  ) : (
    <SourceBadge key={i} source={s} />
  )
)}
```

### 2. 默认折叠的原始回答面板（Step 3）
在助手消息气泡内、来源区块**之后**，新增两个 `<details>` 面板。仅当 `message.mode === "multi"` 且存在 `ragAgentAnswer` / `webAgentAnswer` 时渲染。`<details>` 原生默认折叠，无需 JS 状态。片段：

```tsx
{message.mode === "multi" &&
  (message.ragAgentAnswer || message.webAgentAnswer) && (
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

### 3. 步进器对多智能体管线的兼容（Step 4）
**无需改动**：现有代码已按 `message.steps.map(...)` 渲染（未硬编码 `PIPELINE`）。`message.steps` 由 App.tsx 按模式填充（RAG=5 步、多智能体=4 步），因此天然满足「RAG 消息显示 5 步、多智能体消息显示 4 步」的要求。仅保留原逻辑。

> 备注：本次未做 sources 去重（Task 8 review 的 Minor）。RAG 文档来源（qdrant/neo4j）与 web URL 来源实际不重叠，brief 标注为非强制；如后续需要，可在 map 前按 `type+url/content` 做一次 `Map` 去重。

## 对 MessageBubble.css 的具体改动
**末尾追加**（未覆盖既有规则），新增选择器：

- `.mode-switch` / `.mode-switch button` / `.mode-switch button.active` / `.mode-switch button:disabled`：ChatWindow 已有的 `.mode-switch` 控件配套样式（分段按钮组，active 高亮，disabled 置灰 0.45 透明度）。
- `.source-badge.web` / `.source-badge.web:hover`：web 来源链接徽章，链接色边框圆角，hover 轻微底色（`rgba(37,99,235,0.08)`）。额外加了 `align-self: flex-start` 以适配 `.msg-sources-list` 的纵向 flex 布局（让链接徽章不撑满整行）。
- `.agent-panels` / `.agent-panel` / `.agent-panel summary` / `.agent-panel-body`：折叠面板容器（纵向 gap 6px）、面板边框圆角、可点击 summary（虚线分隔 + pre-wrap 保留原始回答换行）。

## npm run build 输出摘要
```
> hybrid-rag-frontend@1.0.0 build
> tsc -b && vite build

vite v5.4.21 building for production...
✓ 2041 modules transformed.
dist/index.html                  0.87 kB │ gzip:   0.47 kB
dist/assets/index-BaoAPdeR.css  17.19 kB │ gzip:   4.28 kB
dist/assets/index-BWyleNhW.js 498.35 kB │ gzip: 155.01 kB
✓ built in 5.49s
```
`tsc -b` 无类型错误，`vite build` 成功。

## 疑虑
1. **web 徽章分支位置选择**：brief Step 2 示意「在 SourceBadge 组件的分支处」加 web 分支。现有 `SourceBadge` 是独立组件，要么改 `SourceBadge.tsx`（超出 2 文件约束），要么在 `MessageBubble.tsx` 的 map 处内联分支。我选了后者以满足「仅改 2 个文件」。后续如希望 web 徽章与 qdrant/neo4j 徽章风格完全统一，可考虑把 web 分支迁回 `SourceBadge.tsx`。
2. **未做端到端手验**（Step 7）：本任务环境无后端运行条件与 Tavily key，仅完成静态构建验收。运行时验证（步进器点亮顺序、web 徽章跳转、折叠面板展开）需在有后端 + Tavily key 的环境由用户执行。
3. **构建路径**：`npm --prefix` 在 Git Bash 下需用 POSIX 路径（`/d/project/...`），Windows 反斜杠路径会被吞成错误路径；最终用 POSIX 路径成功。
