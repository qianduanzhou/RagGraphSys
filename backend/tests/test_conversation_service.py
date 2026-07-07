"""services.conversation_service 的测试（JSON 持久化的多对话/历史/文档清单）。"""
import pytest

from services.conversation_service import ConversationService


@pytest.fixture
def svc(tmp_path):
    return ConversationService(tmp_path / "conversations.json")


def test_create_returns_full_conversation(svc):
    conv = svc.create("alice")
    assert conv["owner"] == "alice"
    assert conv["title"] == "新对话"
    assert conv["messages"] == [] and conv["documents"] == []
    assert "id" in conv and "created_at" in conv


def test_list_returns_summaries_for_owner_only(svc):
    a1 = svc.create("alice")
    svc.create("bob")
    items = svc.list("alice")
    assert [c["id"] for c in items] == [a1["id"]]
    assert {"id", "title", "updated_at", "document_count", "preview"} <= set(items[0])


def test_get_is_owner_scoped(svc):
    c = svc.create("alice")
    assert svc.get("alice", c["id"])["id"] == c["id"]
    assert svc.get("bob", c["id"]) is None          # 越权
    assert svc.get("alice", "nope") is None         # 不存在


def test_rename(svc):
    c = svc.create("alice")
    assert svc.rename("alice", c["id"], "新标题")["title"] == "新标题"
    assert svc.rename("bob", c["id"], "x") is None


def test_append_message_autotitles_on_first_user(svc):
    c = svc.create("alice")
    svc.append_message("alice", c["id"], "user", "列出中国铁路广州局集团有限公司所有岗位")
    assert svc.get("alice", c["id"])["title"].startswith("列出中国铁路广州局")
    # 第二条 user 不再覆盖标题
    svc.append_message("alice", c["id"], "user", "再问一个")
    assert "再问" not in svc.get("alice", c["id"])["title"]
    # assistant 消息不触发改名
    c2 = svc.create("alice")
    svc.append_message("alice", c2["id"], "assistant", "回答")
    assert svc.get("alice", c2["id"])["title"] == "新对话"


def test_append_message_persists_in_order(svc):
    c = svc.create("alice")
    svc.append_message("alice", c["id"], "user", "q1")
    svc.append_message("alice", c["id"], "assistant", "a1")
    msgs = svc.get("alice", c["id"])["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "q1"


def test_clear_messages_empties_history_but_keeps_docs(svc):
    c = svc.create("alice")
    svc.append_message("alice", c["id"], "user", "q1")
    svc.append_message("alice", c["id"], "assistant", "a1")
    svc.add_document("alice", c["id"], {"name": "d.pdf", "chunks": 3, "at": 1})
    cleared = svc.clear_messages("alice", c["id"])
    assert cleared is not None
    assert cleared["messages"] == []
    # 文档清单不受影响
    assert [d["name"] for d in cleared["documents"]] == ["d.pdf"]
    # 越权返回 None
    assert svc.clear_messages("bob", c["id"]) is None


def test_documents_crud(svc):
    c = svc.create("alice")
    svc.add_document("alice", c["id"], {"name": "d.pdf", "chunks": 3, "at": 1})
    assert [d["name"] for d in svc.list_documents("alice", c["id"])] == ["d.pdf"]
    svc.remove_document("alice", c["id"], "d.pdf")
    assert svc.list_documents("alice", c["id"]) == []


def test_delete_returns_conv_for_store_cleanup(svc):
    c = svc.create("alice")
    svc.add_document("alice", c["id"], {"name": "d.pdf", "chunks": 3, "at": 1})
    gone = svc.delete("alice", c["id"])
    assert gone["id"] == c["id"] and gone["documents"][0]["name"] == "d.pdf"
    assert svc.get("alice", c["id"]) is None
    assert svc.delete("bob", c["id"]) is None


def test_persists_across_instances(svc, tmp_path):
    c = svc.create("alice")
    svc.append_message("alice", c["id"], "user", "hi")
    # 新实例从同一文件加载
    svc2 = ConversationService(tmp_path / "conversations.json")
    assert svc2.get("alice", c["id"])["messages"][0]["content"] == "hi"
