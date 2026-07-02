"""Small JSON-backed account and token service."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict


USERNAME_RE = re.compile(r"^[A-Za-z0-9]{5,}$")
PASSWORD_RE = re.compile(r"^[\x21-\x7E]+$")
PBKDF2_ITERATIONS = 160_000


class AuthError(ValueError):
    """Raised for validation or credential failures."""


class AuthService:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._lock = Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.db_path.exists():
            self._save({"users": {}, "tokens": {}})

    def register(self, username: str, password: str) -> Dict[str, str]:
        username = self._validate_username(username)
        self._validate_password(password)
        with self._lock:
            db = self._load()
            if username in db["users"]:
                raise AuthError("该账号已存在")
            salt = secrets.token_hex(16)
            db["users"][username] = {
                "salt": salt,
                "password_hash": self._hash_password(password, salt),
                "created_at": int(time.time()),
            }
            token = self._create_token(db, username)
            self._save(db)
        return {"username": username, "token": token}

    def login(self, username: str, password: str) -> Dict[str, str]:
        username = (username or "").strip()
        with self._lock:
            db = self._load()
            user = db["users"].get(username)
            if not user:
                raise AuthError("账号或密码错误")
            expected = user.get("password_hash", "")
            actual = self._hash_password(password or "", user.get("salt", ""))
            if not hmac.compare_digest(expected, actual):
                raise AuthError("账号或密码错误")
            token = self._create_token(db, username)
            self._save(db)
        return {"username": username, "token": token}

    def verify_token(self, token: str) -> str:
        token = (token or "").strip()
        if not token:
            raise AuthError("请先登录")
        db = self._load()
        record = db.get("tokens", {}).get(self._token_hash(token))
        if not record:
            raise AuthError("登录已失效，请重新登录")
        username = record.get("username", "")
        if username not in db.get("users", {}):
            raise AuthError("登录已失效，请重新登录")
        return username

    def _create_token(self, db: Dict[str, Any], username: str) -> str:
        token = secrets.token_urlsafe(32)
        db.setdefault("tokens", {})[self._token_hash(token)] = {
            "username": username,
            "created_at": int(time.time()),
        }
        return token

    def _load(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.db_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        users = data.get("users") if isinstance(data, dict) else {}
        tokens = data.get("tokens") if isinstance(data, dict) else {}
        return {
            "users": users if isinstance(users, dict) else {},
            "tokens": tokens if isinstance(tokens, dict) else {},
        }

    def _save(self, db: Dict[str, Any]) -> None:
        tmp = self.db_path.with_suffix(self.db_path.suffix + ".tmp")
        tmp.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.db_path)

    @staticmethod
    def _validate_username(username: str) -> str:
        username = (username or "").strip()
        if not USERNAME_RE.fullmatch(username):
            raise AuthError("账号至少 5 位，只能使用数字和字母")
        return username

    @staticmethod
    def _validate_password(password: str) -> None:
        password = password or ""
        if len(password) <= 8:
            raise AuthError("密码需超过 8 位")
        if not PASSWORD_RE.fullmatch(password):
            raise AuthError("密码只能使用数字、字母和英文符号")

    @staticmethod
    def _hash_password(password: str, salt: str) -> str:
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            PBKDF2_ITERATIONS,
        )
        return digest.hex()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
