# Task 2 报告：WebSearchService（TDD）

## 1. 状态

`DONE`

## 2. 新建文件清单（绝对路径）

- `D:\project\customer\AI\RagGraphSys\backend\services\web_search_service.py`
- `D:\project\customer\AI\RagGraphSys\backend\tests\test_web_search_service.py`

未修改其它文件（Task 1 的 `Settings.tavily_api_key` / `Settings.tavily_max_results` 直接复用）。

## 3. TDD 各步 pytest 输出摘要

### Step 2：写完测试、实现尚未创建时（确认失败）

命令：
```
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_web_search_service.py -v
```
关键输出：
```
collected 0 items / 1 error
ERROR collecting tests/test_web_search_service.py
...
backend\tests\test_web_search_service.py:3: in <module>
    from services.web_search_service import WebSearchService
E   ModuleNotFoundError: No module named 'services.web_search_service'
=========================== short test summary info ===========================
ERROR backend\tests/test_web_search_service.py
!!!!!!!!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!
============================== 1 error in 0.36s ===============================
```
符合预期（`ModuleNotFoundError`，因实现文件尚未创建）。

### Step 4：实现落地后（确认通过）

命令：
```
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_web_search_service.py -v
```
关键输出：
```
collected 7 items

backend\tests\test_web_search_service.py::test_search_normalizes_results PASSED [ 14%]
backend\tests\test_web_search_service.py::test_search_empty_when_no_results PASSED [ 28%]
backend\tests\test_web_search_service.py::test_search_returns_empty_on_exception PASSED [ 42%]
backend\tests\test_web_search_service.py::test_search_empty_when_no_query PASSED [ 57%]
backend\tests\test_web_search_service.py::test_unavailable_when_no_key_and_no_client PASSED [ 71%]
backend\tests\test_web_search_service.py::test_available_when_client_injected PASSED [ 85%]
backend\tests\test_web_search_service.py::test_search_respects_max_results_override PASSED [100%]

============================== 7 passed in 0.06s ==============================
```
符合预期（7 passed）。

## 4. 实现要点

- `WebSearchService(settings, client=None)`；`.available -> bool`；`.search(query, max_results=None) -> List[{title,url,content,score}]`。
- 模块级**不**导入 tavily：`from tavily import TavilyClient` 位于 `__init__` 内部（懒导入），测试注入 fake client 时无需 tavily 真实可用。
- 不可用 / 空 query / 异常 / 无 key 等情形统一优雅降级返回 `[]`，不抛错。
- 测试代码与实现代码均按 brief 逐字落地，未自行增删。

## 5. 疑虑

无。
