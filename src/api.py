"""HTTP API + web app.

All routes live under /api; the single-page app is served from /.
Visitors are identified by an anonymous id generated in their browser.
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict, deque
from pathlib import Path
from statistics import mean, median
from typing import Optional

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src import agent, config, llm, memory
from src.sessions import sessions
from src.suggest import next_prompts
from src.vectorstore import store

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kumbuka")

ID = r"^[A-Za-z0-9_-]+$"
WEB = Path(__file__).resolve().parent.parent / "web"
_hits: dict[tuple, deque] = defaultdict(deque)

app = FastAPI(title="Kumbuka", version="2.0.0",
              description="A memory-augmented conversational agent (LangGraph + Gemini + Qdrant).")
api = APIRouter(prefix="/api")

# Let a separately hosted front end (e.g. GitHub Pages) call this API.
if config.CORS_ORIGINS:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
        max_age=86400,
    )

UserId = Query(..., min_length=3, max_length=64, pattern=ID)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class ChatIn(BaseModel):
    user_id: str = Field(min_length=3, max_length=64, pattern=ID)
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: Optional[str] = Field(None, max_length=64, pattern=ID)


class SuggestIn(BaseModel):
    user_id: str = Field(min_length=3, max_length=64, pattern=ID)
    message: str = Field(min_length=1, max_length=8000)
    reply: str = Field(min_length=1, max_length=20000)


class MemoryIn(BaseModel):
    content: str = Field(min_length=3, max_length=500)
    category: str = "personal"


class MemoryPatch(BaseModel):
    content: Optional[str] = Field(None, min_length=3, max_length=500)
    category: Optional[str] = None


class ConversationIn(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=120)
    pinned: Optional[bool] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _limit(request: Request, bucket: str, limit: int) -> None:
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")
    now = time.time()
    q = _hits[(bucket, ip)]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, f"You've reached the limit of {limit} messages per hour. Try again later.")
    q.append(now)


def _store_error(e: Exception):
    log.exception("Memory store error")
    return HTTPException(503, f"The memory store is unavailable: {type(e).__name__}: {str(e)[:200]}")


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True}


@api.get("/config")
def get_config():
    info = llm.provider_info()
    s = store()
    return {
        **info,
        "vector_backend": s.backend,
        "vector_location": getattr(s, "location", ""),
        "categories": config.CATEGORIES,
        "history_window": config.HISTORY_WINDOW,
        "recall_limit": config.RECALL_LIMIT,
    }


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
@api.post("/chat/stream")
def chat_stream(body: ChatIn, request: Request):
    """Server-sent events: start, recall, context, token*, saved, compressed?, done | error."""
    _limit(request, "chat", config.CHAT_PER_HOUR)
    if body.conversation_id and not sessions().get(body.user_id, body.conversation_id):
        raise HTTPException(404, "Conversation not found")

    def events():
        try:
            for ev in agent.turn_stream(body.user_id, body.message.strip(), body.conversation_id):
                yield _sse(ev)
        except Exception as e:
            log.exception("Chat stream failed")
            yield _sse({"type": "error", "message": f"Unexpected error: {type(e).__name__}"})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@api.post("/chat")
def chat(body: ChatIn, request: Request):
    _limit(request, "chat", config.CHAT_PER_HOUR)
    try:
        return agent.chat(body.user_id, body.message.strip(), body.conversation_id)
    except agent.NotFound:
        raise HTTPException(404, "Conversation not found")
    except RuntimeError as e:
        log.error("Chat failed: %s", e)
        raise HTTPException(502, str(e))


@api.post("/suggest")
def suggest(body: SuggestIn, request: Request):
    _limit(request, "suggest", config.SUGGEST_PER_HOUR)
    try:
        return {"suggestions": next_prompts(body.message, body.reply)}
    except Exception as e:  # suggestions are optional, never an error for the user
        log.warning("Suggestions failed: %s", e)
        return {"suggestions": []}


# ---------------------------------------------------------------------------
# Conversations (session tier)
# ---------------------------------------------------------------------------
@api.get("/conversations")
def list_conversations(user_id: str = UserId):
    return sessions().list(user_id)


@api.post("/conversations")
def new_conversation(user_id: str = UserId):
    return sessions().create(user_id)


@api.get("/conversations/{cid}")
def get_conversation(cid: str, user_id: str = UserId):
    conv = sessions().get(user_id, cid)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    msgs = sessions().messages(cid)
    return {**conv, "messages": msgs,
            "summarized_messages": sum(1 for m in msgs if m["id"] <= conv["summarized_upto"])}


@api.patch("/conversations/{cid}")
def update_conversation(cid: str, body: ConversationIn, user_id: str = UserId):
    fields = {}
    if body.title is not None:
        fields["title"] = body.title.strip()
    if body.pinned is not None:
        fields["pinned"] = int(body.pinned)
    conv = sessions().update(user_id, cid, **fields)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return conv


@api.delete("/conversations/{cid}")
def delete_conversation(cid: str, user_id: str = UserId):
    if not sessions().delete(user_id, cid):
        raise HTTPException(404, "Conversation not found")
    return {"deleted": cid}


@api.delete("/conversations")
def delete_all_conversations(user_id: str = UserId):
    return {"deleted": sessions().delete_all(user_id)}


# ---------------------------------------------------------------------------
# Memories (vector tier)
# ---------------------------------------------------------------------------
@api.get("/memories")
def list_memories(user_id: str = UserId, q: str = Query("", max_length=300),
                  category: Optional[str] = Query(None)):
    try:
        if q.strip():
            return memory.search(user_id, q.strip(), category=category)
        rows = memory.list_memories(user_id)
        return [r for r in rows if not category or r["category"] == category]
    except Exception as e:
        raise _store_error(e)


@api.post("/memories")
def add_memory(body: MemoryIn, user_id: str = UserId):
    try:
        row = memory.save_memory(user_id, body.content, body.category, source="manual")
    except Exception as e:
        raise _store_error(e)
    if not row:
        raise HTTPException(409, "Kumbuka already remembers something almost identical.")
    return row


@api.patch("/memories/{memory_id}")
def edit_memory(memory_id: str, body: MemoryPatch, user_id: str = UserId):
    try:
        row = memory.update_memory(user_id, memory_id, body.content, body.category)
    except Exception as e:
        raise _store_error(e)
    if not row:
        raise HTTPException(404, "Memory not found")
    return row


@api.delete("/memories/{memory_id}")
def forget(memory_id: str, user_id: str = UserId):
    try:
        deleted = memory.delete_memory(user_id, memory_id)
    except Exception as e:
        raise _store_error(e)
    if not deleted:
        raise HTTPException(404, "Memory not found")
    return {"deleted": memory_id}


@api.delete("/memories")
def forget_everything(user_id: str = UserId, confirm: bool = Query(False)):
    if not confirm:
        raise HTTPException(400, "Pass confirm=true to delete every memory.")
    try:
        return {"deleted": memory.delete_all(user_id)}
    except Exception as e:
        raise _store_error(e)


@api.get("/memories/export")
def export_memories(user_id: str = UserId):
    rows = memory.list_memories(user_id)
    return JSONResponse(
        {"exported_at": time.time(), "count": len(rows), "memories": rows},
        headers={"Content-Disposition": 'attachment; filename="kumbuka-memories.json"'},
    )


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------
@api.get("/stats")
def stats(user_id: str = UserId):
    rows = memory.list_memories(user_id)
    turns = [m for m in sessions().turn_metrics(user_id) if m]
    lookups = [m["lookup_ms"] for m in turns if "lookup_ms" in m]
    saved = [m["saved_pct"] for m in turns if "saved_pct" in m]
    # Compression only matters once a conversation is long enough to be folded.
    compressed = [m["saved_pct"] for m in turns if m.get("has_summary")]
    by_cat = Counter(r["category"] for r in rows)
    top = sorted(rows, key=lambda r: r["recall_count"], reverse=True)[:3]
    convs = sessions().list(user_id)
    return {
        "memories": len(rows),
        "by_category": {c: by_cat.get(c, 0) for c in config.CATEGORIES},
        "most_recalled": [r for r in top if r["recall_count"]],
        "conversations": len(convs),
        "turns": len(turns),
        "lookup_ms": {
            "avg": round(mean(lookups), 2) if lookups else None,
            "p50": round(median(lookups), 2) if lookups else None,
            "p95": round(sorted(lookups)[int(0.95 * (len(lookups) - 1))], 2) if lookups else None,
            "recent": list(reversed(lookups[:30])),
        },
        "prompt_saved_pct": {
            "all_turns": round(mean(saved), 1) if saved else None,
            "long_conversations": round(mean(compressed), 1) if compressed else None,
            "recent": list(reversed(saved[:30])),
        },
        "tokens_saved": sum(max(0, m.get("baseline_tokens", 0) - m.get("prompt_tokens", 0)) for m in turns),
    }


app.include_router(api)


# ---------------------------------------------------------------------------
# Legacy routes (v1 clients)
# ---------------------------------------------------------------------------
@app.post("/chat", include_in_schema=False)
def legacy_chat(body: ChatIn, request: Request):
    return chat(body, request)


@app.get("/memories", include_in_schema=False)
def legacy_memories(user_id: str = UserId):
    return memory.list_memories(user_id)


# ---------------------------------------------------------------------------
# Web app
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=WEB / "static", check_dir=False), name="static")


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


# Installable-app files must sit next to index.html so the service worker's scope covers the app.
@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(WEB / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache"})
