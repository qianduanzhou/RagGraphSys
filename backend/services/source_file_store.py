"""Original source-file storage for direct document answering."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from threading import Lock
from typing import Any


_MAX_SAFE_NAME = 120


def _safe_part(value: str | None, fallback: str) -> str:
    text = (value or "").strip() or fallback
    text = re.sub(r"[^0-9A-Za-z._-]+", "_", text)
    return text[:80].strip("._-") or fallback


def _stored_filename(name: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", name or "upload")
    safe = safe.strip("._-") or "upload"
    safe = safe[-_MAX_SAFE_NAME:]
    digest = hashlib.sha256((name or "upload").encode("utf-8")).hexdigest()[:16]
    return f"{digest}-{safe}"


class SourceFileStore:
    """Store raw uploaded files under owner / conversation scopes."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def _scope_dir(self, owner: str | None, conversation_id: str | None) -> Path:
        owner_part = _safe_part(owner, "anonymous")
        conv_part = _safe_part(conversation_id, "_global")
        return self.root / owner_part / conv_part

    def _meta_path(self, owner: str | None, conversation_id: str | None) -> Path:
        return self._scope_dir(owner, conversation_id) / "manifest.json"

    def _load_manifest(self, owner: str | None, conversation_id: str | None) -> dict[str, Any]:
        path = self._meta_path(owner, conversation_id)
        if not path.exists():
            return {"files": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"files": {}}
        files = data.get("files") if isinstance(data, dict) else None
        return {"files": files if isinstance(files, dict) else {}}

    def _save_manifest(
        self,
        owner: str | None,
        conversation_id: str | None,
        manifest: dict[str, Any],
    ) -> None:
        scope = self._scope_dir(owner, conversation_id)
        scope.mkdir(parents=True, exist_ok=True)
        path = self._meta_path(owner, conversation_id)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def save(
        self,
        owner: str | None,
        conversation_id: str | None,
        name: str,
        raw: bytes,
    ) -> dict[str, Any]:
        """Save or replace one raw source file in a scope."""

        with self._lock:
            scope = self._scope_dir(owner, conversation_id)
            scope.mkdir(parents=True, exist_ok=True)
            manifest = self._load_manifest(owner, conversation_id)
            stored = _stored_filename(name)
            path = scope / stored
            path.write_bytes(raw)
            item = {
                "name": name,
                "stored": stored,
                "size": len(raw),
                "at": int(time.time()),
            }
            manifest.setdefault("files", {})[name] = item
            self._save_manifest(owner, conversation_id, manifest)
            return dict(item)

    def list(self, owner: str | None, conversation_id: str | None) -> list[dict[str, Any]]:
        manifest = self._load_manifest(owner, conversation_id)
        files = [dict(item) for item in manifest.get("files", {}).values()]
        files.sort(key=lambda item: (int(item.get("at") or 0), item.get("name") or ""), reverse=True)
        return files

    def read(self, owner: str | None, conversation_id: str | None, name: str) -> bytes:
        manifest = self._load_manifest(owner, conversation_id)
        item = manifest.get("files", {}).get(name)
        if not item:
            raise FileNotFoundError(name)
        stored = item.get("stored")
        if not stored:
            raise FileNotFoundError(name)
        path = self._scope_dir(owner, conversation_id) / str(stored)
        return path.read_bytes()

    def delete(self, owner: str | None, conversation_id: str | None, name: str) -> None:
        with self._lock:
            manifest = self._load_manifest(owner, conversation_id)
            item = manifest.get("files", {}).pop(name, None)
            if item and item.get("stored"):
                path = self._scope_dir(owner, conversation_id) / str(item["stored"])
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            self._save_manifest(owner, conversation_id, manifest)

    def delete_scope(self, owner: str | None, conversation_id: str | None) -> None:
        with self._lock:
            scope = self._scope_dir(owner, conversation_id)
            if scope.exists():
                shutil.rmtree(scope, ignore_errors=True)
