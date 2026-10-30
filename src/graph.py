"""The agent, as a LangGraph state machine.

        ┌────────┐      ┌──────────┐
 START ─▶ recall ├──┬──▶ respond  ├──┐
        └────────┘  │   └──────────┘  │   ┌──────────┐
                    │   ┌──────────┐  ├──▶ compress ├─▶ END
                    └──▶ remember ├──┘   └──────────┘
                        └──────────┘

* recall   – embed the new message and look up relevant long-term memories (Qdrant).
* respond  – answer from: recalled memories + running summary + recent window. Streams tokens.
* remember – in parallel with respond, extract lasting facts from the user's message and store them.
* compress – when the verbatim window grows too long, fold older turns into the running summary.

Nodes report progress through a stream writer, so the API can push live events to the browser.
If LangGraph is not installed the same nodes run through a tiny fallback runner.
"""
from __future__ import annotations

import contextvars
import logging
import operator
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any, Iterator

from typing_extensions import TypedDict

from src import config, llm, memory

log = logging.getLogger("kumbuka.graph")

try:  # pragma: no cover - exercised implicitly
    from langgraph.graph import END, START, StateGraph

    try:
        from langgraph.config import get_stream_writer
    except ImportError:
        get_stream_writer = None
    HAVE_LANGGRAPH = True
except ImportError:  # pragma: no cover
    HAVE_LANGGRAPH = False
    get_stream_writer = None


class AgentState(TypedDict, total=False):
    user_id: str
    conversation_id: str
    message: str
    user_message_id: int
    summary: str
    history: list[dict]          # unsummarised earlier messages: {id, role, content}
    transcript_chars: int        # size of the full conversation before this turn
    recalled: list[dict]
    timings: dict
    reply: str
    saved: list[dict]
    metrics: dict
    compression: dict
    errors: Annotated[list[str], operator.add]


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------
_writer: contextvars.ContextVar = contextvars.ContextVar("kumbuka_writer", default=None)


def emit(event: dict) -> None:
    w = _writer.get()
    if w is not None:
        w(event)
        return
    if get_stream_writer is not None:
        try:
            get_stream_writer()(event)
        except Exception:
            pass  # invoked outside a streaming run


# ---------------------------------------------------------------------------
# Prompting
# ---------------------------------------------------------------------------
BASE_SYSTEM = """You are Kumbuka, a sharp and friendly AI collaborator for software developers.
"Kumbuka" means "remember" in Swahili: you remember people across conversations.

Guidelines:
- Use remembered facts naturally when they help (e.g. answer in their stack). Don't recite them.
- If something the user says now conflicts with a remembered fact, trust the new message.
- Never invent facts about the user.
- Be concise and concrete, like a senior colleague. Use Markdown; put code in fenced blocks with a language tag."""


def build_system(recalled: list[dict], summary: str) -> str:
    facts = "\n".join(f"- {m['content']} [{m['category']}]" for m in recalled) or "Nothing relevant yet."
    return (
        f"{BASE_SYSTEM}\n\nWHAT YOU REMEMBER ABOUT THE USER (long-term memory, most relevant first):\n{facts}"
        f"\n\nCONVERSATION SUMMARY (earlier turns of this chat, compressed):\n{summary or '(none yet)'}"
    )


def build_window(history: list[dict], message: str) -> list[dict]:
    window = [{"role": m["role"], "content": m["content"]} for m in history]
    window.append({"role": "user", "content": message})
    while window and window[0]["role"] != "user":  # Gemini wants the first turn from the user
        window.pop(0)
    return window


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
def recall_node(state: AgentState) -> dict:
    try:
        found, timings = memory.recall(state["user_id"], state["message"])
    except Exception as e:  # memory trouble must never break the chat
        log.warning("Recall failed: %s", e)
        emit({"type": "recall", "memories": [], "timings": {}, "error": str(e)[:200]})
        return {"recalled": [], "timings": {}, "errors": [f"recall: {type(e).__name__}: {str(e)[:150]}"]}
    emit({"type": "recall", "memories": found, "timings": timings})
    return {"recalled": found, "timings": timings}


def respond_node(state: AgentState) -> dict:
    recalled = state.get("recalled", [])
    summary = state.get("summary", "")
    history = state.get("history", [])
    system = build_system(recalled, summary)
    window = build_window(history, state["message"])

    prompt_chars = len(system) + sum(len(m["content"]) for m in window)
    # Baseline: what a naive agent would send - the entire transcript, no summary.
    baseline_chars = (len(build_system(recalled, "")) + state.get("transcript_chars", 0)
                      + len(state["message"]))
    baseline_chars = max(baseline_chars, prompt_chars)
    metrics = {
        "prompt_chars": prompt_chars,
        "baseline_chars": baseline_chars,
        "prompt_tokens": llm.approx_tokens(prompt_chars),
        "baseline_tokens": llm.approx_tokens(baseline_chars),
        "saved_pct": round(100 * (1 - prompt_chars / baseline_chars), 1) if baseline_chars else 0.0,
        "window_messages": len(window),
        "has_summary": bool(summary),
    }
    emit({"type": "context", "metrics": metrics})

    parts: list[str] = []
    for piece in llm.stream(window, system=system, temperature=0.7, task="reply"):
        parts.append(piece)
        emit({"type": "token", "text": piece})
    reply = "".join(parts).strip()
    if not reply:
        reply = "I couldn't produce an answer that time. Please try again."
        emit({"type": "token", "text": reply})
    return {"reply": reply, "metrics": metrics}


EXTRACT_PROMPT = """Extract lasting facts the user stated about THEMSELVES in the message below:
preferences, tech stack, current projects, goals, constraints, or relevant personal context.

Rules:
- Each fact is one short, self-contained sentence in the first person (e.g. "I deploy on Fly.io").
- Pick a category for each: preference, stack, project, goal, constraint, or personal.
- Ignore questions, small talk, one-off requests ("write me a regex"), and anything not about the user.
- Return an empty list if there is nothing worth remembering for future conversations.

USER MESSAGE:
{message}"""


def remember_node(state: AgentState) -> dict:
    text = state.get("message", "")
    if not text.strip():
        return {"saved": []}
    try:
        raw = llm.generate(EXTRACT_PROMPT.format(message=text), schema=memory.MemoryExtractor,
                           temperature=0.0, retries=2, task="extract")
        facts = memory.MemoryExtractor.model_validate_json(raw).memories
        saved = []
        for f in facts[:6]:
            row = memory.save_memory(state["user_id"], f.content, f.category,
                                     conversation_id=state.get("conversation_id"))
            if row:
                saved.append(row)
        emit({"type": "saved", "memories": saved})
        return {"saved": saved}
    except Exception as e:
        log.warning("Memory extraction failed: %s", e)
        emit({"type": "saved", "memories": [], "error": str(e)[:200]})
        return {"saved": [], "errors": [f"remember: {type(e).__name__}: {str(e)[:150]}"]}


SUMMARY_PROMPT = """Update the running summary of a conversation between a developer (user) and an assistant.
Merge the previous summary with the new messages. Keep: the user's goals, decisions made, preferences
and constraints mentioned, code/tools discussed, and open questions. Drop pleasantries and anything
already resolved. Write compact prose, at most {limit} characters.

PREVIOUS SUMMARY:
{summary}

NEW MESSAGES:
{messages}"""


def compress_node(state: AgentState) -> dict:
    msgs = list(state.get("history", []))
    msgs.append({"id": state.get("user_message_id"), "role": "user", "content": state["message"]})
    msgs.append({"id": None, "role": "assistant", "content": state.get("reply", "")})
    total = sum(len(m["content"]) for m in msgs)
    if len(msgs) <= config.HISTORY_WINDOW and total <= config.MAX_HISTORY_CHARS:
        return {"compression": {"folded": 0}}

    keep = max(2, config.KEEP_RECENT)
    fold = [m for m in msgs[:-keep] if m.get("id")]
    if not fold:
        return {"compression": {"folded": 0}}
    text = "\n".join(f"{m['role']}: {m['content'][:1500]}" for m in fold)
    try:
        summary = llm.generate(
            SUMMARY_PROMPT.format(limit=config.SUMMARY_MAX_CHARS, summary=state.get("summary") or "(none)",
                                  messages=text),
            temperature=0.2, retries=2, task="summarize",
        ).strip()[: config.SUMMARY_MAX_CHARS + 200]
    except Exception as e:
        log.warning("Compression failed: %s", e)
        return {"compression": {"folded": 0}, "errors": [f"compress: {str(e)[:150]}"]}
    info = {"folded": len(fold), "summarized_upto": fold[-1]["id"], "summary": summary,
            "before_chars": sum(len(m["content"]) for m in fold), "after_chars": len(summary)}
    emit({"type": "compressed", "folded": info["folded"], "before_chars": info["before_chars"],
          "after_chars": info["after_chars"]})
    return {"compression": info}


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
NODES = {"recall": recall_node, "respond": respond_node, "remember": remember_node, "compress": compress_node}


def build_graph():
    g = StateGraph(AgentState)
    for name, fn in NODES.items():
        g.add_node(name, fn)
    g.add_edge(START, "recall")
    g.add_edge("recall", "respond")    # respond and remember run in parallel
    g.add_edge("recall", "remember")
    g.add_edge(["respond", "remember"], "compress")  # join
    g.add_edge("compress", END)
    return g.compile()


class _FallbackGraph:
    """Same topology, executed without LangGraph (used only if it is not installed)."""

    def invoke(self, state: dict) -> dict:
        state = {**state, "errors": list(state.get("errors", []))}

        def merge(update):
            errs = update.pop("errors", [])
            state.update(update)
            state["errors"] += errs

        merge(recall_node(state))
        with ThreadPoolExecutor(2) as pool:
            ctx1, ctx2 = contextvars.copy_context(), contextvars.copy_context()
            a = pool.submit(ctx1.run, respond_node, dict(state))
            b = pool.submit(ctx2.run, remember_node, dict(state))
            merge(a.result())
            merge(b.result())
        merge(compress_node(state))
        return state


compiled_graph = build_graph() if HAVE_LANGGRAPH else _FallbackGraph()


def run_stream(state: dict) -> Iterator[tuple[str, Any]]:
    """Run the graph, yielding ("event", dict) live and finally ("final", state)."""
    q: "queue.Queue" = queue.Queue()
    done = object()
    result: dict = {}

    def worker():
        try:
            if HAVE_LANGGRAPH and get_stream_writer is not None:
                # Idiomatic LangGraph streaming: node events arrive on the "custom" channel.
                for mode, chunk in compiled_graph.stream(state, stream_mode=["custom", "values"]):
                    if mode == "custom":
                        q.put(("event", chunk))
                    else:
                        result.clear()
                        result.update(chunk)
            else:
                token = _writer.set(lambda ev: q.put(("event", ev)))
                try:
                    result.update(compiled_graph.invoke(state))
                finally:
                    _writer.reset(token)
        except Exception as e:  # surfaced to the client as an error event
            log.exception("Graph run failed")
            q.put(("error", str(e)))
        finally:
            q.put(done)

    threading.Thread(target=worker, daemon=True).start()
    while True:
        item = q.get()
        if item is done:
            break
        yield item
    if result:
        yield ("final", result)


def run(state: dict) -> dict:
    final: dict = {}
    for kind, payload in run_stream(state):
        if kind == "final":
            final = payload
        elif kind == "error":
            raise RuntimeError(payload)
    return final
