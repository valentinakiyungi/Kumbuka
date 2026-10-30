"""Diagnostics: checks the model, the embedding model and the vector store, step by step."""
import traceback

from src import config, llm, memory
from src.vectorstore import store

info = llm.provider_info()
print("provider:", info)
print("qdrant:", config.QDRANT_URL or config.QDRANT_PATH, "| backend:", config.VECTOR_BACKEND)

try:
    v = llm.embed(["hello"], "RETRIEVAL_DOCUMENT")
    print("embedding OK, dimension", len(v[0]))
except Exception as e:
    print("EMBEDDING ERROR:", e)

try:
    s = store()
    print("vector store OK:", s.backend, getattr(s, "location", ""))
except Exception:
    print("VECTOR STORE ERROR:")
    traceback.print_exc()

try:
    print("generate:", llm.generate("Reply with the single word: ready", temperature=0)[:80])
except Exception as e:
    print("GENERATE ERROR:", e)

try:
    uid = "diag-user-1"
    print("saved:", memory.save_memory(uid, "I build APIs with FastAPI", "stack"))
    found, timings = memory.recall(uid, "What framework do I use?")
    print("recall:", [(m["content"], m["score"]) for m in found], timings)
    print("cleanup:", memory.delete_all(uid), "removed")
except Exception:
    traceback.print_exc()
