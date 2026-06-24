## Task 1: 配置与 Tavily 依赖

**Files:**
- Modify: `backend/core/config.py`
- Modify: `backend/requirements.txt`
- Modify: `backend/.env.example`

**Interfaces:**
- Consumes: 无
- Produces: `Settings.tavily_api_key`、`Settings.tavily_max_results`；`tavily-python` 入依赖；后续任务据此构建 `WebSearchService`。

- [ ] **Step 1: config.py 新增两个配置项**

打开 `backend/core/config.py`，在 `# ---- Neo4j ----` 段（约第 54-57 行）**之后**、`# ---- RAG 流水线 ----` 段**之前**，插入新的一段：

```python
    # ---- 联网搜索（Tavily） ----
    # 多智能体模式下「联网智能体」使用。留空则联网搜索不可用，web_agent 自动降级。
    tavily_api_key: str = ""
    tavily_max_results: int = 5
```

- [ ] **Step 2: 安装 tavily-python 并读取实际版本**

Run（PowerShell）:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pip install tavily-python
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pip show tavily-python | Select-String "^Version:"
```
Expected: 第二条命令打印形如 `Version: 0.5.x`。记下该版本号 `<VER>`（后续 Step 3 用）。

- [ ] **Step 3: requirements.txt 固定版本**

打开 `backend/requirements.txt`，在 `neo4j==5.24.0` 行之后新增一行（用 Step 2 读到的真实 `<VER>` 替换）：

```
tavily-python==<VER>
```

- [ ] **Step 4: .env.example 增占位**

打开 `backend/.env.example`，在 Neo4j 段之后新增：

```
# ---- 联网搜索（Tavily） ----
# 多智能体模式需要；留空则联网智能体自动降级。免费额度：https://tavily.com
TAVILY_API_KEY=
TAVILY_MAX_RESULTS=5
```

- [ ] **Step 5: 验收检查点**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -c "from core.config import Settings; s=Settings(); print(s.tavily_api_key, s.tavily_max_results)"
```
Expected: 打印空串与 `5`（即 ` 5`），无导入错误。

---

