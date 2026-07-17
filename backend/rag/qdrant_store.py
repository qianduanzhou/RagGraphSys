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
            raise ValueError("元数据数量必须与文本数量一致")

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

    @staticmethod
    def _payload_filter(**matches: Optional[str]) -> Optional[models.Filter]:
        conditions = [
            models.FieldCondition(key=key, match=models.MatchValue(value=value))
            for key, value in matches.items()
            if value is not None
        ]
        return models.Filter(must=conditions) if conditions else None

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        owner: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """cosine 相似度检索；返回最匹配的 top_k 条结果及其 payload。

        ``conversation_id`` 非 None 时按对话过滤（多对话隔离）；为 None 则不过滤
        （兼容旧的单对话路径与既有测试）。
        """
        limit = top_k or self.settings.qdrant_top_k
        query_vector = self.embedding.embed(query)
        response = self.client.query_points(
            collection_name=self.collection,
            query=query_vector,
            limit=limit,
            with_payload=True,
            query_filter=self._payload_filter(owner=owner, conversation_id=conversation_id),
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

    def count(self, owner: Optional[str] = None) -> int:
        try:
            result = self.client.count(
                collection_name=self.collection,
                exact=True,
                count_filter=self._payload_filter(owner=owner),
            )
            return result.count
        except Exception as exc:  # noqa: BLE001
            logger.warning("count failed: %s", exc)
            return -1

    # ------------------------------------------------------------------ #
    # 删除
    # ------------------------------------------------------------------ #
    def delete_by_source(
        self,
        source: str,
        owner: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> int:
        """删除某来源（文件名）文档的所有分片。返回 best-effort 删除条数。

        通过 payload 的 ``source`` 字段过滤删除；删除条数用前后 ``count`` 差值估算，
        若 count 不可用则返回 0（不影响实际删除是否生效）。``conversation_id`` 非
        None 时仅在指定对话范围内删除（同名文件跨对话不串）。
        """
        try:
            before = self.count(owner=owner)
            self.client.delete(
                collection_name=self.collection,
                points_selector=self._payload_filter(
                    source=source, owner=owner, conversation_id=conversation_id
                ),
            )
            after = self.count(owner=owner)
            removed = (before - after) if (before >= 0 and after >= 0) else 0
            logger.info(
                "Deleted ~%d points for source=%s (before=%s after=%s)",
                removed, source, before, after,
            )
            return removed
        except Exception as exc:  # noqa: BLE001
            logger.warning("delete_by_source failed: %s", exc)
            return 0

    def delete_by_conversation(self, owner: Optional[str], conversation_id: str) -> int:
        """删除某对话的全部分片。返回 best-effort 删除条数。

        用于删除整个对话时清理其知识库；按 ``(owner, conversation_id)`` 过滤。
        """
        try:
            before = self.count(owner=owner)
            self.client.delete(
                collection_name=self.collection,
                points_selector=self._payload_filter(owner=owner, conversation_id=conversation_id),
            )
            after = self.count(owner=owner)
            removed = (before - after) if (before >= 0 and after >= 0) else 0
            logger.info("Deleted ~%d points for conversation=%s", removed, conversation_id)
            return removed
        except Exception as exc:  # noqa: BLE001
            logger.warning("delete_by_conversation failed: %s", exc)
            return 0

    def set_payload_by_ids(self, ids: List[str], payload: Dict[str, Any]) -> int:
        """按 point id 批量更新 payload（迁移脚本回填 ``conversation_id`` 用）。返回更新条数。"""
        if not ids:
            return 0
        try:
            self.client.set_payload(
                collection_name=self.collection,
                payload=payload,
                points=list(ids),
            )
            return len(ids)
        except Exception as exc:  # noqa: BLE001
            logger.warning("set_payload_by_ids failed: %s", exc)
            return 0

    def scroll_by_source(
        self,
        source: str,
        owner: Optional[str] = None,
        conversation_id: Optional[str] = None,
        limit: Optional[int] = None,
        batch_size: int = 256,
    ) -> List[Dict[str, Any]]:
        """拉取某个来源文档的**全部分片**，按 ``chunk_index`` 排序后返回。

        用于聚合/穷举型查询（如「列出所有岗位」）：这类查询需要整文档内容，
        top-k 语义检索只能返回部分分片；这里改为按 source 过滤拉取全部。

        返回与 :meth:`search` 同形的 dict，但 ``score=None``（无真实相似度）——
        :func:`rag.rag_service.merge_results` 会对 ``score is None`` 放行阈值过滤。
        """
        try:
            points: List[Dict[str, Any]] = []
            offset = None
            scroll_filter = self._payload_filter(source=source, owner=owner, conversation_id=conversation_id)
            while True:
                records, next_offset = self.client.scroll(
                    collection_name=self.collection,
                    limit=batch_size,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                    scroll_filter=scroll_filter,
                )
                for record in records or []:
                    payload = getattr(record, "payload", None) or {}
                    points.append({"id": getattr(record, "id", None), "payload": payload})
                if next_offset is None:
                    break
                offset = next_offset

            # 按 chunk_index 稳定排序（ingest_text 必写该字段；缺失退 0、保持插入序）
            points.sort(key=lambda p: int((p["payload"] or {}).get("chunk_index") or 0))

            if limit is not None:
                points = points[:limit]

            return [
                {
                    "text": (p["payload"] or {}).get("text", ""),
                    "score": None,
                    "source": (p["payload"] or {}).get("source", "unknown"),
                    "payload": p["payload"],
                }
                for p in points
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("scroll_by_source failed: %s", exc)
            return []

    def list_sources(
        self,
        owner: Optional[str] = None,
        conversation_id: Optional[str] = None,
        batch_size: int = 256,
    ) -> List[str]:
        """列出指定范围内已有分片的文档来源，用于判断当前对话是否只有一个文档。"""
        try:
            sources: set[str] = set()
            offset = None
            while True:
                records, next_offset = self.client.scroll(
                    collection_name=self.collection,
                    limit=batch_size,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                    scroll_filter=self._payload_filter(owner=owner, conversation_id=conversation_id),
                )
                for record in records or []:
                    payload = getattr(record, "payload", None) or {}
                    source = payload.get("source")
                    if source:
                        sources.add(str(source))
                if next_offset is None:
                    break
                offset = next_offset
            return sorted(sources)
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_sources failed: %s", exc)
            return []

    def scan_all(self, batch_size: int = 256, owner: Optional[str] = None) -> List[Dict[str, Any]]:
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
                    scroll_filter=self._payload_filter(owner=owner),
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
