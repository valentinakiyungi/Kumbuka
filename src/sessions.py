"""Short-term (session) tier: conversations, their messages and their running summary.

Stored in SQLite so chats survive restarts. Each conversation keeps:
  * every message (for the UI and for metrics), and
  * a running summary plus `summarized_upto`, the id of the last message folded into it.
Only messages after `summarized_upto` are sent to the model verbatim.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from src import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT 'New chat',
    summary TEXT NOT NULL DEFAULT '',
    summarized_upto INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS conv_user ON conversations(user_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS msg_conv ON messages(conversation_id, id);
"""


class SessionStore:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()

    def _q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            cur = self.db.execute(sql, args)
            rows = cur.fetchall()
            self.db.commit()
            return rows

    # -- conversations -----------------------------------------------------
    def create(self, user_id: str, title: str = "New chat") -> dict:
        now = time.time()
        cid = uuid.uuid4().hex
        self._q("INSERT INTO conversations(id,user_id,title,created_at,updated_at) VALUES (?,?,?,?,?)",
                (cid, user_id, title, now, now))
        return self.get(user_id, cid)

    def get(self, user_id: str, cid: str) -> Optional[dict]:
        r = self._q("SELECT * FROM conversations WHERE id=? AND user_id=?", (cid, user_id))
        return dict(r[0]) if r else None

    def list(self, user_id: str) -> list[dict]:
        rows = self._q(
            """SELECT c.id, c.title, c.pinned, c.created_at, c.updated_at,
                      (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count,
                      (SELECT content FROM messages m WHERE m.conversation_id=c.id
                         ORDER BY id DESC LIMIT 1) AS last_message
               FROM conversations c WHERE c.user_id=? ORDER BY c.pinned DESC, c.updated_at DESC""",
            (user_id,),
        )
        out = []
        for r in rows:
            d = dict(r)
            d["last_message"] = (d["last_message"] or "")[:120]
            d["pinned"] = bool(d["pinned"])
            out.append(d)
        return out

    def update(self, user_id: str, cid: str, **fields) -> Optional[dict]:
        allowed = {k: v for k, v in fields.items() if k in {"title", "summary", "summarized_upto", "pinned"}}
        if allowed:
            sets = ", ".join(f"{k}=?" for k in allowed)
            self._q(f"UPDATE conversations SET {sets}, updated_at=? WHERE id=? AND user_id=?",
                    (*allowed.values(), time.time(), cid, user_id))
        return self.get(user_id, cid)

    def delete(self, user_id: str, cid: str) -> bool:
        with self.lock:
            cur = self.db.execute("DELETE FROM conversations WHERE id=? AND user_id=?", (cid, user_id))
            self.db.commit()
            return cur.rowcount > 0

    def delete_all(self, user_id: str) -> int:
        with self.lock:
            cur = self.db.execute("DELETE FROM conversations WHERE user_id=?", (user_id,))
            self.db.commit()
            return cur.rowcount

    # -- messages ------------------------------------------------------------
    def add_message(self, cid: str, role: str, content: str, meta: Optional[dict] = None) -> dict:
        now = time.time()
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO messages(conversation_id,role,content,meta,created_at) VALUES (?,?,?,?,?)",
                (cid, role, content, json.dumps(meta or {}), now),
            )
            self.db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))
            self.db.commit()
            mid = cur.lastrowid
        return {"id": mid, "role": role, "content": content, "meta": meta or {}, "created_at": now}

    def set_meta(self, message_id: int, meta: dict) -> None:
        self._q("UPDATE messages SET meta=? WHERE id=?", (json.dumps(meta), message_id))

    def messages(self, cid: str, after_id: int = 0) -> list[dict]:
        rows = self._q("SELECT * FROM messages WHERE conversation_id=? AND id>? ORDER BY id", (cid, after_id))
        return [{**dict(r), "meta": json.loads(r["meta"] or "{}")} for r in rows]

    def transcript_chars(self, cid: str) -> int:
        r = self._q("SELECT COALESCE(SUM(LENGTH(content)),0) AS n FROM messages WHERE conversation_id=?", (cid,))
        return int(r[0]["n"])

    def turn_metrics(self, user_id: str) -> list[dict]:
        rows = self._q(
            """SELECT m.meta FROM messages m JOIN conversations c ON c.id=m.conversation_id
               WHERE c.user_id=? AND m.role='assistant' ORDER BY m.id DESC LIMIT 200""",
            (user_id,),
        )
        return [json.loads(r["meta"]).get("metrics", {}) for r in rows]


_store: Optional[SessionStore] = None
_lock = threading.Lock()


def sessions() -> SessionStore:
    global _store
    with _lock:
        if _store is None:
            _store = SessionStore(config.DB_PATH)
        return _store


def reset(path: Optional[str] = None) -> SessionStore:
    """Swap the store (used by tests)."""
    global _store
    with _lock:
        _store = SessionStore(path or config.DB_PATH)
        return _store
