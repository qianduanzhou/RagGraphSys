# Task 10 报告：README 文档更新

**状态**：已完成

**改动文件**：`D:\project\customer\AI\RagGraphSys\README.md`（仅此一个）

## brief a-e 逐条对应

### (a) 功能列表增加一条
位置：`## 核心特性` 节，插在「优雅降级」与「工程规范」之间。
新增片段：
```
- **多智能体问答模式**：RAG 智能体 + 联网智能体（Tavily）并行检索，整合智能体综合后流式输出（前端可切换，默认 RAG）。
```

### (b) `.env` 字段表增加 Tavily 两项
位置：`## 二、配置说明（.env）` 的变量表，插在 `MAX_REFLECTION_ITERATIONS` 行之后、`APP_HOST` 行之前。
新增片段：
```
| `TAVILY_API_KEY` | Tavily 联网搜索 API key（多智能体模式用；留空则联网自动降级） | （空） |
| `TAVILY_MAX_RESULTS` | 联网搜索返回条数上限 | `5` |
```

### (c) API 表 `/api/chat`、`/api/chat/stream` 加 `mode`
位置：`## 三、API 接口` 的表格。
- 两行的「入参」列由 `{message, history}` 改为 `{message, history, mode?}`。
- 表格下方新增一行说明：
```
> `/api/chat`、`/api/chat/stream` 请求体新增可选字段 `mode`：`"rag"`（默认，单路 RAG 管线） / `"multi"`（多智能体模式：RAG 智能体 + 联网智能体并行 + 整合智能体）。
```

### (d) SSE 帧格式节追加多智能体节点
位置：`## 三、API 接口` 的 SSE 帧格式段，在既有 3 类帧示例之后追加一段说明 + 节点表：
```
多智能体模式（`mode="multi"`）下 `node` 帧会改用以下节点名，且两个 agent 节点的 `update` 会额外携带 `answer`（原始回答文本，供前端默认折叠的原始回答面板展示）：

| 节点名 | `update` 字段 |
|--------|---------------|
| `rag_agent_node` | `{answer, sources, hits, used_rag}` |
| `web_agent_node` | `{answer, sources:[{type:"web",title,url,...}], hits, used_web}` |
| `integration_node` | `{iterations}` |
| `dispatch_node` | `{}` |
```
节点名与字段名与实现一致（`rag_agent_node` / `web_agent_node` / `integration_node` / `dispatch_node`，`answer` / `sources` / `used_rag` / `used_web` / `iterations`）。

### (e) `/api/health` 字段说明加 `web_search: bool`
位置：`## 三、API 接口` 表格 `/api/health` 行。
「返回」列由 `{status, qdrant, neo4j, counts}` 改为 `{status, qdrant, neo4j, counts, web_search}`，并在「存活与依赖检查」后保留不变。README 中 `/api/health` 字段说明仅此一处（无独立字段清单小节），故在此处覆盖 brief (e)。

## 字段命名一致性核对
- `mode` 取值：`rag` / `multi` ✓
- 节点名：`rag_agent_node` / `web_agent_node` / `integration_node` / `dispatch_node` ✓
- `web_search` 布尔字段 ✓
- Tavily 配置项：`TAVILY_API_KEY` / `TAVILY_MAX_RESULTS` ✓
- 多智能体 `update` 字段：`answer` / `sources` / `used_rag` / `used_web` / `iterations` ✓

## 疑虑
- 无。本次改动为纯文档最小侵入增补，未删除/重写任何既有内容，README 原有结构（章节顺序、表格列、SSE 段、FAQ、升级方向）保持不变。
- 编辑过程中一次 Edit 的 `old_string` 与 `new_string` 雷同导致过一次错位（误替换表格行为说明行），已即时发现并修复，最终表格与说明段均已正确就位（已复核 156-184 行）。
