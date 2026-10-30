import json

from fastapi.testclient import TestClient

from src import api, llm, suggest

c = TestClient(api.app)
U = "tester-123"


def sse(resp):
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_health_config_and_home():
    assert c.get("/health").json() == {"ok": True}
    cfg = c.get("/api/config").json()
    assert cfg["demo"] is True and "stack" in cfg["categories"]
    r = c.get("/")
    assert r.status_code == 200 and "Kumbuka" in r.text


def test_validation():
    assert c.get("/api/memories").status_code == 422
    assert c.post("/api/chat", json={"user_id": "x", "message": "hi"}).status_code == 422
    assert c.post("/api/chat", json={"user_id": U, "message": ""}).status_code == 422
    assert c.get("/api/memories", params={"user_id": "bad id!"}).status_code == 422


def test_stream_endpoint_and_conversation_lifecycle():
    r = c.post("/api/chat/stream", json={"user_id": U, "message": "I build CLIs in Rust"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = sse(r)
    assert events[0]["type"] == "start" and events[-1]["type"] == "done"
    cid = events[0]["conversation_id"]

    convs = c.get("/api/conversations", params={"user_id": U}).json()
    assert [x["id"] for x in convs] == [cid] and convs[0]["message_count"] == 2

    detail = c.get(f"/api/conversations/{cid}", params={"user_id": U}).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]

    # continue the same conversation
    events = sse(c.post("/api/chat/stream", json={"user_id": U, "message": "what do I build?", "conversation_id": cid}))
    assert events[-1]["conversation_id"] == cid

    assert c.patch(f"/api/conversations/{cid}", params={"user_id": U}, json={"title": "Rust CLIs", "pinned": True}).json()["title"] == "Rust CLIs"
    # another visitor cannot see or use it
    assert c.get(f"/api/conversations/{cid}", params={"user_id": "someone-else"}).status_code == 404
    assert c.post("/api/chat/stream", json={"user_id": "someone-else", "message": "hi", "conversation_id": cid}).status_code == 404
    assert c.delete(f"/api/conversations/{cid}", params={"user_id": U}).status_code == 200
    assert c.get("/api/conversations", params={"user_id": U}).json() == []


def test_blocking_chat_endpoint_keeps_v1_fields():
    d = c.post("/api/chat", json={"user_id": U, "message": "I prefer tabs over spaces"}).json()
    assert {"reply", "memories_used", "memories_saved", "metrics", "conversation_id"} <= set(d)
    assert d["memories_saved"] == ["I prefer tabs over spaces"]


def test_memory_crud_search_and_export():
    p = {"user_id": U}
    row = c.post("/api/memories", params=p, json={"content": "I deploy on Fly.io", "category": "stack"}).json()
    assert row["source"] == "manual"
    assert c.post("/api/memories", params=p, json={"content": "I deploy on Fly.io", "category": "stack"}).status_code == 409
    c.post("/api/memories", params=p, json={"content": "I enjoy hiking on weekends", "category": "personal"})

    assert len(c.get("/api/memories", params=p).json()) == 2
    assert [m["content"] for m in c.get("/api/memories", params={**p, "category": "stack"}).json()] == ["I deploy on Fly.io"]
    hits = c.get("/api/memories", params={**p, "q": "where do I deploy"}).json()
    assert hits[0]["id"] == row["id"] and "score" in hits[0]

    edited = c.patch(f"/api/memories/{row['id']}", params=p, json={"content": "I deploy on Render", "category": "constraint"}).json()
    assert edited["content"] == "I deploy on Render" and edited["category"] == "constraint"
    assert c.patch("/api/memories/not-a-real-id", params=p, json={"content": "nope"}).status_code == 404

    exp = c.get("/api/memories/export", params=p)
    assert "attachment" in exp.headers["content-disposition"] and exp.json()["count"] == 2

    assert c.delete(f"/api/memories/{row['id']}", params={"user_id": "someone-else"}).status_code == 404
    assert c.delete(f"/api/memories/{row['id']}", params=p).status_code == 200
    assert c.delete("/api/memories", params=p).status_code == 400  # needs confirm
    assert c.delete("/api/memories", params={**p, "confirm": "true"}).json() == {"deleted": 1}


def test_stats():
    c.post("/api/chat", json={"user_id": U, "message": "I use Django and Celery"})
    c.post("/api/chat", json={"user_id": U, "message": "How do I schedule Django Celery tasks?"})
    s = c.get("/api/stats", params={"user_id": U}).json()
    assert s["memories"] >= 1 and s["turns"] == 2 and s["conversations"] == 2
    assert s["lookup_ms"]["p50"] is not None and len(s["lookup_ms"]["recent"]) == 2
    assert s["by_category"]["stack"] >= 1


def test_rate_limit(monkeypatch):
    monkeypatch.setattr(api.config, "CHAT_PER_HOUR", 1)
    assert c.post("/api/chat", json={"user_id": U, "message": "hi"}).status_code == 200
    r = c.post("/api/chat", json={"user_id": U, "message": "hi again"})
    assert r.status_code == 429 and "limit" in r.json()["detail"]


def test_suggestions_are_cleaned(monkeypatch):
    raw = '{"suggestions": ["  How do I add tests?  ", "", "x", "Show me an example", "What about auth?", "Another one"]}'
    monkeypatch.setattr(llm, "generate", lambda *a, **k: raw)
    assert suggest.next_prompts("I use FastAPI", "Nice choice.") == ["How do I add tests?", "Show me an example", "What about auth?"]


def test_suggest_endpoint_never_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("model down")

    monkeypatch.setattr(api, "next_prompts", boom)
    r = c.post("/api/suggest", json={"user_id": U, "message": "hi", "reply": "hello"})
    assert r.status_code == 200 and r.json() == {"suggestions": []}


def test_cors_allows_the_github_pages_site():
    r = c.options("/api/chat/stream", headers={
        "Origin": "https://valentinakiyungi.github.io",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://valentinakiyungi.github.io"
    r = c.get("/api/config", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_installable_app_files_are_served():
    sw = c.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    m = c.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.headers["content-type"].startswith("application/manifest+json")
    manifest = m.json()
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert c.get("/" + icon["src"]).status_code == 200
    assert 'rel="manifest"' in c.get("/").text


def test_vector_store_outage_does_not_break_startup_or_cors(monkeypatch):
    def down():
        raise ConnectionError("cluster suspended")

    monkeypatch.setattr(api, "store", down)
    r = c.get("/api/config", headers={"Origin": "https://valentinakiyungi.github.io"})
    assert r.status_code == 200
    assert r.json()["vector_backend"] == "unavailable" and "cluster suspended" in r.json()["vector_error"]
    assert r.headers["access-control-allow-origin"] == "https://valentinakiyungi.github.io"


def test_unexpected_crash_still_returns_json_with_cors(monkeypatch):
    def boom(user_id):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(api.sessions(), "list", boom)
    r = c.get("/api/conversations", params={"user_id": U}, headers={"Origin": "https://valentinakiyungi.github.io"})
    assert r.status_code == 500 and "kaboom" in r.json()["detail"]
    assert r.headers["access-control-allow-origin"] == "https://valentinakiyungi.github.io"
