from src import memory


def test_save_and_recall_with_scores_and_timings():
    assert memory.save_memory("alice", "I build services with FastAPI and Qdrant", "stack")
    found, timings = memory.recall("alice", "how should I structure a FastAPI service")
    assert [m["content"] for m in found] == ["I build services with FastAPI and Qdrant"]
    assert found[0]["category"] == "stack" and 0 < found[0]["score"] <= 1
    assert set(timings) == {"embed_ms", "lookup_ms"}


def test_recall_increments_usage_counter():
    memory.save_memory("alice", "I deploy on Fly.io")
    memory.recall("alice", "where do I deploy")
    memory.recall("alice", "deploy target")
    assert memory.list_memories("alice")[0]["recall_count"] == 2


def test_duplicates_are_not_stored_twice():
    assert memory.save_memory("alice", "I prefer Qdrant over Pinecone")
    assert memory.save_memory("alice", "I prefer  Qdrant over Pinecone") is None
    assert len(memory.list_memories("alice")) == 1


def test_unrelated_query_returns_nothing():
    memory.save_memory("alice", "I build services with FastAPI")
    assert memory.recall("alice", "recipe for pancakes")[0] == []


def test_users_are_isolated():
    memory.save_memory("alice", "I build services with FastAPI")
    assert memory.recall("bob", "FastAPI services")[0] == []
    assert memory.list_memories("bob") == []
    assert memory.search("bob", "FastAPI") == []


def test_cannot_touch_another_users_memory():
    mid = memory.save_memory("alice", "I use LangGraph")["id"]
    assert not memory.delete_memory("bob", mid)
    assert memory.update_memory("bob", mid, "hacked") is None
    assert memory.delete_memory("alice", mid)
    assert memory.list_memories("alice") == []


def test_edit_re_embeds_so_search_follows_new_text():
    mid = memory.save_memory("alice", "I write Go microservices", "stack")["id"]
    memory.update_memory("alice", mid, content="I write Rust command line tools")
    top = memory.search("alice", "Rust command line")[0]
    assert top["id"] == mid and top["content"] == "I write Rust command line tools"


def test_unknown_categories_fall_back_to_personal():
    row = memory.save_memory("alice", "I live in Arusha", "hobby")
    assert row["category"] == "personal"
    assert memory.update_memory("alice", row["id"], category="goal")["category"] == "goal"


def test_delete_all_only_affects_one_user():
    memory.save_memory("alice", "I use Postgres")
    memory.save_memory("alice", "I use Redis for caching")
    memory.save_memory("bob", "I use MySQL")
    assert memory.delete_all("alice") == 2
    assert memory.list_memories("alice") == []
    assert len(memory.list_memories("bob")) == 1
