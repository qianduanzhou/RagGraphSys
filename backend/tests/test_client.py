"""core.client.HttpClient 的测试。"""
import pytest

from core.client import HttpClient, HttpClientError
from tests.conftest import FakeHttpSession, FakeResp


def make_client(session: FakeHttpSession) -> HttpClient:
    client = HttpClient(timeout=1, max_retries=2, backoff=0)
    client._session = session
    return client


def test_post_json_returns_parsed_body():
    session = FakeHttpSession()
    session.request_responses = [FakeResp(200, payload={"ok": True})]
    client = make_client(session)

    assert client.post_json("http://x", {"a": 1}) == {"ok": True}


def test_post_json_4xx_raises_without_retry():
    session = FakeHttpSession()
    session.request_responses = [FakeResp(404, payload={"detail": "nope"})]
    client = make_client(session)

    with pytest.raises(HttpClientError) as exc:
        client.post_json("http://x", {})
    assert exc.value.status_code == 404
    # 仅尝试一次（4xx 不重试）
    assert len([c for c in session.calls if c[0] == "request"]) == 1


def test_post_json_5xx_retries_then_raises(monkeypatch):
    monkeypatch.setattr("core.client.time.sleep", lambda *_: None)
    session = FakeHttpSession()
    session.request_responses = [FakeResp(500), FakeResp(500), FakeResp(500)]
    client = make_client(session)

    with pytest.raises(HttpClientError):
        client.post_json("http://x", {})
    # 初始 + 2 次重试 == 共 3 次尝试
    assert len([c for c in session.calls if c[0] == "request"]) == 3


def test_post_json_5xx_then_success(monkeypatch):
    monkeypatch.setattr("core.client.time.sleep", lambda *_: None)
    session = FakeHttpSession()
    session.request_responses = [FakeResp(500), FakeResp(200, payload={"recovered": True})]
    client = make_client(session)

    assert client.post_json("http://x", {}) == {"recovered": True}


def test_post_stream_parses_sse_skips_done_and_noise():
    session = FakeHttpSession()
    session.post_response = FakeResp(
        200,
        lines=[
            'data: {"choices":[{"delta":{"content":"你"}}]}',
            ": keep-alive",                 # 注释行，忽略
            'data: {"choices":[{"delta":{"content":"好"}}]}',
            "data: [DONE]",                 # 哨兵行，忽略
            'data: {"choices":[{"delta":{}}]}',  # 无 content，由调用方而非解析器忽略
            "not a data line",              # 忽略
        ],
    )
    client = make_client(session)

    chunks = list(client.post_stream("http://x", {}))
    assert chunks == [
        {"choices": [{"delta": {"content": "你"}}]},
        {"choices": [{"delta": {"content": "好"}}]},
        {"choices": [{"delta": {}}]},
    ]


def test_post_stream_4xx_raises():
    session = FakeHttpSession()
    session.post_response = FakeResp(401, text="unauthorized")
    client = make_client(session)

    with pytest.raises(HttpClientError) as exc:
        list(client.post_stream("http://x", {}))
    assert exc.value.status_code == 401
