"""HTTP 客户端封装。

所有发往外部服务的 HTTP 请求都通过 :class:`HttpClient`
统一处理认证、超时、重试与错误处理。业务代码不直接调用 ``requests``。
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterator, Optional

import requests

from .logger import get_logger

logger = get_logger(__name__)


def _safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


class HttpClientError(Exception):
    """HTTP 请求失败或返回错误状态码时抛出。"""

    def __init__(self, message: str, status_code: Optional[int] = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class HttpClient:
    """:mod:`requests` 的轻量封装，带重试与退避。"""

    def __init__(self, timeout: int = 60, max_retries: int = 2, backoff: float = 0.8):
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self._session = requests.Session()

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict] = None,
        json: Any = None,
        stream: bool = False,
    ) -> requests.Response:
        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt <= self.max_retries:
            try:
                response = self._session.request(
                    method,
                    url,
                    headers=headers,
                    json=json,
                    timeout=self.timeout,
                    stream=stream,
                )
                if response.status_code >= 500:
                    raise requests.RequestException(f"server error {response.status_code}: {response.text[:300]}")
                if response.status_code >= 400:
                    raise HttpClientError(
                        f"HTTP {response.status_code}: {response.text[:500]}",
                        status_code=response.status_code,
                        payload=_safe_json(response),
                    )
                return response
            except HttpClientError:
                # 4xx 是确定性错误，不重试。
                raise
            except requests.RequestException as exc:
                last_exc = exc
                attempt += 1
                if attempt > self.max_retries:
                    break
                sleep_for = self.backoff * attempt
                logger.warning("request to %s failed (%s); retrying in %.1fs", url, exc, sleep_for)
                time.sleep(sleep_for)

        raise HttpClientError(f"请求 {url} 在 {attempt} 次尝试后仍失败：{last_exc}") from last_exc

    def post_json(self, url: str, payload: Any, headers: Optional[dict] = None) -> Any:
        """以 JSON 形式 POST ``payload``，返回解析后的 JSON 响应。"""
        response = self._request("POST", url, headers=headers, json=payload)
        return _safe_json(response)

    def post_stream(
        self,
        url: str,
        payload: Any,
        headers: Optional[dict] = None,
    ) -> Iterator[Any]:
        """以 ``stream=True`` 方式 POST ``payload``，逐个产出 SSE JSON 数据块。

        跳过结尾的 ``[DONE]`` 哨兵以及任何非 JSON 行。出错时抛出
        :class:`HttpClientError`（本方法不对流式请求重试，因为部分输出
        对调用方仍有用）。
        """
        response = self._session.post(
            url,
            json=payload,
            headers=headers,
            timeout=self.timeout,
            stream=True,
        )
        if response.status_code >= 400:
            raise HttpClientError(
                f"HTTP {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
                payload=_safe_json(response),
            )
        for raw in response.iter_lines(decode_unicode=True):
            if not raw:
                continue
            line = raw.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]" or data == "":
                continue
            try:
                yield json.loads(data)
            except ValueError:
                # 忽略 keep-alive / 格式错误的数据帧。
                continue

    def get_json(self, url: str, headers: Optional[dict] = None, params: Optional[dict] = None) -> Any:
        """GET ``url``，返回解析后的 JSON 响应。"""
        response = self._session.request("GET", url, headers=headers, params=params, timeout=self.timeout)
        if response.status_code >= 400:
            raise HttpClientError(
                f"HTTP {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
                payload=_safe_json(response),
            )
        return _safe_json(response)
