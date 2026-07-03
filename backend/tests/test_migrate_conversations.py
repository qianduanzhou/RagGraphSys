"""迁移脚本测试：旧全局文档（无 conversation_id）归入「导入的文档」对话。"""
import pytest

from rag.qdrant_store import QdrantStore
from services.conversation_service import ConversationService
from script.migrate_conversations import migrate
from tests.conftest import FakeEmbedding, FakeQdrantClient


@pytest.fixture
def store(settings):
    s = QdrantStore(settings, FakeEmbedding())
    s.client = FakeQdrantClient()
    return s


def test_migrate_backfills_orphans_and_creates_conversation(store, tmp_path):
    # alice 的遗留分片（无 conversation_id）
    store.upsert(
        ["a", "b"],
        metadatas=[
            {"source": "d1.pdf", "owner": "alice", "created_at": 100, "chunk_index": 0},
            {"source": "d1.pdf", "owner": "alice", "created_at": 100, "chunk_index": 1},
        ],
    )
    # bob 的遗留分片
    store.upsert(["c"], metadatas=[{"source": "d2.md", "owner": "bob", "created_at": 200, "chunk_index": 0}])
    # 已有 conversation_id 的不被处理
    store.upsert(
        ["x"],
        metadatas=[{"source": "d3.txt", "owner": "alice", "created_at": 1, "conversation_id": "existing", "chunk_index": 0}],
    )

    convs = ConversationService(tmp_path / "conversations.json")
    stats = migrate(store, convs)

    assert stats["owners"] == 2
    assert stats["points_backfilled"] == 3
    assert stats["conversations_created"] == 2

    # alice 的导入对话登记了 d1.pdf（2 chunks）
    alice_imported = [c for c in convs.list("alice") if c["title"] == "导入的文档"][0]
    docs = {d["name"]: d["chunks"] for d in convs.list_documents("alice", alice_imported["id"])}
    assert docs == {"d1.pdf": 2}

    # 遗留分片已回填为导入对话的 conversation_id
    backfilled = [
        p.payload.get("conversation_id")
        for p in store.client.points
        if p.payload.get("source") == "d1.pdf"
    ]
    assert all(c == alice_imported["id"] for c in backfilled)

    # 已有 conversation_id 的未被改动
    existing = [p for p in store.client.points if p.payload.get("source") == "d3.txt"][0]
    assert existing.payload["conversation_id"] == "existing"


def test_migrate_is_idempotent(store, tmp_path):
    store.upsert(["a"], metadatas=[{"source": "d.pdf", "owner": "alice", "created_at": 1, "chunk_index": 0}])
    convs = ConversationService(tmp_path / "conversations.json")
    first = migrate(store, convs)
    assert first["conversations_created"] == 1

    second = migrate(store, convs)
    # 第二次：所有分片已有 conversation_id，无遗留可处理
    assert second == {"owners": 0, "points_backfilled": 0, "conversations_created": 0}
    # 仍只有一条导入对话
    assert len([c for c in convs.list("alice") if c["title"] == "导入的文档"]) == 1


def test_migrate_skips_orphans_without_owner(store, tmp_path):
    # 无 owner 的孤儿分片无法归属，跳过
    store.upsert(["a"], metadatas=[{"source": "d.pdf", "created_at": 1, "chunk_index": 0}])
    convs = ConversationService(tmp_path / "conversations.json")
    stats = migrate(store, convs)
    assert stats == {"owners": 0, "points_backfilled": 0, "conversations_created": 0}
