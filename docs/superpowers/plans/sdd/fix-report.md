# Minor 修复报告

- **状态**：全部完成，3 项验证均通过。
- **作用域**：仅改 3 个文件，无 git 操作，未触碰其它文件。
- **日期**：2026-06-22

---

## 1. 后端测试隔离 — `backend/tests/test_api.py`

### 问题
`test_chat_multi_503_when_graph_missing` 直接写 `main.app.state.multi_agent_graph = None`，用例结束后**未恢复**，污染 `app.state` 全局单例（测试重排/并行会串扰）。

### 改动
给用例加 `monkeypatch` fixture 参数，用 `monkeypatch.setattr` 注入（用例结束自动还原原值）。断言保持不变（仍断言 503）。

```diff
-def test_chat_multi_503_when_graph_missing(client):
-    main.app.state.multi_agent_graph = None
+def test_chat_multi_503_when_graph_missing(client, monkeypatch):
+    # 用 monkeypatch 注入，用例结束自动还原，避免污染 app.state 全局单例。
+    monkeypatch.setattr(main.app.state, "multi_agent_graph", None)
     r = client.post("/api/chat", json={"message": "hi", "history": [], "mode": "multi"})
     assert r.status_code == 503
```

### 说明
- 同文件其它用例（`test_chat_with_mock_graph`、`test_chat_stream_*`、`test_chat_multi_routes_to_multi_graph` 等）也直接写 `app.state`，但它们是赋值为 mock 对象、属于各用例的独立桩，未在本次清单范围内，保持原样未动（清单明确仅处理 503 用例）。

---

## 2. 前端空 url 守卫 — `frontend/src/components/MessageBubble.tsx`

### 问题
web 来源徽章 `<a href={s.url} ...>`，`SourceRef.url` 是可选字段；若 `url` 为空会渲染 `href=""` 空链接（点击会跳到当前页根路径）。

### 改动
渲染 web 来源时加守卫——仅当 `s.url` 为真值才渲染为 `<a>`，否则回退为非链接的纯文本 `<span>`（只显示标题 / `🔗`）。其余渲染逻辑（qdrant/neo4j 分支、`SourceBadge`、外层 `sources.map`）保持不变。

```diff
                 {sources.map((s, i) =>
                   // web 来源渲染为可点击链接徽章，与既有 qdrant/neo4j 徽章分支并列。
+                  // SourceRef.url 是可选字段：仅在 url 为真值时渲染 <a>，否则回退为纯文本，避免空 href。
                   s.type === "web" ? (
-                    <a
-                      key={i}
-                      className="source-badge web"
-                      href={s.url}
-                      target="_blank"
-                      rel="noopener noreferrer"
-                      title={s.title || s.url}
-                    >
-                      {"🔗 "}
-                      {s.title || s.url}
-                    </a>
+                    s.url ? (
+                      <a
+                        key={i}
+                        className="source-badge web"
+                        href={s.url}
+                        target="_blank"
+                        rel="noopener noreferrer"
+                        title={s.title || s.url}
+                      >
+                        {"🔗 "}
+                        {s.title || s.url}
+                      </a>
+                    ) : (
+                      <span
+                        key={i}
+                        className="source-badge web"
+                        title={s.title || ""}
+                      >
+                        {"🔗 "}
+                        {s.title || "（无标题）"}
+                      </span>
+                    )
                   ) : (
                     <SourceBadge key={i} source={s} />
                   )
                 )}
```

### 说明
- 回退 `<span>` 复用了同一 `className="source-badge web"` 以保持视觉一致；无 url 时 title 仅取 `s.title`（不再回退到 `s.url`，因为 url 为空）。
- 文案「（无标题）」用于 url 与 title 双双为空的极端情况。

---

## 3. README 测试统计过时 — `README.md`

### 问题
文中「87 个 pytest 用例，覆盖率约 94%」与实际（后端 167 passed）不符。

### 改动
全文搜索「87」用例数共 3 处（核心特性 / 项目结构注释 / 测试节注释），统一更新为 **167**；覆盖率描述（约 94%）按要求保留。其余与测试统计无关的「87」（端口 `7687` 中的数字）不动。

3 处 diff：

```diff
-- 核心特性（第 38 行）
-- **自带测试**：87 个 pytest 用例，覆盖率约 **94%**，全程 mock，无需联网。
+- **自带测试**：167 个 pytest 用例，覆盖率约 **94%**，全程 mock，无需联网。
```

```diff
-- 项目结构注释（第 53 行）
-│   ├── tests/                     # pytest 测试套件（87 个）
+│   ├── tests/                     # pytest 测试套件（167 个）
```

```diff
-- 测试节（第 198 行）
-pytest                                   # 运行全部（87 个）
+pytest                                   # 运行全部（167 个）
```

### 说明
- 验证：全文剩余「87」仅出现在端口 `7687`（共 3 处：`.env` 表、部署架构图、端口对照表），均与测试统计无关，未改动。
- 覆盖率数字（约 94%）本次未重新测量，按要求保留原描述，仅更用例数。

---

## 验证结果（实际输出摘要）

所有命令在 `D:\project\customer\AI\RagGraphSys` 下执行。

### 1. 后端 `test_api.py`
```
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest backend/tests/test_api.py -v
```
- 结果：**30 passed in 189.02s**
- 关键用例 `test_chat_multi_503_when_graph_missing PASSED`（monkeypatch 改造后仍断言 503 通过）。

### 2. 后端全套
```
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest backend/tests -q
```
- 结果：**167 passed in 206.79s**（与 README 更新后的用例数一致，全绿）。

### 3. 前端 build
```
npm --prefix D:\project\customer\AI\RagGraphSys\frontend run build
```
- 结果：tsc 无类型错误，vite v5.4.21 **✓ built in 3.81s**，2041 modules transformed。
- 产物：`dist/index.html` 0.87 kB、`dist/assets/index-BaoAPdeR.css` 17.19 kB、`dist/assets/index-DzUowJH4.js` 498.45 kB（gzip 155.04 kB）。

---

## 疑虑

1. **同文件其它用例仍直接写 `app.state`**：`test_api.py` 中 `test_chat_with_mock_graph`、`test_chat_stream_sse_frames`、`test_chat_multi_routes_to_multi_graph`、`test_chat_default_mode_is_rag`、`test_chat_stream_multi_emits_agent_nodes` 等也直接赋值 `main.app.state.graph` / `multi_agent_graph`（赋值为 mock 对象）。本次清单仅要求处理 503 用例，故未一并改造；若后续要做更彻底的测试隔离，可统一迁移到 `monkeypatch` 或 fixture + teardown 模式。
2. **覆盖率未重新测量**：README 覆盖率（约 94%）按要求保留，本次未跑 `--cov` 重新统计，数字可能已随用例增长而漂移。
3. **回退 `<span>` 的视觉一致性**：MessageBubble 空 url 回退分支复用了 `className="source-badge web"`，依赖该 class 对 `<span>` 也有合理样式；CSS 未改、前端 build 通过，但未在浏览器实际渲染验证空 url 场景的视觉效果（如有视觉异常，可能需微调 `.source-badge.web` 对 `<span>` 的样式）。
