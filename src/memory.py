"""Long-term memory: facts about the user, embedded and stored in the vector tier."""
from __future__ import annotations

import time
import uuid
from typing import Optional

from pydantic import BaseModel, Field

from src import config, llm
from src.vectorstore import store

Category = str  # one of config.CATEGORIES


class MemoryPayload(BaseModel):
    content: str = Field(..., description="One short, self-contained fact the user stated about themselves")
    category: str = Field("personal", description="preference | stack | project | goal | constraint | personal")


class MemoryExtractor(BaseModel):
    memories: list[MemoryPayload]


def _clean_category(c: Optional[str]) -> str:
    c = (c or "").strip().lower()
    return c if c in config.CATEGORIES else "personal"


def _row(hit, score: Optional[float] = None) -> dict:
    p = hit.payload
    row = {
        "id": hit.id,
        "content": p.get("content", ""),
        "category": _clean_category(p.get("category")),
        "created_at": p.get("created_at", 0),
        "updated_at": p.get("updated_at", p.get("created_at", 0)),
        "recall_count": p.get("recall_count", 0),
        "last_recalled": p.get("last_recalled"),
        "source": p.get("source", "chat"),
        "conversation_id": p.get("conversation_id"),
    }
    if score is not None:
        row["score"] = round(score, 3)
    return row


def recall_threshold() -> float:
    # The demo bag-of-words embedder produces lower similarities than Gemini embeddings.
    return config.RECALL_THRESHOLD if not config.demo_mode() else min(config.RECALL_THRESHOLD, 0.2)


def save_memory(user_id: str, text: str, category: str = "personal", source: str = "chat",
                conversation_id: Optional[str] = None) -> Optional[dict]:
    """Store a fact. Returns the stored row, or None if it is empty or a near-duplicate."""
    text = " ".join(text.split()).strip()
    if not text:
        return None
    vec = llm.embed([text], "RETRIEVAL_DOCUMENT")[0]
    dup = store().search(user_id, vec, 1, config.DUPLICATE_THRESHOLD)
    if dup:
        return None
    now = time.time()
    mid = str(uuid.uuid4())
    payload = {
        "user_id": user_id, "content": text, "category": _clean_category(category),
        "created_at": now, "updated_at": now, "recall_count": 0, "source": source,
        "conversation_id": conversation_id,
    }
    store().upsert(mid, vec, payload)
    return _row(type("H", (), {"id": mid, "payload": payload})())


def recall(user_id: str, query: str, limit: int | None = None) -> tuple[list[dict], dict]:
    """Semantic lookup. Returns (memories with scores, timings in ms)."""
    timings = {"embed_ms": 0.0, "lookup_ms": 0.0}
    if not query.strip():
        return [], timings
    t0 = time.perf_counter()
    vec = llm.embed([query], "RETRIEVAL_QUERY")[0]
    t1 = time.perf_counter()
    hits = store().search(user_id, vec, limit or config.RECALL_LIMIT, recall_threshold())
    t2 = time.perf_counter()
    timings = {"embed_ms": round((t1 - t0) * 1000, 2), "lookup_ms": round((t2 - t1) * 1000, 2)}
    now = time.time()
    for h in hits:
        try:
            store().set_payload(h.id, {"recall_count": h.payload.get("recall_count", 0) + 1,
                                       "last_recalled": now})
        except Exception:
            pass
    return [_row(h, h.score) for h in hits], timings


def retrieve_memories(user_id: str, query: str, limit: int = 3) -> list[str]:
    """Backwards-compatible helper: just the recalled texts."""
    return [m["content"] for m in recall(user_id, query, limit)[0]]


def search(user_id: str, query: str, limit: int = 20, category: Optional[str] = None) -> list[dict]:
    vec = llm.embed([query], "RETRIEVAL_QUERY")[0]
    return [_row(h, h.score) for h in store().search(user_id, vec, limit, 0.0, category)]


def list_memories(user_id: str) -> list[dict]:
    rows = [_row(h) for h in store().scroll(user_id)]
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)


def get_memory(user_id: str, memory_id: str) -> Optional[dict]:
    hit = store().get(memory_id)
    if not hit or hit.payload.get("user_id") != user_id:
        return None
    return _row(hit)


def update_memory(user_id: str, memory_id: str, content: Optional[str] = None,
                  category: Optional[str] = None) -> Optional[dict]:
    hit = store().get(memory_id)
    if not hit or hit.payload.get("user_id") != user_id:
        return None
    payload = dict(hit.payload)
    payload["updated_at"] = time.time()
    if category is not None:
        payload["category"] = _clean_category(category)
    if content is not None and " ".join(content.split()) != payload["content"]:
        payload["content"] = " ".join(content.split())
        vec = llm.embed([payload["content"]], "RETRIEVAL_DOCUMENT")[0]
        store().upsert(memory_id, vec, payload)  # re-embed edited text
    else:
        store().set_payload(memory_id, payload)
    return _row(type("H", (), {"id": memory_id, "payload": payload})())


def delete_memory(user_id: str, memory_id: str) -> bool:
    hit = store().get(memory_id)
    if not hit or hit.payload.get("user_id") != user_id:
        return False
    store().delete([memory_id])
    return True


def delete_all(user_id: str) -> int:
    n = len(store().scroll(user_id))
    store().delete_user(user_id)
    return n


def ensure_collection() -> None:
    store()
