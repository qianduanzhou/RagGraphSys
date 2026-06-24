## Task 5: main.py lifespan 装配

**Files:**
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `WebSearchService`（Task 2）、`build_multi_agent_graph`（Task 4）、`settings`
- Produces: `app.state.web`（`WebSearchService`）、`app.state.multi_agent_graph`（编译图）。Task 6 的 `_select_graph`/`/health` 读取它们。

- [ ] **Step 1: 修改 main.py 导入与 lifespan**

打开 `backend/main.py`。

(a) 在导入区（`from graph import build_graph` 之后）新增两行：

```python
from multiagent import build_multi_agent_graph
from services.web_search_service import WebSearchService
```

(b) 在 `lifespan` 函数内、`app.state.graph = build_graph(llm, rag, settings)`（第 53 行）**之后**、`logger.info("Application ready...")` **之前**，插入：

```python
    web = WebSearchService(settings)
    app.state.web = web
    app.state.multi_agent_graph = build_multi_agent_graph(llm, rag, web, settings)
```

（`web` 即使无 key 也会构造成功——`available=False`，`build_multi_agent_graph` 照常编译；运行时 `web_agent` 自动降级。）

- [ ] **Step 2: 验收检查点——应用能启动（降级模式）**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -c "import main; print('ok'); print('web=', main.app.state.__dict__.get('web') is not None or 'lazy'); print('multi=', main.app.state.__dict__.get('multi_agent_graph') is not None or 'lazy')"
```

> 注：`lifespan` 在 `TestClient` / 实际启动时才执行，`import main` 不触发它。上面的命令主要验证「导入 main 不报错」。真正的 lifespan 触发在 Task 6 的 TestClient 集成测试里验证。

Expected: 打印 `ok` 且无导入错误（`lif` 字段无所谓）。

- [ ] **Step 3: 用 TestClient 触发 lifespan 验证装配**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -c "from fastapi.testclient import TestClient; import main; c=TestClient(main.app); c.__enter__(); print('web=', main.app.state.web.available); print('multi=', main.app.state.multi_agent_graph is not None); c.__exit__(None,None,None)"
```
Expected: 打印 `web= False`（无 key）与 `multi= True`，无异常。

---

