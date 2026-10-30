"""Vector tier storage.

`QdrantStore` is the production backend (local files, a Qdrant server, or Qdrant Cloud).
`MemoryStore` is a small numpy-backed stand-in with the same interface, used by the
test-suite and when `VECTOR_BACKEND=memory` (or when qdrant-client is not installed).

Every method is scoped by `user_id` so one visitor can never read another's memories.
"""
from __future__ import annotations

import atexit
import logging
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from src import config

log = logging.getLogger("kumbuka.vectors")


@dataclass
class Hit:
    id: str
    score: float
    payload: dict


class QdrantStore:
    backend = "qdrant"

    def __init__(self) -> None:
        from qdrant_client import QdrantClient, models

        self.m = models
        if config.QDRANT_URL:
            self.c = QdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY or None)
            self.location = "server"
        elif config.QDRANT_PATH == ":memory:":
            self.c = QdrantClient(":memory:")
            self.location = "in-memory"
        else:
            Path(config.QDRANT_PATH).mkdir(parents=True, exist_ok=True)
            self.c = QdrantClient(path=config.QDRANT_PATH)
            self.location = "local files"
        self.name = config.COLLECTION_NAME
        self._ensure()

    def _ensure(self) -> None:
        m = self.m
        if not self.c.collection_exists(self.name):
            self.c.create_collection(
                collection_name=self.name,
                vectors_config=m.VectorParams(size=config.VECTOR_SIZE, distance=m.Distance.COSINE),
            )
            if config.QDRANT_URL:  # payload indexes only matter on a Qdrant server
                for field in ("user_id", "category"):
                    try:
                        self.c.create_payload_index(self.name, field_name=field,
                                                    field_schema=m.PayloadSchemaType.KEYWORD)
                    except Exception:
                        pass
        # Warm up the query path so the first real lookup isn't paying one-off setup costs.
        try:
            self.search("__warmup__", [1.0] + [0.0] * (config.VECTOR_SIZE - 1), 1, 0.0)
        except Exception:
            pass
        atexit.register(self._close)

    def _close(self) -> None:
        try:
            self.c.close()
        except Exception:
            pass

    def _filter(self, user_id: str, category: Optional[str] = None):
        m = self.m
        must = [m.FieldCondition(key="user_id", match=m.MatchValue(value=user_id))]
        if category:
            must.append(m.FieldCondition(key="category", match=m.MatchValue(value=category)))
        return m.Filter(must=must)

    def upsert(self, id: str, vector: list[float], payload: dict) -> None:
        self.c.upsert(self.name, points=[self.m.PointStruct(id=id, vector=vector, payload=payload)])

    def search(self, user_id, vector, limit, threshold, category=None) -> list[Hit]:
        res = self.c.query_points(
            collection_name=self.name, query=vector, limit=limit,
            query_filter=self._filter(user_id, category), score_threshold=threshold, with_payload=True,
        ).points
        return [Hit(str(p.id), float(p.score), dict(p.payload)) for p in res]

    def scroll(self, user_id, limit=1000) -> list[Hit]:
        out, offset = [], None
        while len(out) < limit:
            pts, offset = self.c.scroll(self.name, scroll_filter=self._filter(user_id), limit=256,
                                        offset=offset, with_payload=True)
            out += [Hit(str(p.id), 1.0, dict(p.payload)) for p in pts]
            if offset is None:
                break
        return out[:limit]

    def get(self, id: str) -> Optional[Hit]:
        try:
            pts = self.c.retrieve(self.name, ids=[id], with_payload=True)
        except Exception:
            return None
        return Hit(str(pts[0].id), 1.0, dict(pts[0].payload)) if pts else None

    def set_payload(self, id: str, payload: dict) -> None:
        self.c.set_payload(self.name, payload=payload, points=[id])

    def delete(self, ids: list[str]) -> None:
        self.c.delete(self.name, points_selector=self.m.PointIdsList(points=ids))

    def delete_user(self, user_id: str) -> None:
        self.c.delete(self.name, points_selector=self.m.FilterSelector(filter=self._filter(user_id)))


class MemoryStore:
    """Brute-force cosine search over normalised vectors; fine for thousands of points."""

    backend = "in-process"
    location = "in-memory"

    def __init__(self) -> None:
        import numpy as np

        self.np = np
        self.lock = threading.Lock()
        self.vectors: dict[str, "np.ndarray"] = {}
        self.payloads: dict[str, dict] = {}

    def upsert(self, id, vector, payload):
        v = self.np.asarray(vector, dtype="float32")
        v = v / (self.np.linalg.norm(v) or 1.0)
        with self.lock:
            self.vectors[id], self.payloads[id] = v, dict(payload)

    def _ids(self, user_id, category=None):
        return [i for i, p in self.payloads.items()
                if p.get("user_id") == user_id and (not category or p.get("category") == category)]

    def search(self, user_id, vector, limit, threshold, category=None):
        np = self.np
        with self.lock:
            ids = self._ids(user_id, category)
            if not ids:
                return []
            q = np.asarray(vector, dtype="float32")
            q = q / (np.linalg.norm(q) or 1.0)
            scores = np.stack([self.vectors[i] for i in ids]) @ q
            order = np.argsort(-scores)[:limit]
            return [Hit(ids[k], float(scores[k]), dict(self.payloads[ids[k]]))
                    for k in order if scores[k] >= threshold]

    def scroll(self, user_id, limit=1000):
        with self.lock:
            return [Hit(i, 1.0, dict(self.payloads[i])) for i in self._ids(user_id)][:limit]

    def get(self, id):
        with self.lock:
            return Hit(id, 1.0, dict(self.payloads[id])) if id in self.payloads else None

    def set_payload(self, id, payload):
        with self.lock:
            if id in self.payloads:
                self.payloads[id].update(payload)

    def delete(self, ids):
        with self.lock:
            for i in ids:
                self.vectors.pop(i, None)
                self.payloads.pop(i, None)

    def delete_user(self, user_id):
        self.delete(self._ids(user_id))


@lru_cache(maxsize=1)
def store():
    if config.VECTOR_BACKEND == "memory":
        return MemoryStore()
    try:
        return QdrantStore()
    except ImportError:
        log.warning("qdrant-client is not installed; using the in-process vector store (not persistent).")
        return MemoryStore()
