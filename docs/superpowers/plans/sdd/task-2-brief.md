## Task 2: WebSearchService（TDD）

**Files:**
- Create: `backend/services/web_search_service.py`
- Test: `backend/tests/test_web_search_service.py`

**Interfaces:**
- Consumes: `Settings`（Task 1 的 `tavily_api_key` / `tavily_max_results`）
- Produces: `WebSearchService(settings, client=None)`；`.available -> bool`；`.search(query, max_results=None) -> List[{title,url,content,score}]`（失败/不可用返回 `[]`）。后续 `MultiAgentNodes` 持有一个 `WebSearchService` 实例。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_web_search_service.py`：

```python
"""WebSearchService 测试：Tavily 客户端以 fake 注入，全程不触网。"""
from core.config import Settings
from services.web_search_service import WebSearchService


class FakeTavily:
    """模拟 tavily.TavilyClient：只实现本服务用到的 search。"""

    def __init__(self, results=None, raise_search=False):
        self._results = results or []
        self._raise = raise_search

    def search(self, query, max_results=5, search_depth="basic"):
        if self._raise:
            raise RuntimeError("tavily down")
        return {"results": self._results}


def test_search_normalizes_results():
    fake = FakeTavily(results=[{"title": "T", "url": "http://x", "content": "c", "score": 0.9}])
    svc = WebSearchService(Settings(llm_api_key="k"), client=fake)
    assert svc.search("q") == [{"title": "T", "url": "http://x", "content": "c", "score": 0.9}]


def test_search_empty_when_no_results():
    svc = WebSearchService(Settings(llm_api_key="k"), client=FakeTavily(results=[]))
    assert svc.search("q") == []


def test_search_returns_empty_on_exception():
    svc = WebSearchService(Settings(llm_api_key="k"), client=FakeTavily(raise_search=True))
    assert svc.search("q") == []


def test_search_empty_when_no_query():
    svc = WebSearchService(Settings(llm_api_key="k"), client=FakeTavily(results=[{"title": "T"}]))
    assert svc.search("") == []


def test_unavailable_when_no_key_and_no_client():
    svc = WebSearchService(Settings())  # 无 key、无注入 client
    assert svc.available is False
    assert svc.search("q") == []


def test_available_when_client_injected():
    svc = WebSearchService(Settings(llm_api_key="k"), client=FakeTavily())
    assert svc.available is True


def test_search_respects_max_results_override():
    captured = {}

    class _C(FakeTavily):
        def search(self, query, max_results=5, search_depth="basic"):
            captured["max_results"] = max_results
            return {"results": []}

    svc = WebSearchService(Settings(llm_api_key="k"), client=_C())
    svc.search("q", max_results=8)
    assert captured["max_results"] == 8
```

- [ ] **Step 2: 运行测试确认失败**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest backend/tests/test_web_search_service.py -v
```
Expected: FAIL（`ModuleNotFoundError: No module named 'services.web_search_service'`）。

- [ ] **Step 3: 实现 WebSearchService**

创建 `backend/services/web_search_service.py`：

```python
"""Web 搜索服务 —— 唯一封装 Tavily 的地方。

与 services/llm_service.py、services/embedding_service.py 同属「模型边界」：
业务与图代码只调用本服务的方法，不直接接触 Tavily 客户端。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.config import Settings
from core.logger import get_logger

logger = get_logger(__name__)


class WebSearchService:
    """基于 Tavily 的联网搜索封装。不可用时优雅降级（返回空结果）。"""

    def __init__(self, settings: Settings, client: Optional[Any] = None):
        self.settings = settings
        self._max_results = settings.tavily_max_results
        if client is not None:
            # 测试或自定义实现可注入
            self._client = client
        elif settings.tavily_api_key:
            try:
                from tavily import TavilyClient

                self._client = TavilyClient(api_key=settings.tavily_api_key)
                logger.info("WebSearch 初始化完成：max_results=%d", self._max_results)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Tavily 客户端初始化失败，联网搜索不可用：%s", exc)
                self._client = None
        else:
            logger.info("未配置 TAVILY_API_KEY，联网搜索不可用")
            self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def search(self, query: str, max_results: Optional[int] = None) -> List[Dict[str, Any]]:
        """执行联网搜索，返回归一化结果 ``[{title, url, content, score}]``。

        不可用、空 query 或异常时返回 ``[]``，不抛错（让上层 agent 优雅降级）。
        """
        if not self.available or not query:
            return []
        limit = max_results or self._max_results
        try:
            resp = self._client.search(
                query=query,
                max_results=limit,
                search_depth="basic",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tavily 搜索失败：%s", exc)
            return []
        results = resp.get("results", []) if isinstance(resp, dict) else []
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
                "score": r.get("score"),
            }
            for r in results
            if isinstance(r, dict)
        ]
```

- [ ] **Step 4: 运行测试确认通过**

Run:
```powershell
& "D:\project\customer\AI\RagGraphSys\backend\.venv\Scripts\python.exe" -m pytest backend/tests/test_web_search_service.py -v
```
Expected: 7 passed。

---

