"""One chat turn, end to end: load session state, run the graph, persist the result."""
from __future__ import annotations

import logging
import re
import time
from typing import Iterator, Optional

from src import graph, llm
from src.sessions import sessions

log = logging.getLogger("kumbuka.agent")


class NotFound(Exception):
    pass


def _title_from(message: str) -> str:
    text = re.sub(r"[`*_#>\[\]]", "", " ".join(message.split()))
    text = re.sub(r"^(hi|hello|hey|so|ok|okay)[,!. ]+", "", text, flags=re.I)
    if len(text) <= 42:
        return text[:1].upper() + text[1:] if text else "New chat"
    cut = text[:42].rsplit(" ", 1)[0]
    return (cut[:1].upper() + cut[1:]).rstrip(",.;:") + "…"


def turn_stream(user_id: str, message: str, conversation_id: Optional[str] = None) -> Iterator[dict]:
    """Yield UI events for one turn. The last event is either `done` or `error`."""
    store = sessions()
    conv = store.get(user_id, conversation_id) if conversation_id else None
    if conversation_id and not conv:
        raise NotFound("Conversation not found")
    is_new = conv is None
    if is_new:
        conv = store.create(user_id, _title_from(message))
    cid = conv["id"]

    history = [{"id": m["id"], "role": m["role"], "content": m["content"]}
               for m in store.messages(cid, after_id=conv["summarized_upto"])]
    transcript_chars = store.transcript_chars(cid)
    user_msg = store.add_message(cid, "user", message)
    if not is_new and len(history) == 0 and conv["title"] == "New chat":
        store.update(user_id, cid, title=_title_from(message))

    yield {"type": "start", "conversation_id": cid, "title": store.get(user_id, cid)["title"],
           "user_message_id": user_msg["id"], "new": is_new}

    t0 = time.perf_counter()
    state = {
        "user_id": user_id, "conversation_id": cid, "message": message,
        "user_message_id": user_msg["id"], "summary": conv["summary"], "history": history,
        "transcript_chars": transcript_chars, "errors": [],
    }
    final: dict = {}
    for kind, payload in graph.run_stream(state):
        if kind == "event":
            yield payload
        elif kind == "error":
            yield {"type": "error", "message": f"The model call failed: {payload}"}
            return
        else:
            final = payload

    reply = final.get("reply", "")
    metrics = dict(final.get("metrics", {}))
    metrics.update(final.get("timings", {}))
    metrics["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    meta = {
        "recalled": final.get("recalled", []),
        "saved": final.get("saved", []),
        "metrics": metrics,
        "model": llm.provider_info()["model"],
        "errors": final.get("errors", []),
    }
    comp = final.get("compression") or {}
    if comp.get("folded"):
        meta["compression"] = {k: comp[k] for k in ("folded", "before_chars", "after_chars")}
        store.update(user_id, cid, summary=comp["summary"], summarized_upto=comp["summarized_upto"])
    bot = store.add_message(cid, "assistant", reply, meta)
    yield {"type": "done", "conversation_id": cid, "message_id": bot["id"], "reply": reply, **meta}


def chat(user_id: str, message: str, conversation_id: Optional[str] = None) -> dict:
    """Blocking version of a turn (CLI, tests, the plain JSON endpoint)."""
    out: dict = {}
    for ev in turn_stream(user_id, message, conversation_id):
        if ev["type"] == "error":
            raise RuntimeError(ev["message"])
        if ev["type"] == "done":
            out = ev
    return {
        "conversation_id": out["conversation_id"],
        "reply": out["reply"],
        "memories_used": [m["content"] for m in out["recalled"]],
        "memories_saved": [m["content"] for m in out["saved"]],
        "recalled": out["recalled"],
        "saved": out["saved"],
        "metrics": out["metrics"],
        "memory_error": "; ".join(out.get("errors", [])),
    }
