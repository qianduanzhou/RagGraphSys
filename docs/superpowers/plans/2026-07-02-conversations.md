# 多对话与每对话文档/记忆 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 RagGraphSys 增加多对话能力——每个用户可建多个对话，每个对话有独立的文档列表（文档归属对话）与独立的聊天记忆（后端持久化），问答只检索当前对话的文档。

**Architecture:** 新增 `ConversationService`（JSON 持久化，镜像 `auth_service`）管理对话/历史/文档清单；Qdrant 与 Neo4j 的 payload 增加 `conversation_id`，所有检索按 `(owner, conversation_id)` 过滤；新增对话级 REST/SSE 端点，chat 改由后端从对话记录加载历史并在生成后写回；前端侧栏改两段式（对话列表 + 当前对话文档），消息改从后端拉取。

**Tech Stack:** Python 3 / FastAPI / LangGraph / Qdrant / Neo4j（后端）；React + TypeScript + Vite + vitest（前端）。

## Global Constraints

- 项目根：`D:\project\customer\AI\RagGraphSys`；后端在 `backend/`（用 `backend/venv/Scripts/python.exe` 跑 pytest）。
- **`conversation_id` 一律 Optional**：`None` 表示「不按对话过滤」（兼容旧路径与既有 211 个测试，无需改动它们）。新端点始终传真实 conv_id。
- 持久化范式统一为 JSON + `threading.Lock` + 临时文件原子替换（见 `backend/services/auth_service.py`）。
- TDD：每个任务先写失败测试 → 跑红 → 实现 → 跑绿 → 提交。
- 命名/文案为中文（与现有一致）；不引入新重依赖。
- 完整设计依据：`docs/superpowers/specs/2026-07-02-conversations-design.md`。

## File Structure

**后端（Phase A）**
- Create `backend/services/conversation_service.py` — `ConversationService`（对话/历史/文档清单 CRUD）。
- Create `backend/tests/test_conversation_service.py`。
- Create `backend/script/migrate_conversations.py` — 一次性迁移旧全局文档到「导入的文档」对话。
- Modify `backend/core/config.py` — 加 `conversations_db_path`。
- Modify `backend/main.py` — 实例化 `ConversationService` 挂 `app.state.conversations`。
- Modify `backend/rag/qdrant_store.py` — `upsert`/`search`/`scroll_by_source`/`delete_by_source` 加 `conversation_id`；新增 `delete_by_conversation`。
- Modify `backend/rag/neo4j_store.py` — `add_knowledge`/`search`/`delete_by_source` 加 `conversation_id`；新增 `delete_by_conversation`。
- Modify `backend/rag/rag_service.py` — `ingest_text`/`retrieve`/`build_context`/`resolve_vector_hits`/`delete_document(s)` 透传 `conversation_id`。
- Modify `backend/nodes.py` — `GraphState.conversation_id`；`qdrant_node`/`neo4j_node`/`merge` 传 conv_id。
- Modify `backend/multiagent/nodes.py` — `MultiAgentState.conversation_id`；`rag_agent` 传 conv_id。
- Modify `backend/api.py` — 对话 CRUD + 文档端点 + 对话级 chat；`_summarize_update` 不变。
- Modify `backend/tests/conftest.py` — `MockQdrant`/`MockNeo4j`/`MockRag`/`FakeRag` 的方法签名加 `conversation_id=None`；`FakeQdrantClient` 的 filter 匹配支持 `conversation_id`。

**前端（Phase B）**
- Modify `frontend/src/types.ts` — `Conversation`/`ConversationSummary`/`ConversationDoc`。
- Modify `frontend/src/api/client.ts` — 对话 API 方法；`chatStream(conversationId, message, mode)` 不再发 history。
- Modify `frontend/src/App.tsx` — 对话状态 + 列表/切换/新建/重命名/删除；消息与文档按当前对话拉取。
- Modify `frontend/src/components/Sidebar.tsx` — 两段式（对话列表 + 当前对话文档）。
- Create `frontend/src/components/ConversationList.tsx`（+ `.css`）— 对话列表 UI。
- Remove `frontend/src/chat-history.ts` 及其测试 `chat-history.test.ts`；`App.tsx` 移除引用。
- 调整 `frontend/src/App.css` 适配新侧栏结构。

---

# Phase A — 后端

## Task A1: `ConversationService`（持久化 + CRUD + 历史 + 文档清单）

**Files:**
- Modify: `backend/core/config.py`（加 `conversations_db_path`）
- Create: `backend/services/conversation_service.py`
- Create: `backend/tests/test_conversation_service.py`

**Interfaces (Produces):**
```python
class ConversationService:
    def __init__(self, db_path: str | Path): ...
    def create(self, owner: str, title: str | None = None) -> dict          # 返回完整 conv dict
    def list(self, owner: str) -> list[dict]                                 # 摘要：id/title/updated_at/document_count/preview
    def get(self, owner: str, conv_id: str) -> dict | None                   # 含 messages + documents；越权/不存在返回 None
    def rename(self, owner: str, conv_id: str, title: str) -> dict | None
    def delete(self, owner: str, conv_id: str) -> dict | None                # 返回被删 conv（含 documents，供调用方清库）；越权返回 None
    def append_message(self, owner: str, conv_id: str, role: str, content: str) -> dict | None  # 首条 user 触发自动 title
    def list_documents(self, owner: str, conv_id: str) -> list[dict]
    def add_document(self, owner: str, conv_id: str, doc: dict) -> None      # doc={name,chunks,at}
    def remove_document(self, owner: str, conv_id: str, name: str) -> None
```
conv dict 形状：`{id, owner, title, created_at, updated_at, documents:[{name,chunks,at}], messages:[{role,content,at}]}`。

- [ ] **Step 1: 加配置项**

`backend/core/config.py`，在 `auth_db_path` 旁加：
```python
    conversations_db_path: str = str(BASE_DIR / "data" / "conversations.json")
```

- [ ] **Step 2: 写失败测试** `backend/tests/test_conversation_service.py`

```python
import time
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
    # 第二条不再覆盖标题
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
```

- [ ] **Step 3: 跑红**

Run: `cd backend && ./venv/Scripts/python.exe -m pytest tests/test_conversation_service.py -q`
Expected: FAIL（`ModuleNotFoundError`）。

- [ ] **Step 4: 实现** `backend/services/conversation_service.py`

```python
"""对话服务：JSON 持久化的多对话、历史与文档清单管理。

镜像 services/auth_service 的持久化范式（threading.Lock + 临时文件原子替换）。
Qdrant/Neo4j 是分片的单一事实来源；本服务只管对话元数据、消息历史与文档清单。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional


DEFAULT_TITLE = "新对话"
_TITLE_LEN = 20


class ConversationService:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._lock = Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.db_path.exists():
            self._save({"conversations": {}})

    # -- 内部 IO --
    def _load(self) -> Dict[str, Dict[str, Any]]:
        try:
            data = json.loads(self.db_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        convs = data.get("conversations") if isinstance(data, dict) else None
        return convs if isinstance(convs, dict) else {}

    def _save(self, convs: Dict[str, Any]) -> None:
        tmp = self.db_path.with_suffix(self.db_path.suffix + ".tmp")
        tmp.write_text(json.dumps({"conversations": convs}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.db_path)

    @staticmethod
    def _new(owner: str, title: Optional[str]) -> Dict[str, Any]:
        now = int(time.time())
        return {
            "id": uuid.uuid4().hex,
            "owner": owner,
            "title": title or DEFAULT_TITLE,
            "created_at": now,
            "updated_at": now,
            "documents": [],
            "messages": [],
        }

    # -- CRUD --
    def create(self, owner: str, title: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            convs = self._load()
            conv = self._new(owner, title)
            convs[conv["id"]] = conv
            self._save(convs)
        return conv

    def list(self, owner: str) -> List[Dict[str, Any]]:
        convs = self._load()
        mine = [c for c in convs.values() if c.get("owner") == owner]
        mine.sort(key=lambda c: c.get("updated_at", 0), reverse=True)
        out = []
        for c in mine:
            msgs = c.get("messages") or []
            preview = msgs[-1]["content"][:40] if msgs else ""
            out.append({
                "id": c["id"],
                "title": c.get("title", DEFAULT_TITLE),
                "updated_at": c.get("updated_at", 0),
                "document_count": len(c.get("documents") or []),
                "preview": preview,
            })
        return out

    def get(self, owner: str, conv_id: str) -> Optional[Dict[str, Any]]:
        c = self._load().get(conv_id)
        if not c or c.get("owner") != owner:
            return None
        return c

    def rename(self, owner: str, conv_id: str, title: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            convs = self._load()
            c = convs.get(conv_id)
            if not c or c.get("owner") != owner:
                return None
            c["title"] = title.strip()[:60] or DEFAULT_TITLE
            c["updated_at"] = int(time.time())
            self._save(convs)
            return c

    def delete(self, owner: str, conv_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            convs = self._load()
            c = convs.get(conv_id)
            if not c or c.get("owner") != owner:
                return None
            del convs[conv_id]
            self._save(convs)
            return c

    def append_message(self, owner: str, conv_id: str, role: str, content: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            convs = self._load()
            c = convs.get(conv_id)
            if not c or c.get("owner") != owner:
                return None
            now = int(time.time())
            c["messages"].append({"role": role, "content": content, "at": now})
            # 首条 user 消息触发自动 title
            if role == "user" and c.get("title", DEFAULT_TITLE) == DEFAULT_TITLE:
                c["title"] = content.strip()[:_TITLE_LEN] or DEFAULT_TITLE
            c["updated_at"] = now
            self._save(convs)
            return c

    # -- 文档清单 --
    def list_documents(self, owner: str, conv_id: str) -> List[Dict[str, Any]]:
        c = self.get(owner, conv_id)
        return list((c or {}).get("documents", []))

    def add_document(self, owner: str, conv_id: str, doc: Dict[str, Any]) -> None:
        with self._lock:
            convs = self._load()
            c = convs.get(conv_id)
            if not c or c.get("owner") != owner:
                return
            c.setdefault("documents", []).append(doc)
            c["updated_at"] = int(time.time())
            self._save(convs)

    def remove_document(self, owner: str, conv_id: str, name: str) -> None:
        with self._lock:
            convs = self._load()
            c = convs.get(conv_id)
            if not c or c.get("owner") != owner:
                return
            c["documents"] = [d for d in c.get("documents", []) if d.get("name") != name]
            c["updated_at"] = int(time.time())
            self._save(convs)
```

- [ ] **Step 5: 跑绿**

Run: `cd backend && ./venv/Scripts/python.exe -m pytest tests/test_conversation_service.py -q`
Expected: 9 passed。

- [ ] **Step 6: Commit**

```bash
git add backend/services/conversation_service.py backend/tests/test_conversation_service.py backend/core/config.py
git commit -m "feat: 新增 ConversationService 管理多对话/历史/文档清单"
```

---

## Task A2: QdrantStore 按 `conversation_id` 隔离

**Files:**
- Modify: `backend/rag/qdrant_store.py`
- Modify: `backend/tests/test_qdrant_store.py`
- Modify: `backend/tests/conftest.py`（`FakeQdrantClient._matches`/`scroll`/`query_points` 支持 `conversation_id` 条件；`MockQdrant.search`/`scroll_by_source` 签名加 `conversation_id=None`）

**Interfaces (Produces):**
```python
# QdrantStore
def upsert(self, texts, metadatas=None) -> int                       # 不变；conversation_id 由调用方放进 metadata
def search(self, query, top_k=None, owner=None, conversation_id=None) -> list[dict]
def scroll_by_source(self, source, owner=None, conversation_id=None, limit=None) -> list[dict]
def delete_by_source(self, source, owner=None, conversation_id=None) -> int
def delete_by_conversation(self, owner, conversation_id) -> int      # 新增
```
约定：`conversation_id is None` → 不加该过滤（兼容旧行为与既有测试）。

- [ ] **Step 1: 升级 conftest 的 FakeQdrantClient 过滤**

`backend/tests/conftest.py` 的 `_payload_filter` 真实实现会按任意 `key=value` 匹配；`FakeQdrantClient._matches` 已是通用的「must 条件全等」实现，天然支持 `conversation_id`。无需改 `_matches`。只需确认 `MockQdrant` 签名加 `conversation_id=None`（见 Step 4）。

- [ ] **Step 2: 写失败测试**（追加到 `backend/tests/test_qdrant_store.py`）

```python
def test_upsert_stores_conversation_id(settings):
    store = make_store(settings)
    store.upsert(["x"], metadatas=[{"source": "d", "conversation_id": "c1"}])
    assert store.client.points[0].payload["conversation_id"] == "c1"


def test_search_filters_by_conversation(settings):
    store = make_store(settings)
    store.upsert(["a"], metadatas=[{"source": "d", "conversation_id": "c1", "chunk_index": 0}])
    store.upsert(["b"], metadatas=[{"source": "d", "conversation_id": "c2", "chunk_index": 0}])
    store.client.scored = [
        scored({"text": "a", "source": "d", "conversation_id": "c1"}, 0.9),
        scored({"text": "b", "source": "d", "conversation_id": "c2"}, 0.9),
    ]
    out = store.search("q", top_k=5, conversation_id="c1")
    assert [h["text"] for h in out] == ["a"]


def test_scroll_by_source_filters_by_conversation(settings):
    store = make_store(settings)
    _upsert_chunk(store, "d.txt", 0, "a", )  # 复用文件内已有 _upsert_chunk，需补 conversation_id；见下
    # 注：_upsert_chunk 需支持 conversation_id——在测试里直接用 upsert：
    store = make_store(settings)
    store.upsert(["a"], metadatas=[{"source": "d.txt", "chunk_index": 0, "conversation_id": "c1"}])
    store.upsert(["b"], metadatas=[{"source": "d.txt", "chunk_index": 0, "conversation_id": "c2"}])
    assert [h["text"] for h in store.scroll_by_source("d.txt", conversation_id="c1")] == ["a"]


def test_delete_by_source_scoped_to_conversation(settings):
    store = make_store(settings)
    store.upsert(["a1", "a2"], metadatas=[
        {"source": "d.txt", "chunk_index": 0, "conversation_id": "c1"},
        {"source": "d.txt", "chunk_index": 1, "conversation_id": "c2"},
    ])
    assert store.delete_by_source("d.txt", conversation_id="c1") == 1
    remaining = [p.payload["conversation_id"] for p in store.client.points]
    assert remaining == ["c2"]


def test_delete_by_conversation_clears_only_that_conv(settings):
    store = make_store(settings)
    store.upsert(["a", "b", "c"], metadatas=[
        {"source": "d1", "conversation_id": "c1"},
        {"source": "d2", "conversation_id": "c1"},
        {"source": "d3", "conversation_id": "c2"},
    ])
    assert store.delete_by_conversation(owner=None, conversation_id="c1") == 2
    assert [p.payload["conversation_id"] for p in store.client.points] == ["c2"]
```

- [ ] **Step 3: 跑红** — `pytest tests/test_qdrant_store.py -q`（新用例 FAIL：`unexpected keyword 'conversation_id'`）。

- [ ] **Step 4: 实现** `backend/rag/qdrant_store.py`

(a) `search`：
```python
def search(self, query, top_k=None, owner=None, conversation_id=None):
    limit = top_k or self.settings.qdrant_top_k
    query_vector = self.embedding.embed(query)
    response = self.client.query_points(
        collection_name=self.collection,
        query=query_vector,
        limit=limit,
        with_payload=True,
        query_filter=self._payload_filter(owner=owner, conversation_id=conversation_id),
    )
    # ...解析不变
```

(b) `scroll_by_source`：把 `scroll_filter` 改为 `self._payload_filter(source=source, owner=owner, conversation_id=conversation_id)`。

(c) `delete_by_source`：`points_selector=self._payload_filter(source=source, owner=owner, conversation_id=conversation_id)`。

(d) 新增 `delete_by_conversation`：
```python
def delete_by_conversation(self, owner, conversation_id) -> int:
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
```

`_payload_filter(**matches)` 已是通用实现（`auth_service` 同款：忽略 value 为 None 的键），`conversation_id=None` 自然不进条件。无需改 `_payload_filter`。

(e) conftest：`MockQdrant.search`/`scroll_by_source`/`delete_by_source` 签名加 `conversation_id=None`（实现里忽略即可，因为 Mock 不真过滤）：
```python
def search(self, query, top_k=None, owner=None, conversation_id=None): ...
def scroll_by_source(self, source, owner=None, conversation_id=None, limit=None): ...
def delete_by_source(self, source, owner=None, conversation_id=None) -> int: return self.deleted
def delete_by_conversation(self, owner, conversation_id) -> int: return self.deleted
```

- [ ] **Step 5: 跑绿** — `pytest tests/test_qdrant_store.py -q`（全部通过，含既有用例）。

- [ ] **Step 6: Commit**
```bash
git add backend/rag/qdrant_store.py backend/tests/test_qdrant_store.py backend/tests/conftest.py
git commit -m "feat: QdrantStore 按 conversation_id 隔离检索与删除"
```

---

## Task A3: Neo4jStore 按 `conversation_id` 隔离

**Files:**
- Modify: `backend/rag/neo4j_store.py`
- Modify: `backend/tests/test_neo4j_store.py`
- Modify: `backend/tests/conftest.py`（`MockNeo4j` 签名加 `conversation_id=None`）

**Interfaces (Produces):**
```python
# Neo4jStore
def add_knowledge(self, triples, source, conversation_id=None) -> int   # source 形如 owner::conv::name 或保持 owner::name；新增 conversation_id 属性
def search(self, entities, limit=5, owner=None, conversation_id=None) -> list[dict]
def delete_by_source(self, source) -> int                                # 保持；新增：
def delete_by_conversation(self, owner, conversation_id) -> int
```

- [ ] **Step 1: 读现状** — `Read backend/rag/neo4j_store.py` 与 `backend/tests/test_neo4j_store.py`，确认 `add_knowledge`/`search`/`delete_by_source` 的 Cypher 与属性写法（三元组节点/关系上带 `source` 属性）。

- [ ] **Step 2: 写失败测试**（追加到 `test_neo4j_store.py`），断言：`add_knowledge(..., conversation_id="c1")` 写入的关系带 `conversation_id` 属性；`search(entities, conversation_id="c1")` 只返回 c1 的关系；`delete_by_conversation` 只清 c1。用 `FakeSession`/`FakeTx` 捕获 Cypher 参数。

```python
def test_add_knowledge_tags_conversation_id(fake_driver_for_neo4j):
    # 用真实 Neo4jStore 注入 FakeDriver，断言 execute_write 收到的 Cypher 参数含 conversation_id
    ...
def test_search_filters_by_conversation(...): ...
def test_delete_by_conversation_only_that_conv(...): ...
```
（具体 Cypher 断言依 Step 1 读到的实际实现填充。）

- [ ] **Step 3: 跑红** — `pytest tests/test_neo4j_store.py -q`。

- [ ] **Step 4: 实现** — 在写入 Cypher 的关系/节点上加 `conversation_id` 属性；`search` 的 WHERE 加 `AND r.conversation_id=$conversation_id`（参数为 None 时不加该条件——动态拼 Cypher 时注意注入安全，仅拼字面 `conversation_id` 字段名）；新增 `delete_by_conversation` 用 `MATCH ()-[r]->() WHERE r.conversation_id=$cid [AND r.source STARTS WITH $owner_prefix] DELETE r`。

- [ ] **Step 5: 跑绿** — `pytest tests/test_neo4j_store.py -q`。

- [ ] **Step 6: Commit**
```bash
git add backend/rag/neo4j_store.py backend/tests/test_neo4j_store.py backend/tests/conftest.py
git commit -m "feat: Neo4jStore 按 conversation_id 隔离检索与删除"
```

---

## Task A4: RagService 透传 `conversation_id`

**Files:**
- Modify: `backend/rag/rag_service.py`
- Modify: `backend/tests/test_rag_service.py`
- Modify: `backend/tests/conftest.py`（`MockRag` 若被直接用于检索，加 `conversation_id` 透传）

**Interfaces (Produces):**
```python
# RagService
def ingest_text(self, text, source="manual", owner=None, conversation_id=None) -> dict   # metadata 写 conversation_id
def retrieve(self, query, top_k=None, owner=None, conversation_id=None) -> dict            # search/scroll 传 conv_id
def build_context(self, query, top_k=None, owner=None, conversation_id=None) -> dict
def resolve_vector_hits(self, question, vector_hits, owner=None, conversation_id=None) -> tuple[list, bool]
def delete_document(self, source, owner=None, conversation_id=None) -> dict
def delete_documents(self, sources, owner=None, conversation_id=None) -> dict
def delete_conversation(self, owner, conversation_id) -> dict                               # 新增：清 qdrant+neo4j
```

- [ ] **Step 1: 写失败测试**（追加到 `test_rag_service.py`）

```python
def test_ingest_text_passes_conversation_id_to_qdrant(settings):
    seen = {}
    class Q(MockQdrant):
        def upsert(self, texts, metadatas=None):
            seen["meta"] = metadatas
            return len(texts)
    rag = _make_rag(qdrant=Q(), settings=settings)
    rag.ingest_text("内容", source="d.pdf", owner="alice", conversation_id="c1")
    assert seen["meta"][0]["conversation_id"] == "c1"


def test_retrieve_passes_conversation_id(settings):
    class Q(MockQdrant):
        def search(self, query, top_k=None, owner=None, conversation_id=None):
            return [{"text": "x", "source": "d", "conversation_id": conversation_id}]
    rag = _make_rag(qdrant=Q(), settings=settings)
    out = rag.retrieve("q", owner="alice", conversation_id="c1")
    assert out["qdrant"][0]["conversation_id"] == "c1"


def test_delete_conversation_calls_both_stores(settings):
    class Q(MockQdrant):
        def __init__(self): super().__init__(); self.deleted_conv=None
        def delete_by_conversation(self, owner, conversation_id): self.deleted_conv=(owner,conversation_id); return 7
    class N(MockNeo4j):
        def __init__(self): super().__init__(); self.deleted_conv=None
        def delete_by_conversation(self, owner, conversation_id): self.deleted_conv=(owner,conversation_id); return 3
    q,n=Q(),N()
    rag=_make_rag(qdrant=q, neo4j=n, settings=settings)
    out=rag.delete_conversation("alice","c1")
    assert out=={"chunks":7,"relations":3} and q.deleted_conv==("alice","c1") and n.deleted_conv==("alice","c1")
```

- [ ] **Step 2: 跑红** — `pytest tests/test_rag_service.py -q`。

- [ ] **Step 3: 实现** `backend/rag/rag_service.py`
- `ingest_text`：metadata 加 `conversation_id`（当 not None）；`self.qdrant.upsert(chunks, metadatas)`；三元组 `add_knowledge(..., conversation_id=conversation_id)`。
- `retrieve`：`self.qdrant.search(query, top_k=limit, owner=owner, conversation_id=conversation_id)`；`resolve_vector_hits(..., conversation_id=conversation_id)`。
- `resolve_vector_hits`：`scroll_by_source(dominant_source, owner=owner, conversation_id=conversation_id, limit=...)`。
- `build_context`：透传 `conversation_id` 给 `retrieve`。
- `delete_document`/`delete_documents`：透传 `conversation_id` 给 `delete_by_source`。
- 新增 `delete_conversation`：
```python
def delete_conversation(self, owner, conversation_id) -> Dict[str, Any]:
    chunks = self.qdrant.delete_by_conversation(owner, conversation_id)
    relations = self.neo4j.delete_by_conversation(owner, conversation_id)
    logger.info("Deleted conversation: %d chunks, %d relations", chunks, relations)
    return {"chunks": chunks, "relations": relations}
```

- [ ] **Step 4: 跑绿** — `pytest tests/test_rag_service.py -q`。

- [ ] **Step 5: 全量回归** — `pytest -q`（确保 211 既有用例 + A1–A4 新增全绿）。

- [ ] **Step 6: Commit**
```bash
git add backend/rag/rag_service.py backend/tests/test_rag_service.py backend/tests/conftest.py
git commit -m "feat: RagService 透传 conversation_id 并支持删对话"
```

---

## Task A5: 图节点透传 `conversation_id`

**Files:**
- Modify: `backend/nodes.py`
- Modify: `backend/multiagent/nodes.py`
- Modify: `backend/tests/test_nodes.py`、`backend/tests/test_multiagent_nodes.py`

**Interfaces:**
- `GraphState`/`MultiAgentState` 加字段 `conversation_id: str`。
- `qdrant_node`：`self.rag.qdrant.search(question, top_k, owner=..., conversation_id=state.get("conversation_id"))`。
- `neo4j_node`：`self.rag.neo4j.search(keywords, limit, owner=..., conversation_id=state.get("conversation_id"))`。
- `merge_node`：`resolve_vector_hits(question, vector_hits, owner=..., conversation_id=state.get("conversation_id"))`。
- 多智能体 `rag_agent`：`build_context(question, owner=..., conversation_id=state.get("conversation_id"))`。

- [ ] **Step 1: 写失败测试**（追加到 `test_nodes.py`）

```python
def test_qdrant_node_passes_conversation_id(settings):
    class Q(MockQdrant):
        def search(self, query, top_k=None, owner=None, conversation_id=None):
            return [{"text": str(conversation_id), "score": 0.9, "source": "d"}]
    rag = MockRag(Q())
    nodes = GraphNodes(MockLLM(), rag, settings)
    out = nodes.qdrant({"question": "q", "owner": "alice", "conversation_id": "c1"})
    assert out["qdrant_results"][0]["text"] == "c1"


def test_merge_node_passes_conversation_id_to_resolve(settings):
    seen = {}
    class R(MockRag):
        def resolve_vector_hits(self, question, vector_hits, owner=None, conversation_id=None):
            seen["cid"] = conversation_id
            return vector_hits, False
    nodes = GraphNodes(MockLLM(), R(), settings)
    nodes.merge({"question": "q", "qdrant_results": [{"text": "v", "score": 0.9, "source": "d"}], "conversation_id": "c1"})
    assert seen["cid"] == "c1"
```
（多智能体同理追加 `test_rag_agent_passes_conversation_id` 到 `test_multiagent_nodes.py`：让 `FakeRag.build_context` 记录 `conversation_id`。）

- [ ] **Step 2: 跑红** — `pytest tests/test_nodes.py tests/test_multiagent_nodes.py -q`。

- [ ] **Step 3: 实现** — 按上面「Interfaces」改 4 处节点调用。conftest 的 `MockRag.resolve_vector_hits` 签名已在 A4 加 `conversation_id=None`。

- [ ] **Step 4: 跑绿 + 全量回归** — `pytest -q`。

- [ ] **Step 5: Commit**
```bash
git add backend/nodes.py backend/multiagent/nodes.py backend/tests/test_nodes.py backend/tests/test_multiagent_nodes.py
git commit -m "feat: 图节点透传 conversation_id 到检索层"
```

---

## Task A6: 对话 CRUD + 文档端点（API）

**Files:**
- Modify: `backend/main.py`（实例化 `ConversationService`，挂 `app.state.conversations`）
- Modify: `backend/api.py`（新增端点）
- Modify: `backend/tests/test_api.py`

**Interfaces (Produces — HTTP):**
- `POST /api/conversations` body `{title?}` → 完整 conv
- `GET /api/conversations` → `[{id,title,updated_at,document_count,preview}]`
- `GET /api/conversations/{id}` → 完整 conv
- `PATCH /api/conversations/{id}` body `{title}` → 完整 conv
- `DELETE /api/conversations/{id}` → 删 conv + 清 Qdrant/Neo4j（返回 `{id, chunks, relations}`）
- `POST /api/conversations/{id}/documents`（multipart `files[]` + `folder_path?`，复刻 `/ingest/files` 解析）→ `BatchIngestResponse`
- `DELETE /api/conversations/{id}/documents` body `{source}` → `DeleteDocResponse`

- [ ] **Step 1: main.py 挂服务**

`backend/main.py` 在构建 `auth` 旁加：
```python
from services.conversation_service import ConversationService
...
app.state.conversations = ConversationService(settings.conversations_db_path)
```
（具体位置依现有 lifespan 挂载代码；`Read backend/main.py` 确认。）

- [ ] **Step 2: api.py 取服务辅助**

`backend/api.py` 加：
```python
def _conversations(request: Request) -> ConversationService:
    svc = getattr(request.app.state, "conversations", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="对话服务尚未初始化")
    return svc


def _require_conv(request: Request, conv_id: str, username: str):
    conv = _conversations(request).get(username, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="对话不存在或无权访问")
    return conv
```
并 `from services.conversation_service import ConversationService`。

- [ ] **Step 3: 写失败测试**（追加到 `test_api.py`，用现有 `client` fixture 与已登录 header）

```python
def test_conversation_crud_flow(auth_client):
    r = auth_client.post("/api/conversations", json={})
    assert r.status_code == 200
    cid = r.json()["id"]
    assert auth_client.get("/api/conversations").json()[0]["id"] == cid
    assert auth_client.patch(f"/api/conversations/{cid}", json={"title": "T"}).json()["title"] == "T"
    assert auth_client.get(f"/api/conversations/{cid}").json()["title"] == "T"


def test_conversation_isolation_between_users(auth_client, auth_client_bob):
    cid = auth_client.post("/api/conversations", json={}).json()["id"]
    assert auth_client_bob.get(f"/api/conversations/{cid}").status_code == 404
    assert auth_client_bob.get("/api/conversations").json() == []


def test_delete_conversation_cleans_stores(auth_client, monkeypatch):
    cid = auth_client.post("/api/conversations", json={}).json()["id"]
    r = auth_client.delete(f"/api/conversations/{cid}")
    assert r.status_code == 200
    assert r.json()["id"] == cid
```
（`auth_client_bob` 若无，在 conftest 加一个第二用户登录 client。）

- [ ] **Step 4: 跑红** — `pytest tests/test_api.py -q`。

- [ ] **Step 5: 实现端点** — `POST/GET list/GET one/PATCH/DELETE` + 文档上传/删除。文档上传复刻 `ingest_files` 的解析循环（`parse_upload` + `rag.ingest_text(..., conversation_id=cid)`），每文件成功后 `conversations.add_document(username, cid, {name,chunks,at})`；删除文档调 `rag.delete_document(source, owner=username, conversation_id=cid)` + `conversations.remove_document`。删对话：`conv = conversations.delete(username, cid)` → `rag.delete_conversation(username, cid)`。

- [ ] **Step 6: 跑绿 + 全量回归** — `pytest -q`。

- [ ] **Step 7: Commit**
```bash
git add backend/main.py backend/api.py backend/tests/test_api.py backend/tests/conftest.py
git commit -m "feat: 对话 CRUD 与文档端点（按对话隔离）"
```

---

## Task A7: 对话级 chat（后端接管历史读写）

**Files:**
- Modify: `backend/api.py`（新增 `POST /api/conversations/{id}/chat` 与 `/chat/stream`）
- Modify: `backend/tests/test_api.py`

**Interfaces (Produces — HTTP):**
- 请求体：`{message: str, mode?: "rag"|"multi"}`（**无 history**）。
- 后端流程：`get conv` → 用 `conv["messages"]` 构造 `history` → 注入 `owner`+`conversation_id` 跑图 → 生成后 `append_message(user)` + `append_message(assistant)`。
- 非流式返回与旧 `ChatResponse` 同形；流式 SSE 帧不变（`node`/`delta`/`done`）。

- [ ] **Step 1: 写失败测试**（追加到 `test_api.py`）

```python
def test_conversation_chat_accumulates_history(auth_client, monkeypatch):
    cid = auth_client.post("/api/conversations", json={}).json()["id"]
    # mock 图返回固定答案，并断言收到的 history 随轮次增长
    captured = {"hist": []}
    def fake_invoke(initial): captured["hist"].append(len(initial.get("history", []))); return {"answer":"A","sources":[],"used_rag":False,"iterations":1}
    monkeypatch.setattr("api._select_graph", lambda req,mode: types.SimpleNamespace(invoke=fake_invoke))
    r1 = auth_client.post(f"/api/conversations/{cid}/chat", json={"message":"你好"})
    assert r1.json()["answer"]=="A"
    auth_client.post(f"/api/conversations/{cid}/chat", json={"message":"再问"})
    assert captured["hist"] == [0, 2]   # 第2轮含 1 user + 1 assistant 历史
    msgs = auth_client.get(f"/api/conversations/{cid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user","assistant","user","assistant"]


def test_conversation_chat_other_user_404(auth_client, auth_client_bob):
    cid = auth_client.post("/api/conversations", json={}).json()["id"]
    assert auth_client_bob.post(f"/api/conversations/{cid}/chat", json={"message":"x"}).status_code == 404
```

- [ ] **Step 2: 跑红** — `pytest tests/test_api.py -q`。

- [ ] **Step 3: 实现非流式端点**

```python
@router.post("/conversations/{conv_id}/chat", response_model=ChatResponse)
def conversation_chat(conv_id: str, payload: ChatRequest, request: Request, username: str = Depends(current_user)):
    convs = _conversations(request)
    conv = convs.get(username, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="对话不存在或无权访问")
    graph = _select_graph(request, payload.mode)
    history = [{"role": m["role"], "content": m["content"]} for m in conv.get("messages", [])]
    try:
        result = graph.invoke({
            "question": payload.message, "history": history, "iterations": 0,
            "owner": username, "conversation_id": conv_id,
        })
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"问答流程执行失败：{exc}") from exc
    answer = result.get("answer", "")
    convs.append_message(username, conv_id, "user", payload.message)
    convs.append_message(username, conv_id, "assistant", answer)
    return ChatResponse(answer=answer, sources=result.get("sources", []), used_rag=result.get("used_rag", False), iterations=result.get("iterations", 0))
```

- [ ] **Step 4: 实现流式端点** — 复刻现有 `chat_stream` 的 SSE 结构；从 conv 取 history；流式完成后用累积的 `answer` 调两次 `append_message`。

```python
@router.post("/conversations/{conv_id}/chat/stream")
async def conversation_chat_stream(conv_id: str, payload: ChatRequest, request: Request, username: str = Depends(current_user)):
    convs = _conversations(request)
    conv = convs.get(username, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="对话不存在或无权访问")
    graph = _select_graph(request, payload.mode)
    history = [{"role": m["role"], "content": m["content"]} for m in conv.get("messages", [])]
    initial = {"question": payload.message, "history": history, "iterations": 0, "streaming": True, "owner": username, "conversation_id": conv_id}
    async def event_stream():
        buffer = []
        try:
            async for mode, data in graph.astream(initial, stream_mode=["updates","custom"]):
                if mode == "updates":
                    for node, update in data.items():
                        yield _sse({"type":"node","node":node,"update":_summarize_update(node,update)})
                elif mode == "custom":
                    if isinstance(data, dict) and data.get("type")=="delta" and "text" in data:
                        buffer.append(data["text"])
                    yield _sse(data)
            yield _sse({"type":"done"})
        except Exception as exc:  # noqa: BLE001
            logger.exception("stream graph failed: %s", exc)
            yield _sse({"type":"error","message":f"问答流程执行失败：{exc}"})
        finally:
            answer = "".join(buffer)
            convs.append_message(username, conv_id, "user", payload.message)
            if answer:
                convs.append_message(username, conv_id, "assistant", answer)
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})
```

- [ ] **Step 5: 跑绿 + 全量回归** — `pytest -q`。

- [ ] **Step 6: Commit**
```bash
git add backend/api.py backend/tests/test_api.py
git commit -m "feat: 对话级 chat（后端接管历史读写，按对话隔离检索）"
```

---

## Task A8: 迁移脚本（旧全局文档 → 「导入的文档」对话）

**Files:**
- Create: `backend/script/migrate_conversations.py`
- Create: `backend/tests/test_migrate_conversations.py`

**Interfaces:** 幂等脚本：扫 Qdrant 每个 owner 的分片（`conversation_id` 缺失的），按 owner 各建一条「导入的文档」对话，回填这些分片的 `conversation_id`，并在 `conversations.json` 建记录。重跑不重复建（已存在同名对话则跳过/合并）。

- [ ] **Step 1: 写失败测试** `test_migrate_conversations.py`：用 `FakeQdrantClient` 灌入无 `conversation_id` 的分片，跑迁移，断言分片被回填 conv_id、conversations.json 出现对应记录；再跑一次断言幂等（不新增对话、不重复回填）。

- [ ] **Step 2: 跑红**。

- [ ] **Step 3: 实现** `migrate_conversations.py`：
  - 遍历 `qdrant.scan_all()`，按 `owner` 分组、筛 `conversation_id` 缺失者。
  - 对每个 owner：若无标题为「导入的文档」的对话则 `conversations.create(owner, "导入的文档")`；按 `source` 聚合文档清单写入。
  - 回填：Qdrant 没有原地改 payload 的简单 API → 用 `client.set_payload`（`points_selector` 按 owner 过滤 + `payload={"conversation_id":cid}`）。若版本不支持，退化为「删旧 + 重插」。
  - Neo4j 三元组同样回填（`SET r.conversation_id=$cid`）。

- [ ] **Step 4: 跑绿** — `pytest tests/test_migrate_conversations.py -q`。

- [ ] **Step 5: 手动验证** — `./venv/Scripts/python.exe script/migrate_conversations.py`（在已有数据的环境跑一次，`GET /api/conversations` 应见「导入的文档」）。

- [ ] **Step 6: Commit**
```bash
git add backend/script/migrate_conversations.py backend/tests/test_migrate_conversations.py
git commit -m "feat: 旧全局文档迁移到「导入的文档」对话（幂等）"
```

**Phase A 完成标志：** `pytest -q` 全绿；`curl` 走通「建对话→传文档→对话内 chat→删对话」。

---

# Phase B — 前端

> 前提：Phase A 端点已就绪。前端切到新端点后，旧 `chat-history.ts`、旧 `/api/chat*`、`/api/docs`、`/ingest*` 调用全部移除。

## Task B1: 类型与 API client

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`

- [ ] **Step 1: types.ts 增加类型**

```ts
export interface ConversationDoc { name: string; chunks: number; at: number; }
export interface ConversationMessage { role: string; content: string; at?: number; }
export interface ConversationSummary {
  id: string; title: string; updated_at: number; document_count: number; preview: string;
}
export interface Conversation {
  id: string; owner: string; title: string; created_at: number; updated_at: number;
  documents: ConversationDoc[]; messages: ConversationMessage[];
}
```

- [ ] **Step 2: client.ts 增加方法**（保留现有 `chatStream` 旧签名到 B4 再删；新增对话版）
```ts
export async function listConversations(): Promise<ConversationSummary[]>
export async function createConversation(title?: string): Promise<Conversation>
export async function getConversation(id: string): Promise<Conversation>
export async function renameConversation(id: string, title: string): Promise<Conversation>
export async function deleteConversation(id: string): Promise<void>
export async function uploadConversationDocs(id: string, files: File[]): Promise<BatchIngestResponse>
export async function deleteConversationDoc(id: string, source: string): Promise<void>
export function chatStreamInConversation(
  conversationId: string, message: string,
  cb: StreamCallbacks, mode: ChatMode
): Promise<void>
```
`chatStreamInConversation` 复用现有 SSE 解析，URL 改为 `/api/conversations/${id}/chat/stream`，body 只发 `{message, mode}`。

- [ ] **Step 3: 类型检查** — `cd frontend && npm run build`（tsc 通过）。

- [ ] **Step 4: Commit**
```bash
git add frontend/src/types.ts frontend/src/api/client.ts
git commit -m "feat(fe): 对话相关类型与 API client 方法"
```

---

## Task B2: App 状态机改为「以对话为中心」

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: 状态替换**

去掉 `messages` 的 localStorage 读写（`loadUserMessages/saveUserMessages/clearUserMessages` 调用全部移除）。新增：
```ts
const [conversations, setConversations] = useState<ConversationSummary[]>([]);
const [currentId, setCurrentId] = useState<string | null>(null);
const [current, setCurrent] = useState<Conversation | null>(null);   // 其 messages 渲染为 ChatMessage[]
```
登录后：`listConversations()` → 若空则 `createConversation()` → `setCurrentId` 第一条；`currentId` 变化 → `getConversation(id)` → `setCurrent`。

- [ ] **Step 2: `handleSend` 改走对话**
```ts
await chatStreamInConversation(currentId!, text, { onNode, onDelta, onDone, onError }, mode);
// 流结束后 getConversation(currentId) 刷新 current（拿到持久化的 user+assistant 消息）
```
本地乐观追加 user/assistant 气泡的逻辑可保留以保流畅，但最终以 `getConversation` 为准刷新。

- [ ] **Step 3: 文档上传/删除改对话版** — `handleUploadFiles`/`handleDeleteDoc` 改用 `uploadConversationDocs(currentId, files)` / `deleteConversationDoc(currentId, source)`，完成后 `getConversation(currentId)` 刷新 `current.documents`。

- [ ] **Step 4: 新建/切换/重命名/删除对话的 handler** — `createConversation`→prepend 列表并选中；`renameConversation`→更新列表与 current；`deleteConversation`→从列表移除、清 Qdrant（后端已清）、选中下一条或新建。

- [ ] **Step 5: 渲染** — 把 `current?.messages` 映射为 `ChatMessage[]` 传给 `ChatWindow`；把 `current?.documents` 传给 `Sidebar`。

- [ ] **Step 6: 类型检查 + 跑现有前端测试** — `npm run build && npm run test`。

- [ ] **Step 7: Commit**
```bash
git add frontend/src/App.tsx
git commit -m "feat(fe): App 以对话为中心（列表/切换/历史走后端）"
```

---

## Task B3: 侧栏两段式（对话列表 + 当前对话文档）

**Files:**
- Create: `frontend/src/components/ConversationList.tsx`（+ `.css`）
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/App.css`

- [ ] **Step 1: ConversationList 组件** — props：`conversations`、`currentId`、`onSelect/onCreate/onRename/onDelete`。UI：顶部「+ 新对话」按钮 + 列表项（标题、updated_at 相对时间、最后消息预览）；项上 hover 出重命名/删除。

- [ ] **Step 2: Sidebar 改造** — 顶部嵌 `<ConversationList/>`，下方保留现有文档上传/列表区，但 docs 来自 `current.documents`、上传/删除回调指向当前对话。

- [ ] **Step 3: 样式** — `App.css`/`ConversationList.css` 调整侧栏两段滚动与分隔。

- [ ] **Step 4: 手动联调** — `npm run dev`，浏览器走查：新建/切换/重命名/删除对话、各对话文档独立、聊天历史随对话切换。

- [ ] **Step 5: Commit**
```bash
git add frontend/src/components/ConversationList.tsx frontend/src/components/ConversationList.css frontend/src/components/Sidebar.tsx frontend/src/App.css
git commit -m "feat(fe): 侧栏两段式（对话列表 + 当前对话文档）"
```

---

## Task B4: 清理旧前端代码与端点

**Files:**
- Remove: `frontend/src/chat-history.ts`、`frontend/src/chat-history.test.ts`
- Modify: `frontend/src/App.tsx`（移除残留 import）
- Modify: `backend/api.py`（移除旧 `/api/chat`、`/api/chat/stream`、`/api/docs`、`/ingest*`、`/docs/delete*`、`/stats` 中不再用的；或保留 `/api/health`）
- Modify: 对应后端测试（移除旧端点用例）

- [ ] **Step 1: 删 `chat-history.ts` 与其测试**，清理 `App.tsx` import。

- [ ] **Step 2: 后端移除旧端点**（确认前端无引用后）：`POST /api/chat`、`/api/chat/stream`、`GET /api/docs`、`POST /api/ingest*`、`/api/docs/delete*`、`GET /api/stats`。保留 `/api/health`、`/api/auth/*`、对话端点。

- [ ] **Step 3: 更新后端测试** — 删除/改写 `test_api.py` 中针对旧端点的用例（如 `test_chat_*`、`test_ingest_*`、`test_docs_*`、`test_stats`）。

- [ ] **Step 4: 全量回归** — `cd backend && ./venv/Scripts/python.exe -m pytest -q` 全绿；`cd frontend && npm run build && npm run test` 通过。

- [ ] **Step 5: Commit**
```bash
git add -A
git commit -m "chore: 移除旧单对话端点与前端 localStorage 历史"
```

---

## Task B5: 端到端验证

- [ ] **Step 1: 双对话隔离** — 建对话 A 传「岗位一览表.pdf」，建对话 B 传另一文档；A 内问「列出所有岗位」得完整列表且不命中 B；切到 B 问 A 的内容应得到「知识库中无相关内容」。

- [ ] **Step 2: 记忆持久** — A 内多轮对话后刷新页面/重登，历史仍在（后端持久化）。

- [ ] **Step 3: 删除清理** — 删 A，确认 `GET /api/conversations` 不再含 A，B 的问答不受影响。

- [ ] **Step 4: 迁移** — 若环境有旧全局文档，跑 `script/migrate_conversations.py`，「导入的文档」对话出现且可问答。

---

## Self-Review 结论

- **Spec 覆盖**：设计文档 §2 数据模型 → A1/A2/A3；§3 检索隔离 → A2/A3/A4/A5；§4 ConversationService → A1；§5 API → A6/A7；§6 前端 → B1–B4；§7 迁移 → A8；§8 测试 → 各任务 TDD + B5。全覆盖。
- **类型一致**：`conversation_id` 全链路 Optional；`ConversationService` 方法签名在 A1 定义、A6/A7 消费；`delete_by_conversation` 在 A2/A3 定义、A4 消费。已对齐。
- **占位符**：A3 的 Cypher 断言需依实际 `neo4j_store.py` 填充（已标注「Step 1 读现状」），其余步骤含完整代码。
