import pytest

from src import config, llm, sessions, vectorstore


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Every test gets a fresh in-memory vector store, a fresh SQLite DB and the offline demo model."""
    monkeypatch.setattr(config, "LLM_PROVIDER", "demo")
    monkeypatch.setattr(config, "QDRANT_URL", "")
    monkeypatch.setattr(config, "QDRANT_PATH", ":memory:")
    monkeypatch.setattr(config, "RECALL_THRESHOLD", 0.1)
    llm.provider.cache_clear()
    vectorstore.store.cache_clear()
    sessions.reset(":memory:")
    from src import api
    api._hits.clear()
    yield
    llm.provider.cache_clear()
    vectorstore.store.cache_clear()
