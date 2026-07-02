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
    svc = WebSearchService(Settings(tavily_api_key=""))  # 无 key、无注入 client
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
