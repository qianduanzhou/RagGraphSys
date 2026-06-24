"""共享的测试替身与 fixtures。

这里的一切都不接触网络：大模型的 HTTP 层被伪造，Qdrant / Neo4j 客户端被替换为内存中的替身。
"""
from __future__ import annotations

import json
import types
from typing import Any, Dict, Iterable, List, Optional

import pytest

from core.config import Settings


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
@pytest.fixture
def settings() -> Settings:
    """带占位 key 的确定性配置（无环境变量泄漏）。"""
    return Settings(llm_api_key="test-key")


# --------------------------------------------------------------------------- #
# 模型服务替身（ChatOpenAI / Embeddings 接口，供 services 测试注入）
# --------------------------------------------------------------------------- #
class FakeChatModel:
    """ChatOpenAI 的内存替身：实现 invoke / stream / bind。"""

    def __init__(
        self,
        chat_content: str = "hello",
        stream_chunks: Optional[List[Any]] = None,
        raise_on_invoke: bool = False,
        raise_on_stream: bool = False,
    ) -> None:
        self.chat_content = chat_content
        self.stream_chunks = stream_chunks if stream_chunks is not None else []
        self.raise_on_invoke = raise_on_invoke
        self.raise_on_stream = raise_on_stream
        self.invoked_with: Any = None
        self.bound_kwargs: List[dict] = []

    def bind(self, **kwargs):
        self.bound_kwargs.append(kwargs)
        return self

    def invoke(self, messages, **kw):
        self.invoked_with = messages
        if self.raise_on_invoke:
            raise RuntimeError("invoke boom")
        return types.SimpleNamespace(content=self.chat_content)

    def stream(self, messages, **kw):
        self.invoked_with = messages
        if self.raise_on_stream:
            raise RuntimeError("stream boom")
        for chunk in self.stream_chunks:
            yield types.SimpleNamespace(content=chunk)


class FakeEmbeddingsModel:
    """LangChain Embeddings 接口的内存替身：实现 embed_query / embed_documents。"""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.embed_query_calls: List[str] = []
        self.embed_documents_calls: List[List[str]] = []

    def embed_query(self, text: str) -> List[float]:
        self.embed_query_calls.append(text)
        return [0.0] * self.dim

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        self.embed_documents_calls.append(list(texts))
        return [[float(i)] * self.dim for i in range(len(texts))]


# --------------------------------------------------------------------------- #
# requests 替身（用于测试 HttpClient 自身）
# --------------------------------------------------------------------------- #
class FakeResp:
    def __init__(self, status_code: int = 200, payload: Any = None, text: Optional[str] = None, lines: Optional[List[str]] = None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else (json.dumps(payload) if payload is not None else "")
        self._lines = lines or []

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload

    def iter_lines(self, decode_unicode: bool = True):
        yield from self._lines


class FakeHttpSession:
    def __init__(self) -> None:
        self.request_responses: List[FakeResp] = []
        self.post_response: Optional[FakeResp] = None
        self.calls: List[tuple] = []

    def request(self, method: str, url: str, **kw) -> FakeResp:
        self.calls.append(("request", method, url, kw))
        if not self.request_responses:
            return FakeResp(200, payload={})
        return self.request_responses.pop(0)

    def post(self, url: str, **kw) -> FakeResp:
        self.calls.append(("post", url, kw))
        return self.post_response or FakeResp(200, payload={})


# --------------------------------------------------------------------------- #
# Embedding 替身
# --------------------------------------------------------------------------- #
class FakeEmbedding:
    def __init__(self, dim: int = 4) -> None:
        self.dim = dim

    def embed(self, text: str) -> List[float]:
        return [0.0] * self.dim

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [[float(i)] * self.dim for i in range(len(texts))]

    @property
    def dimension(self) -> int:
        return self.dim


# --------------------------------------------------------------------------- #
# Qdrant 替身
# --------------------------------------------------------------------------- #
class FakeQdrantClient:
    def __init__(self) -> None:
        self.exists = False
        self.created: List[dict] = []
        self.points: List[Any] = []
        self.scored: List[Any] = []

    def collection_exists(self, name: str) -> bool:
        return self.exists

    def create_collection(self, **kw) -> None:
        self.created.append(kw)
        self.exists = True

    def upsert(self, collection_name: str, points: List[Any]) -> Any:
        self.points.extend(points)
        return types.SimpleNamespace(operation_id=1)

    def query_points(self, collection_name: str, query: Any, limit: int, with_payload: bool = True):
        return types.SimpleNamespace(points=list(self.scored))

    def count(self, collection_name: str, exact: bool = True):
        return types.SimpleNamespace(count=len(self.points))

    def delete(self, collection_name: str, points_selector: Any) -> Any:
        """模拟按 payload Filter 删除点（仅支持本仓库使用的 must=[FieldCondition] 形态）。"""
        for cond in getattr(points_selector, "must", []) or []:
            key = getattr(cond, "key", None)
            value = getattr(getattr(cond, "match", None), "value", None)
            kept: List[Any] = []
            for p in self.points:
                payload = getattr(p, "payload", {}) or {}
                if payload.get(key) == value:
                    continue  # 命中过滤条件 -> 删除
                kept.append(p)
            self.points = kept
        return types.SimpleNamespace(operation_id=1)

    def scroll(self, collection_name: str, limit: int = 10, offset: Optional[int] = None,
               with_payload: bool = True, with_vectors: bool = False):
        """模拟 qdrant_client 的 scroll：返回 (records, next_offset) 元组。

        records 每个元素是带 ``id`` / ``payload`` 属性的 SimpleNamespace，
        与真实 :class:`qdrant_client.models.Record` 在本仓库的使用方式一致。
        """
        start = 0 if offset is None else int(offset)
        batch = self.points[start:start + limit]
        records = [
            types.SimpleNamespace(id=getattr(p, "id", None), payload=getattr(p, "payload", None))
            for p in batch
        ]
        next_offset = start + limit if start + limit < len(self.points) else None
        return records, next_offset


def scored(payload: dict, score: float) -> types.SimpleNamespace:
    return types.SimpleNamespace(payload=payload, score=score)


# --------------------------------------------------------------------------- #
# Neo4j 替身
# --------------------------------------------------------------------------- #
class FakeTx:
    def __init__(self, records: Optional[List[dict]] = None) -> None:
        self.calls: List[tuple] = []
        # run 返回的记录列表（供 .single() 读取，例如 delete 的 deleted 计数）。
        self._records: List[dict] = records or []

    def run(self, cypher: str, **params):
        self.calls.append((cypher, params))
        return FakeResult(list(self._records))


class FakeSession:
    def __init__(self, tx: Optional[FakeTx] = None, records: Optional[List[dict]] = None) -> None:
        self._tx = tx or FakeTx()
        self._records = records or []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute_write(self, fn, *args):
        return fn(self._tx, *args)

    def run(self, cypher: str, **params):
        return FakeResult(self._records)


class FakeResult:
    """模拟 Neo4j Result：可迭代的记录集合，并提供 .single()。"""

    def __init__(self, records: List[dict]) -> None:
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def single(self):
        return self._records[0] if self._records else None


class FakeDriver:
    def __init__(self, session: FakeSession) -> None:
        self._session = session
        self.closed = False

    def session(self) -> FakeSession:
        return self._session

    def verify_connectivity(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------- #
# 高层图替身（LLM + RAG）—— 供 nodes/graph 测试使用
# --------------------------------------------------------------------------- #
class MockLLM:
    def __init__(
        self,
        chat_resp: str = "RAG",
        stream_tokens: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
        reflect_pass: bool = True,
        reflect_feedback: str = "",
        raise_on_chat: bool = False,
        raise_on_stream: bool = False,
    ) -> None:
        self.chat_resp = chat_resp
        self.stream_tokens = stream_tokens or []
        self.keywords = keywords or []
        self.reflect_pass = reflect_pass
        self.reflect_feedback = reflect_feedback
        self.raise_on_chat = raise_on_chat
        self.raise_on_stream = raise_on_stream
        self.chat_calls: int = 0

    def chat(self, messages, **kw) -> str:
        self.chat_calls += 1
        if self.raise_on_chat:
            raise RuntimeError("chat boom")
        return self.chat_resp

    def chat_stream(self, messages, **kw):
        if self.raise_on_stream:
            raise RuntimeError("stream boom")
        for token in self.stream_tokens:
            yield token

    def extract_keywords(self, query: str, max_entities: int = 5) -> List[str]:
        return list(self.keywords)

    def reflect(self, question: str, answer: str, context: str) -> Dict[str, Any]:
        return {"pass": self.reflect_pass, "feedback": self.reflect_feedback}


class MockQdrant:
    def __init__(
        self,
        hits: Optional[List[dict]] = None,
        raise_search: bool = False,
        deleted: int = 0,
    ) -> None:
        self._hits = hits if hits is not None else [{"text": "vec", "score": 0.9, "source": "d.txt"}]
        self.raise_search = raise_search
        self.deleted = deleted

    def search(self, query: str, top_k: Optional[int] = None) -> List[dict]:
        if self.raise_search:
            raise RuntimeError("qdrant down")
        return list(self._hits)

    def delete_by_source(self, source: str) -> int:
        return self.deleted


class MockNeo4j:
    def __init__(self, rels: Optional[List[dict]] = None, deleted: int = 0) -> None:
        self._rels = rels if rels is not None else [{"head": "X", "rel": "RELATES_TO", "tail": "Y"}]
        self.deleted = deleted

    def search(self, entities, limit: int = 5) -> List[dict]:
        return list(self._rels)

    def delete_by_source(self, source: str) -> int:
        return self.deleted


class MockRag:
    def __init__(self, qdrant: Optional[MockQdrant] = None, neo4j: Optional[MockNeo4j] = None) -> None:
        self.qdrant = qdrant or MockQdrant()
        self.neo4j = neo4j or MockNeo4j()
