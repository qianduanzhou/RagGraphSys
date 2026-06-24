# Task 5 报告：main.py lifespan 装配

## 状态

完成。

## 改动文件

- `backend/main.py`
  - 导入区（`from graph import build_graph` 之后）：新增
    - `from multiagent import build_multi_agent_graph`
    - `from services.web_search_service import WebSearchService`
  - `lifespan` 内，`app.state.graph = build_graph(llm, rag, settings)` 之后、`logger.info("Application ready...")` 之前，插入：
    - `web = WebSearchService(settings)`
    - `app.state.web = web`
    - `app.state.multi_agent_graph = build_multi_agent_graph(llm, rag, web, settings)`

逐字按 brief，未改动其它文件。

## Step 2 验证（应用能启动/导入不报错）

命令：
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -c "import main; print('ok'); print('web=', main.app.state.__dict__.get('web') is not None or 'lazy'); print('multi=', main.app.state.__dict__.get('multi_agent_graph') is not None or 'lazy')"
```

实际输出：
```
ok
web= lazy
multi= lazy
```

符合预期（`import main` 不触发 lifespan，`web`/`multi` 此时未挂上，打印 `lazy`）。无导入错误。

## Step 3 验证（TestClient 触发 lifespan）

命令：
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -c "from fastapi.testclient import TestClient; import main; c=TestClient(main.app); c.__enter__(); print('web=', main.app.state.web.available); print('multi=', main.app.state.multi_agent_graph is not None); c.__exit__(None,None,None)"
```

实际输出（截取关键行）：
```
2026-06-22 10:41:14 | INFO     | services.web_search_service | δ配置 TAVILY_API_KEY，网络搜索不可用
2026-06-22 10:41:14 | INFO     | multiagent.graph | MultiAgent graph compiled: dispatch->(rag,web)->integration
2026-06-22 10:41:14 | INFO     | main | Application ready: http://0.0.0.0:8000
web= False
multi= True
2026-06-22 10:41:14 | INFO     | main | Application stopped.
```

符合预期：
- `web= False`：无 Tavily key，`WebSearchService` 仍构造成功，`available=False`。
- `multi= True`：`build_multi_agent_graph` 始终编译，不因缺 key 而不建图。
- 无异常抛出。

## 疑虑

- 启动日志显示 Qdrant（502）与 Neo4j（连接拒绝）在本地不可用，属于已有降级路径（Task 0-4 范围），与本任务无关，不影响 `web`/`multi_agent_graph` 装配。
- `multiagent.graph` 编译日志 `dispatch->(rag,web)->integration` 与 brief 描述一致；web_agent 内部降级由运行时处理，符合设计。
- 无其他疑虑。
