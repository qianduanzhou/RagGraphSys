"""API 层测试：纯辅助函数 + FastAPI TestClient 集成。"""
import json

import pytest
from fastapi.testclient import TestClient

import main
from api import _sse, _summarize_update
from services.auth_service import AuthService


# --------------------------------------------------------------------------- #
# 纯辅助函数
# --------------------------------------------------------------------------- #
def test_sse_format():
    assert _sse({"type": "delta", "text": "x"}) == 'data: {"type": "delta", "text": "x"}\n\n'


def test_summarize_router():
    assert _summarize_update("router_node", {"needs_rag": True, "used_rag": True}) == {
        "needs_rag": True, "used_rag": True,
    }


def test_summarize_qdrant_hits():
    assert _summarize_update("qdrant_node", {"qdrant_results": [{}, {}, {}]}) == {"hits": 3}


def test_summarize_neo4j_hits():
    assert _summarize_update("neo4j_node", {"neo4j_results": [{}]}) == {"hits": 1}


def test_summarize_merge_includes_sources():
    src = [{"type": "qdrant", "content": "c"}]
    out = _summarize_update("merge_node", {"sources": src, "used_rag": True, "context": "x"})
    assert out == {"sources": src, "used_rag": True}


def test_summarize_llm_iterations():
    assert _summarize_update("llm_node", {"answer": "x", "iterations": 2}) == {"iterations": 2}


def test_summarize_unknown_node_is_empty():
    assert _summarize_update("reflection_node", {"reflection_passed": True}) == {}


def test_summarize_non_dict_is_empty():
    assert _summarize_update("router_node", "nope") == {}


# --------------------------------------------------------------------------- #
# TestClient 集成（应用以降级模式启动；graph 被替换为 mock）
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path):
    from services.conversation_service import ConversationService
    with TestClient(main.app) as c:
        auth = AuthService(tmp_path / "users.json")
        session = auth.register("testuser", "password123!")
        main.app.state.auth = auth
        main.app.state.conversations = ConversationService(tmp_path / "conversations.json")
        c.headers.update({"Authorization": f"Bearer {session['token']}"})
        yield c


def _bob_headers(client) -> dict:
    """注册第二用户 bob 并返回其鉴权头（与 client 共享同一 auth/conversations 实例）。"""
    auth = main.app.state.auth
    session = auth.register("bob12345", "password123!")
    return {"Authorization": f"Bearer {session['token']}"}


def test_health_degraded(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert "qdrant" in body and "neo4j" in body


def test_docs_requires_auth(client):
    r = client.get("/api/docs", headers={"Authorization": ""})
    assert r.status_code == 401


def test_chat_with_mock_graph(client):
    class _MockGraph:
        def invoke(self, state):
            return {"answer": "mock-answer", "sources": [], "used_rag": False, "iterations": 1}

    main.app.state.graph = _MockGraph()
    r = client.post("/api/chat", json={"message": "hi", "history": []})
    assert r.status_code == 200
    assert r.json()["answer"] == "mock-answer"


def test_chat_stream_sse_frames(client):
    """通过 TestClient 的端到端 SSE：节点事件 + 实时 delta + done。"""
    class _MockStreamGraph:
        async def astream(self, initial, stream_mode=("updates",)):
            # 模拟真实图的多模式流：(mode, payload) 元组
            yield ("updates", {"router_node": {"needs_rag": True, "used_rag": True}})
            yield ("updates", {"merge_node": {"sources": [{"type": "qdrant", "content": "c", "score": 0.9, "source": "d"}], "used_rag": True, "context": "x"}})
            yield ("custom", {"type": "delta", "text": "你"})
            yield ("custom", {"type": "delta", "text": "好"})
            yield ("updates", {"llm_node": {"answer": "你好", "iterations": 1}})

    main.app.state.graph = _MockStreamGraph()

    with client.stream("POST", "/api/chat/stream", json={"message": "q", "history": []}) as r:
        body = "".join(r.iter_text())

    frames = []
    for block in body.split("\n\n"):
        data_lines = [ln for ln in block.split("\n") if ln.startswith("data:")]
        if data_lines:
            frames.append(json.loads(data_lines[0][len("data:"):].strip()))

    types = [(f["type"], f.get("node")) for f in frames]
    assert ("node", "router_node") in types
    assert ("node", "merge_node") in types
    assert ("node", "llm_node") in types
    assert any(f["type"] == "delta" and f.get("text") == "你" for f in frames)
    assert any(f["type"] == "delta" and f.get("text") == "好" for f in frames)
    assert frames[-1]["type"] == "done"

    # merge 节点事件暴露了真实 sources，用于提前渲染徽章
    merge = next(f for f in frames if f.get("node") == "merge_node")
    assert merge["update"]["sources"][0]["type"] == "qdrant"


def test_chat_stream_rejects_when_uninitialised(client):
    main.app.state.graph = None
    r = client.post("/api/chat/stream", json={"message": "q", "history": []})
    assert r.status_code == 503


def test_list_docs_aggregates_by_source(client):
    """已入库文档按 source 聚合，分片数累加、时间戳取最大值。"""
    class _Qdrant:
        def scan_all(self, owner=None):
            return [
                {"id": 1, "payload": {"source": "a.txt", "created_at": 100}},
                {"id": 2, "payload": {"source": "a.txt", "created_at": 200}},
                {"id": 3, "payload": {"source": "b.md", "created_at": 50}},
            ]

    class _Neo4j:
        def count_entities(self, owner=None):
            return 7

    class _Rag:
        qdrant = _Qdrant()
        neo4j = _Neo4j()

    main.app.state.rag = _Rag()
    r = client.get("/api/docs")
    assert r.status_code == 200
    docs = {d["name"]: d for d in r.json()}
    assert docs["a.txt"]["chunks"] == 2
    assert docs["a.txt"]["at"] == 200  # 取最大时间戳
    assert docs["b.md"]["chunks"] == 1


def test_ingest_files_rejects_empty(client):
    class _Rag:
        pass

    main.app.state.rag = _Rag()  # rag 有效，但未提供任何文件 -> 400
    r = client.post("/api/ingest/files", files=[], data={})
    assert r.status_code == 400


def test_delete_doc_endpoint(client):
    class _Rag:
        def delete_document(self, source, owner=None):
            return {"source": source, "chunks": 5, "relations": 2}

    main.app.state.rag = _Rag()
    r = client.post("/api/docs/delete", json={"source": "notes.txt"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"source": "notes.txt", "chunks": 5, "relations": 2}


def test_delete_doc_rejects_empty_source(client):
    main.app.state.rag = type("_Rag", (), {})()
    r = client.post("/api/docs/delete", json={"source": ""})
    assert r.status_code == 422  # Pydantic 校验 min_length=1


# --------------------------------------------------------------------------- #
# 批量删除 /api/docs/delete/batch
# --------------------------------------------------------------------------- #
def test_delete_docs_batch_success(client):
    """全成功：status=ok，deleted 计数正确，逐项返回 chunks/relations。"""
    class _Rag:
        def delete_documents(self, sources, owner=None):
            return {
                "status": "ok",
                "deleted": len(sources),
                "failed": 0,
                "results": [
                    {"source": s, "chunks": 3, "relations": 1, "ok": True}
                    for s in sources
                ],
            }
    main.app.state.rag = _Rag()
    r = client.post("/api/docs/delete/batch", json={"sources": ["a.md", "b.txt"]})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["deleted"] == 2
    assert body["failed"] == 0
    assert [it["source"] for it in body["results"]] == ["a.md", "b.txt"]
    assert all(it["ok"] for it in body["results"])


def test_delete_docs_batch_partial_failure(client):
    """部分失败：status=partial，整批不中断，失败项带 error。"""
    class _Rag:
        def delete_documents(self, sources, owner=None):
            results = []
            for s in sources:
                if s == "bad.md":
                    results.append({"source": s, "ok": False, "error": "boom"})
                else:
                    results.append({"source": s, "chunks": 1, "relations": 0, "ok": True})
            return {
                "status": "partial",
                "deleted": sum(1 for x in results if x["ok"]),
                "failed": sum(1 for x in results if not x["ok"]),
                "results": results,
            }
    main.app.state.rag = _Rag()
    r = client.post(
        "/api/docs/delete/batch",
        json={"sources": ["good.md", "bad.md", "ok.txt"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "partial"
    assert body["deleted"] == 2
    assert body["failed"] == 1
    bad = [it for it in body["results"] if not it["ok"]]
    assert len(bad) == 1 and bad[0]["source"] == "bad.md" and bad[0]["error"] == "boom"


def test_delete_docs_batch_rejects_empty(client):
    """空 sources 列表 → 422（Pydantic min_length=1）。"""
    main.app.state.rag = type("_Rag", (), {})()
    r = client.post("/api/docs/delete/batch", json={"sources": []})
    assert r.status_code == 422


def test_ingest_file_parses_code_file(client):
    """上传 .py 代码文件：经解析器提取文本后入库。"""
    captured = {}

    class _Rag:
        def ingest_text(self, text, source="manual", owner=None):
            captured["text"] = text
            captured["source"] = source
            return {"chunks": 1, "triples": 0}

    main.app.state.rag = _Rag()
    r = client.post(
        "/api/ingest/file",
        files={"file": ("main.py", b"print('hello')", "text/plain")},
    )
    assert r.status_code == 200
    assert captured["text"] == "print('hello')"
    assert captured["source"] == "main.py"


def test_ingest_file_rejects_unsupported_type(client):
    main.app.state.rag = type("_Rag", (), {})()
    r = client.post(
        "/api/ingest/file",
        files={"file": ("setup.exe", b"MZ", "application/octet-stream")},
    )
    assert r.status_code == 415


def test_ingest_files_accepts_multiple_types(client):
    """批量上传混合类型（代码 + 文本）：均解析入库。"""
    sources = []

    class _Rag:
        def ingest_text(self, text, source="manual", owner=None):
            sources.append((source, text))
            return {"chunks": 1, "triples": 0}

    main.app.state.rag = _Rag()
    r = client.post(
        "/api/ingest/files",
        files=[
            ("files", ("a.py", b"x = 1", "text/plain")),
            ("files", ("b.md", b"# Title", "text/markdown")),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["succeeded"] == 2
    assert body["failed"] == 0
    names = {s for s, _ in sources}
    assert names == {"a.py", "b.md"}


def test_ingest_files_unpacks_zip_members(client):
    """上传 zip：内部成员按相对路径 source 逐个入库。"""
    import io as _io
    import zipfile
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.md", b"# hi")
        zf.writestr("docs/note.txt", b"a note")
    zip_bytes = buf.getvalue()

    sources = []

    class _Rag:
        def ingest_text(self, text, source="manual", owner=None):
            sources.append((source, text))
            return {"chunks": 1, "triples": 0}

    main.app.state.rag = _Rag()
    r = client.post(
        "/api/ingest/files",
        files=[("files", ("bundle.zip", zip_bytes, "application/zip"))],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["succeeded"] == 2
    names = {s for s, _ in sources}
    assert names == {"readme.md", "docs/note.txt"}


def test_ingest_files_corrupted_zip_recorded_as_failure(client):
    """损坏 zip 记为一条失败结果，不返回 400。"""
    main.app.state.rag = type("_Rag", (), {})()
    r = client.post(
        "/api/ingest/files",
        files=[("files", ("bad.zip", b"not a zip", "application/zip"))],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["failed"] == 1
    assert body["succeeded"] == 0
    assert body["files"][0]["ok"] is False


# ------------------------------------------------------------------ #
# 多智能体模式（mode="multi"）
# ------------------------------------------------------------------ #
def test_summarize_rag_agent_includes_answer():
    out = _summarize_update("rag_agent_node", {
        "rag_agent_answer": "RA", "rag_agent_sources": [{"type": "qdrant", "content": "c"}], "used_rag": True,
    })
    assert out == {"answer": "RA", "sources": [{"type": "qdrant", "content": "c"}], "hits": 1, "used_rag": True}


def test_summarize_web_agent_includes_answer():
    out = _summarize_update("web_agent_node", {
        "web_agent_answer": "WA", "web_sources": [{"type": "web", "url": "http://x"}], "used_web": True,
    })
    assert out == {"answer": "WA", "sources": [{"type": "web", "url": "http://x"}], "hits": 1, "used_web": True}


def test_summarize_dispatch_is_empty():
    assert _summarize_update("dispatch_node", {}) == {}


def test_summarize_integration_iterations():
    assert _summarize_update("integration_node", {"answer": "x", "iterations": 1}) == {"iterations": 1}


def test_chat_multi_routes_to_multi_graph(client):
    class _MockMulti:
        def invoke(self, state):
            return {"answer": "multi-answer", "sources": [], "used_rag": True, "iterations": 1}

    main.app.state.multi_agent_graph = _MockMulti()
    r = client.post("/api/chat", json={"message": "hi", "history": [], "mode": "multi"})
    assert r.status_code == 200
    assert r.json()["answer"] == "multi-answer"


def test_chat_default_mode_is_rag(client):
    """不传 mode 时默认 rag，走原 graph。"""
    class _MockGraph:
        def invoke(self, state):
            return {"answer": "rag-answer", "sources": [], "used_rag": False, "iterations": 1}

    main.app.state.graph = _MockGraph()
    r = client.post("/api/chat", json={"message": "hi", "history": []})
    assert r.status_code == 200
    assert r.json()["answer"] == "rag-answer"


def test_chat_multi_503_when_graph_missing(client, monkeypatch):
    # 用 monkeypatch 注入，用例结束自动还原，避免污染 app.state 全局单例。
    monkeypatch.setattr(main.app.state, "multi_agent_graph", None)
    r = client.post("/api/chat", json={"message": "hi", "history": [], "mode": "multi"})
    assert r.status_code == 503


def test_health_includes_web_search(client):
    class _Web:
        available = True

    main.app.state.web = _Web()
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["web_search"] is True


def test_chat_stream_multi_emits_agent_nodes(client):
    class _MockMultiStream:
        async def astream(self, initial, stream_mode=("updates",)):
            yield ("updates", {"dispatch_node": {}})
            yield ("updates", {"rag_agent_node": {"rag_agent_answer": "RA", "rag_agent_sources": [], "used_rag": True}})
            yield ("updates", {"web_agent_node": {"web_agent_answer": "WA", "web_sources": [], "used_web": False}})
            yield ("custom", {"type": "delta", "text": "整"})
            yield ("custom", {"type": "delta", "text": "合"})
            yield ("updates", {"integration_node": {"answer": "整合", "iterations": 1}})

    main.app.state.multi_agent_graph = _MockMultiStream()
    with client.stream("POST", "/api/chat/stream", json={"message": "q", "history": [], "mode": "multi"}) as r:
        body = "".join(r.iter_text())
    frames = []
    for block in body.split("\n\n"):
        data_lines = [ln for ln in block.split("\n") if ln.startswith("data:")]
        if data_lines:
            frames.append(json.loads(data_lines[0][len("data:"):].strip()))
    nodes = [f.get("node") for f in frames if f["type"] == "node"]
    assert "rag_agent_node" in nodes and "web_agent_node" in nodes and "integration_node" in nodes
    rag = next(f for f in frames if f.get("node") == "rag_agent_node")
    assert rag["update"]["answer"] == "RA"
    assert frames[-1]["type"] == "done"


# --------------------------------------------------------------------------- #
# 对话管理（CRUD / 文档 / 对话级 chat）
# --------------------------------------------------------------------------- #
def test_conversation_crud_flow(client):
    r = client.post("/api/conversations", json={})
    assert r.status_code == 200
    cid = r.json()["id"]
    assert r.json()["title"] == "新对话"
    # 列表
    assert client.get("/api/conversations").json()[0]["id"] == cid
    # 改名
    assert client.patch(f"/api/conversations/{cid}", json={"title": "我的对话"}).json()["title"] == "我的对话"
    # 详情
    assert client.get(f"/api/conversations/{cid}").json()["title"] == "我的对话"
    # 不存在
    assert client.get("/api/conversations/nope").status_code == 404


def test_conversation_isolation_between_users(client):
    cid = client.post("/api/conversations", json={}).json()["id"]
    bob = _bob_headers(client)
    # bob 看不到 testuser 的对话
    assert client.get(f"/api/conversations/{cid}", headers=bob).status_code == 404
    assert client.get("/api/conversations", headers=bob).json() == []
    # bob 改名/删除 testuser 的对话也 404
    assert client.patch(f"/api/conversations/{cid}", json={"title": "x"}, headers=bob).status_code == 404
    assert client.delete(f"/api/conversations/{cid}", headers=bob).status_code == 404


def test_conversation_requires_auth(client):
    r = client.post("/api/conversations", json={}, headers={"Authorization": ""})
    assert r.status_code == 401


def test_delete_conversation_cleans_stores(client):
    class _Rag:
        def delete_conversation(self, owner, conversation_id):
            return {"chunks": 5, "relations": 2}

    main.app.state.rag = _Rag()
    cid = client.post("/api/conversations", json={}).json()["id"]
    r = client.delete(f"/api/conversations/{cid}")
    assert r.status_code == 200
    assert r.json() == {"id": cid, "chunks": 5, "relations": 2}
    # 已删，再取 404
    assert client.get(f"/api/conversations/{cid}").status_code == 404


def test_upload_conversation_documents_ingests_with_conv_id(client):
    captured = {}

    class _Rag:
        def ingest_text(self, text, source="manual", owner=None, conversation_id=None):
            captured["conv"] = conversation_id
            return {"chunks": 2, "triples": 0}

    main.app.state.rag = _Rag()
    cid = client.post("/api/conversations", json={}).json()["id"]
    r = client.post(
        f"/api/conversations/{cid}/documents",
        files={"files": ("a.py", b"x = 1", "text/plain")},
    )
    assert r.status_code == 200
    assert r.json()["succeeded"] == 1
    assert captured["conv"] == cid
    # 文档登记到对话清单
    docs = client.get(f"/api/conversations/{cid}").json()["documents"]
    assert [d["name"] for d in docs] == ["a.py"]


def test_delete_conversation_document(client):
    class _Rag:
        def delete_document(self, source, owner=None, conversation_id=None):
            assert conversation_id is not None
            return {"source": source, "chunks": 3, "relations": 1}

    main.app.state.rag = _Rag()
    cid = client.post("/api/conversations", json={}).json()["id"]
    main.app.state.conversations.add_document("testuser", cid, {"name": "d.md", "chunks": 3, "at": 1})
    r = client.request("DELETE", f"/api/conversations/{cid}/documents", json={"source": "d.md"})
    assert r.status_code == 200
    assert r.json()["chunks"] == 3
    assert client.get(f"/api/conversations/{cid}").json()["documents"] == []


def test_conversation_chat_accumulates_history(client, monkeypatch):
    import types as _types
    cid = client.post("/api/conversations", json={}).json()["id"]
    captured = {"hist_lens": []}

    class _Graph:
        def invoke(self, state):
            captured["hist_lens"].append(len(state.get("history", [])))
            captured["conv"] = state.get("conversation_id")
            return {"answer": "A", "sources": [], "used_rag": False, "iterations": 1}

    monkeypatch.setattr(main.app.state, "graph", _Graph())
    r1 = client.post(f"/api/conversations/{cid}/chat", json={"message": "你好"})
    assert r1.status_code == 200 and r1.json()["answer"] == "A"
    client.post(f"/api/conversations/{cid}/chat", json={"message": "再问"})
    # 第1轮历史0条；第2轮历史2条（上一轮 user+assistant）
    assert captured["hist_lens"] == [0, 2]
    assert captured["conv"] == cid
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    # 首条 user 触发自动改名
    assert client.get(f"/api/conversations/{cid}").json()["title"].startswith("你好")


def test_conversation_chat_other_user_404(client):
    bob = _bob_headers(client)
    cid = client.post("/api/conversations", json={}).json()["id"]
    assert client.post(f"/api/conversations/{cid}/chat", json={"message": "x"}, headers=bob).status_code == 404


def test_conversation_chat_stream_writes_back(client, monkeypatch):
    cid = client.post("/api/conversations", json={}).json()["id"]

    class _StreamGraph:
        async def astream(self, initial, stream_mode=("updates",)):
            yield ("custom", {"type": "delta", "text": "答"})
            yield ("custom", {"type": "delta", "text": "案"})

    monkeypatch.setattr(main.app.state, "graph", _StreamGraph())
    with client.stream("POST", f"/api/conversations/{cid}/chat/stream", json={"message": "问"}) as r:
        body = "".join(r.iter_text())
    assert '"type": "done"' in body or '"type":"done"' in body
    # 流结束后写回对话记录
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "答案"
