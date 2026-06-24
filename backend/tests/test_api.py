"""API 层测试：纯辅助函数 + FastAPI TestClient 集成。"""
import json

import pytest
from fastapi.testclient import TestClient

import main
from api import _sse, _summarize_update


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
def client():
    with TestClient(main.app) as c:
        yield c


def test_health_degraded(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert "qdrant" in body and "neo4j" in body


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
        def scan_all(self):
            return [
                {"id": 1, "payload": {"source": "a.txt", "created_at": 100}},
                {"id": 2, "payload": {"source": "a.txt", "created_at": 200}},
                {"id": 3, "payload": {"source": "b.md", "created_at": 50}},
            ]

    class _Neo4j:
        def count_entities(self):
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
        def delete_document(self, source):
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
        def delete_documents(self, sources):
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
        def delete_documents(self, sources):
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
        def ingest_text(self, text, source="manual"):
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
        def ingest_text(self, text, source="manual"):
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
        def ingest_text(self, text, source="manual"):
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
