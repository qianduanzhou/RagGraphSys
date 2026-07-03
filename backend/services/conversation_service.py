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
        tmp.write_text(
            json.dumps({"conversations": convs}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
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
            out.append(
                {
                    "id": c["id"],
                    "title": c.get("title", DEFAULT_TITLE),
                    "updated_at": c.get("updated_at", 0),
                    "document_count": len(c.get("documents") or []),
                    "preview": preview,
                }
            )
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
            c["title"] = (title or "").strip()[:60] or DEFAULT_TITLE
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

    def append_message(
        self, owner: str, conv_id: str, role: str, content: str
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            convs = self._load()
            c = convs.get(conv_id)
            if not c or c.get("owner") != owner:
                return None
            now = int(time.time())
            c["messages"].append({"role": role, "content": content, "at": now})
            # 首条 user 消息触发自动 title（仅当标题仍是默认值）
            if role == "user" and c.get("title", DEFAULT_TITLE) == DEFAULT_TITLE:
                c["title"] = (content or "").strip()[:_TITLE_LEN] or DEFAULT_TITLE
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
            c["documents"] = [
                d for d in c.get("documents", []) if d.get("name") != name
            ]
            c["updated_at"] = int(time.time())
            self._save(convs)
