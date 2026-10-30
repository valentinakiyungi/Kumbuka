from src import agent, config, graph, llm
from src.sessions import sessions


def run_turn(user, message, cid=None):
    events = list(agent.turn_stream(user, message, cid))
    return events, events[-1]


def test_turn_streams_events_in_a_sensible_order():
    events, done = run_turn("u-1", "I build APIs with FastAPI and deploy on Fly.io.")
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert kinds.index("recall") < kinds.index("context") < kinds.index("token")
    assert "saved" in kinds
    assert done["reply"] == "".join(e["text"] for e in events if e["type"] == "token").strip()


def test_facts_are_extracted_and_recalled_in_a_later_conversation():
    _, first = run_turn("u-2", "I build APIs with FastAPI and Postgres. I prefer short answers.")
    assert {m["category"] for m in first["saved"]} >= {"stack", "preference"}
    # brand-new conversation: the session tier is empty, the vector tier still knows
    _, later = run_turn("u-2", "How do I add migrations to my FastAPI and Postgres app?")
    assert later["conversation_id"] != first["conversation_id"]
    assert any("FastAPI" in m["content"] for m in later["recalled"])
    assert later["metrics"]["lookup_ms"] >= 0


def test_questions_and_requests_are_not_saved():
    _, done = run_turn("u-3", "Can you write me a regex for emails?")
    assert done["saved"] == []


def test_history_is_persisted_and_titled():
    _, done = run_turn("u-4", "Hello! I am building a budgeting app in Flutter")
    conv = sessions().get("u-4", done["conversation_id"])
    assert conv["title"].startswith("I am building a budgeting app")
    msgs = sessions().messages(done["conversation_id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["meta"]["metrics"]["prompt_chars"] > 0


def test_long_conversations_are_compressed(monkeypatch):
    monkeypatch.setattr(config, "HISTORY_WINDOW", 4)
    monkeypatch.setattr(config, "KEEP_RECENT", 2)
    cid = None
    dones = []
    for i in range(6):
        _, done = run_turn("u-5", f"Step {i}: here is a fairly long update about my parser work, item {i}. " * 3, cid)
        cid = done["conversation_id"]
        dones.append(done)
    conv = sessions().get("u-5", cid)
    assert conv["summary"] and conv["summarized_upto"] > 0
    m = dones[-1]["metrics"]
    assert m["has_summary"] and m["prompt_chars"] < m["baseline_chars"] and m["saved_pct"] > 0
    # only the unsummarised tail is sent verbatim
    assert m["window_messages"] <= config.HISTORY_WINDOW + 1


def test_model_failure_becomes_an_error_event(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("quota exceeded")
        yield  # pragma: no cover

    monkeypatch.setattr(llm, "stream", boom)
    events, last = run_turn("u-6", "hello there")
    assert last["type"] == "error" and "quota exceeded" in last["message"]


def test_memory_failure_does_not_break_the_chat(monkeypatch):
    def broken(*a, **k):
        raise ConnectionError("qdrant down")

    monkeypatch.setattr(graph.memory, "recall", broken)
    _, done = run_turn("u-7", "I use Svelte")
    assert done["type"] == "done" and done["reply"]
    assert any("recall" in e for e in done["errors"])


def test_graph_uses_langgraph_when_installed():
    if graph.HAVE_LANGGRAPH:
        assert type(graph.compiled_graph).__name__ == "CompiledStateGraph"
        assert set(graph.compiled_graph.get_graph().nodes) >= {"recall", "respond", "remember", "compress"}
