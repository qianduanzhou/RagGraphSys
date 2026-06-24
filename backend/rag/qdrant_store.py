"""Qdrant 向量库。

职责：
  * 确保集合存在，并按 embedding 维度创建 cosine 索引，
  * 写入文档（向量化后连同保留原文的 payload 一并存储），
  * 基于 cosine 相似度检索，返回 payload 和得分。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient, models

from core.config import Settings
from core.logger import get_logger
from services.embedding_service import EmbeddingService

logger = get_logger(__name__)


class QdrantStore:
    def __init__(self, settings: Settings, embedding_service: EmbeddingService):
        self.settings = settings
        self.embedding = embedding_service
        self.collection = settings.qdrant_collection
        self.client = QdrantClient(url=settings.qdrant_url)

    # ------------------------------------------------------------------ #
    # 集合管理
    # ------------------------------------------------------------------ #
    def ensure_collection(self) -> None:
        dim = self.embedding.dimension
        if self.client.collection_exists(self.collection):
            logger.info("Qdrant collection '%s' already exists", self.collection)
            return
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(
                size=dim,
                distance=models.Distance.COSINE,
            ),
        )
        logger.info("Created Qdrant collection '%s' (dim=%d, distance=cosine)", self.collection, dim)

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def upsert(
        self,
        texts: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """对 ``texts`` 进行向量化并连同 payload 一起写入。返回写入条数。"""
        if not texts:
            return 0
        metadatas = metadatas or [{} for _ in texts]
        if len(metadatas) != len(texts):
            raise ValueError("metadatas length must match texts length")

        vectors = self.embedding.embed_batch(texts)
        points = [
            models.PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={**meta, "text": text},
            )
            for text, vector, meta in zip(texts, vectors, metadatas)
        ]
        self.client.upsert(collection_name=self.collection, points=points)
        logger.info("Upserted %d points into '%s'", len(points), self.collection)
        return len(points)

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def search(self, query: str, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        """cosine 相似度检索；返回最匹配的 top_k 条结果及其 payload。"""
        limit = top_k or self.settings.qdrant_top_k
        query_vector = self.embedding.embed(query)
        response = self.client.query_points(
            collection_name=self.collection,
            query=query_vector,
            limit=limit,
            with_payload=True,
        )
        results = []
        for point in response.points:
            payload = point.payload or {}
            results.append(
                {
                    "text": payload.get("text", ""),
                    "score": float(point.score) if point.score is not None else 0.0,
                    "source": payload.get("source", "unknown"),
                    "payload": payload,
                }
            )
        logger.info("Qdrant search returned %d results for: %s", len(results), query[:80])
        return results

    def count(self) -> int:
        try:
            result = self.client.count(collection_name=self.collection, exact=True)
            return result.count
        except Exception as exc:  # noqa: BLE001
            logger.warning("count failed: %s", exc)
            return -1

    # ------------------------------------------------------------------ #
    # 删除
    # ------------------------------------------------------------------ #
    def delete_by_source(self, source: str) -> int:
        """删除某来源（文件名）文档的所有分片。返回 best-effort 删除条数。

        通过 payload 的 ``source`` 字段过滤删除；删除条数用前后 ``count`` 差值估算，
        若 count 不可用则返回 0（不影响实际删除是否生效）。
        """
        try:
            before = self.count()
            self.client.delete(
                collection_name=self.collection,
                points_selector=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="source",
                            match=models.MatchValue(value=source),
                        )
                    ]
                ),
            )
            after = self.count()
            removed = (before - after) if (before >= 0 and after >= 0) else 0
            logger.info(
                "Deleted ~%d points for source=%s (before=%s after=%s)",
                removed, source, before, after,
            )
            return removed
        except Exception as exc:  # noqa: BLE001
            logger.warning("delete_by_source failed: %s", exc)
            return 0

    def scan_all(self, batch_size: int = 256) -> List[Dict[str, Any]]:
        """扫描集合中所有点，返回 ``[{id, payload}]``。用于聚合文档列表。

        注意：``QdrantClient.scroll`` 返回的是 ``(records, next_offset)`` 元组而非
        可迭代的点序列，``next_offset`` 为 ``None`` 表示已到末尾。这里按 ``batch_size``
        翻页直到耗尽，避免一次性把整张表拉进内存。
        """
        try:
            points: List[Dict[str, Any]] = []
            offset = None
            while True:
                records, next_offset = self.client.scroll(
                    collection_name=self.collection,
                    limit=batch_size,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for record in records or []:
                    payload = getattr(record, "payload", None) or {}
                    points.append({"id": getattr(record, "id", None), "payload": payload})
                if next_offset is None:
                    break
                offset = next_offset
            return points
        except Exception as exc:  # noqa: BLE001
            logger.warning("scan_all failed: %s", exc)
            return []
