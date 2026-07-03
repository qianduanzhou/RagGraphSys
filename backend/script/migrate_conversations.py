"""一次性迁移：把旧的全局文档（无 ``conversation_id`` 的分片）归入「导入的文档」对话。

背景：多对话特性上线后，分片按 ``(owner, conversation_id)`` 隔离检索；上线前入库的
分片没有 ``conversation_id``，在新模型下检索不到。本脚本为每个**有遗留分片**的用户
自动建一条「导入的文档」对话（已存在则复用），回填这些分片的 ``conversation_id``，
并在对话记录里登记文档清单。

幂等：重跑不会重复建对话，也不会对已回填的分片二次处理。

Neo4j 旧三元组（来源标记为 ``owner::source``、无对话维度）在新模型下不会被对话级
检索命中（前缀不匹配）；如需重建图谱，删除对应文档后重新上传即可。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Tuple

from rag.qdrant_store import QdrantStore
from services.conversation_service import ConversationService

IMPORTED_TITLE = "导入的文档"


def migrate(qdrant: QdrantStore, conversations: ConversationService) -> Dict[str, int]:
    """执行迁移，返回统计 {owners, points_backfilled, conversations_created}。"""
    points = qdrant.scan_all()

    # 按 owner 聚合「无 conversation_id」的遗留分片
    by_owner: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in points:
        payload = (p.get("payload") if isinstance(p, dict) else None) or {}
        if payload.get("conversation_id"):
            continue  # 已归属某对话，跳过
        owner = payload.get("owner")
        if owner:
            by_owner[owner].append(p)

    stats = {"owners": 0, "points_backfilled": 0, "conversations_created": 0}
    for owner, orphan_points in by_owner.items():
        conv_id, created = _find_or_create_imported(conversations, owner)
        if created:
            stats["conversations_created"] += 1

        # 回填 conversation_id
        ids = [p.get("id") for p in orphan_points if p.get("id")]
        stats["points_backfilled"] += qdrant.set_payload_by_ids(ids, {"conversation_id": conv_id})

        # 登记文档清单（按 source 聚合，已登记的跳过）
        existing = {d["name"] for d in conversations.list_documents(owner, conv_id)}
        agg: Dict[str, Dict[str, Any]] = {}
        for p in orphan_points:
            payload = (p.get("payload") if isinstance(p, dict) else None) or {}
            name = payload.get("source") or "unknown"
            if name in existing:
                continue
            at = int(payload.get("created_at") or 0)
            d = agg.setdefault(name, {"name": name, "chunks": 0, "at": at})
            d["chunks"] += 1
            d["at"] = max(d["at"], at)
        for d in agg.values():
            conversations.add_document(owner, conv_id, d)

        stats["owners"] += 1

    return stats


def _find_or_create_imported(
    conversations: ConversationService, owner: str
) -> Tuple[str, bool]:
    """返回 owner 的「导入的文档」对话 (id, created)；不存在则创建。"""
    for s in conversations.list(owner):
        if s.get("title") == IMPORTED_TITLE:
            return s["id"], False
    conv = conversations.create(owner, IMPORTED_TITLE)
    return conv["id"], True


def main() -> None:
    from core.config import get_settings
    from services.embedding_service import EmbeddingService

    settings = get_settings()
    embedding = EmbeddingService(settings)
    qdrant = QdrantStore(settings, embedding)
    conversations = ConversationService(settings.conversations_db_path)
    stats = migrate(qdrant, conversations)
    print("迁移完成：", stats)


if __name__ == "__main__":
    main()
