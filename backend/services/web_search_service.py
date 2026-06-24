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
