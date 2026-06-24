## Task 10: README 文档更新

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: 全部前序任务
- Produces: 文档反映新能力（多智能体模式、Tavily 配置、新 SSE 字段）。

- [ ] **Step 1: 在 README 适当位置（技术栈表 / 功能列表 / .env 字段表 / API 表 / SSE 帧格式 各节）增补**

(a) 功能列表增加一条：
```
- 多智能体问答模式：RAG 智能体 + 联网智能体（Tavily）并行检索，整合智能体综合后流式输出（前端可切换，默认 RAG）。
```

(b) `.env` 字段表增加：
```
| TAVILY_API_KEY    | Tavily 联网搜索 API key（多智能体模式用；留空则联网自动降级） | （空）        |
| TAVILY_MAX_RESULTS| 联网搜索返回条数上限                                          | 5             |
```

(c) API 表 `/api/chat`、`/api/chat/stream` 备注里加：请求体新增可选字段 `mode`（`"rag"` 默认 / `"multi"`）。

(d) SSE 帧格式节，`node` 帧的节点名表追加多智能体节点，并说明 `update` 在多智能体下两个 agent 节点会额外携带 `answer`（原始回答文本，供折叠面板）：
```
| rag_agent_node   | {answer, sources, hits, used_rag}            |
| web_agent_node   | {answer, sources:[{type:"web",title,url,...}], hits, used_web} |
| integration_node | {iterations}                                 |
| dispatch_node    | {}                                           |
```

(e) `/api/health` 返回字段说明加 `web_search: bool`。

- [ ] **Step 2: 验收检查点**

人工通读 README 相关小节，确认无遗留 `TBD`、字段名与代码一致。

---

## Self-Review（计划作者自检，已执行）

- **Spec 覆盖**：spec 第 5 节全部组件 → Task 1–6；第 5.3 前端 → Task 7–9；第 6 数据流 → Task 6 SSE 测试 + Task 8 onNode；第 7 Prompt → Task 3 节点实现内嵌；第 8 降级矩阵 → Task 2/3/6 的兜底分支测试；第 9 测试 → 各任务 TDD；第 10 文档 → Task 10。无遗漏。
- **占位符扫描**：Task 1 Step 3 的 `<VER>` 是「安装后回读并替换」的显式指令（非悬空占位）；其余步骤均含可执行代码/命令。
- **类型一致性**：`build_multi_agent_graph(llm, rag, web, settings)`、`WebSearchService(settings, client=None).search()`、`MultiAgentNodes(llm, rag, web, settings)`、`route_after_dispatch`、`_select_graph`、`ChatRequest.mode`、`MULTI_AGENT_PIPELINE` 等命名在定义任务与消费任务间一致。`MultiAgentState` 统一为 `TypedDict(total=False)`（Task 3/4 一致，与 `GraphState` 同构）。
- **修正项**：规划中发现并已更新 spec 的两处——(1) `RagService.retrieve` 返回 dict 而非元组，改用 `build_context`；(2) 无 Tavily key 改为「始终建图 + 优雅降级」而非 503。spec 已同步修改。

---

## 执行交付

计划已保存至 `docs/superpowers/plans/2026-06-22-multi-agent.md`。两种执行方式：

**1. Subagent-Driven（推荐）** — 每个 Task 派发独立 subagent，任务间 review，迭代快。

**2. Inline Execution** — 在当前会话用 executing-plans 批量执行，带检查点。

请选择执行方式。
