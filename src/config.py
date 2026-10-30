"""Runtime configuration, read once from the environment (and an optional .env file)."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent.parent
if load_dotenv:
    load_dotenv(BASE_DIR / ".env")


def _f(name: str, default: str) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: str) -> int:
    return int(os.getenv(name, default))


# --- LLM -------------------------------------------------------------------
CHAT_MODELS = [
    m.strip()
    for m in os.getenv("GEMINI_MODELS", "gemini-3.8-flash,gemini-3.5-flash,gemini-2.5-flash").split(",")
    if m.strip()
]
EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-001")
VECTOR_SIZE = _i("VECTOR_SIZE", "768")
# "auto": Gemini when a key is present, otherwise the offline demo provider.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").lower()

# --- Long-term memory (vector tier) ------------------------------------------
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
QDRANT_URL = os.getenv("QDRANT_URL", "")
QDRANT_PATH = os.getenv("QDRANT_PATH", str(BASE_DIR / "data" / "qdrant"))
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "kumbuka_memory")
# "qdrant" (default) or "memory" (in-process numpy store, for tests and offline demos)
VECTOR_BACKEND = os.getenv("VECTOR_BACKEND", "qdrant").lower()

DUPLICATE_THRESHOLD = _f("DUPLICATE_THRESHOLD", "0.92")
RECALL_THRESHOLD = _f("RECALL_THRESHOLD", "0.55")
RECALL_LIMIT = _i("RECALL_LIMIT", "4")

# --- Short-term memory (session tier) + compression -------------------------
DB_PATH = os.getenv("KUMBUKA_DB", str(BASE_DIR / "data" / "kumbuka.db"))
HISTORY_WINDOW = _i("HISTORY_WINDOW", "8")           # messages kept verbatim before folding
KEEP_RECENT = _i("KEEP_RECENT", "4")                 # messages left verbatim after a fold
MAX_HISTORY_CHARS = _i("MAX_HISTORY_CHARS", "6000")  # fold early if the window gets long
SUMMARY_MAX_CHARS = _i("SUMMARY_MAX_CHARS", "1200")

# --- API -------------------------------------------------------------------
CHAT_PER_HOUR = _i("CHAT_PER_HOUR", "60")
SUGGEST_PER_HOUR = _i("SUGGEST_PER_HOUR", "120")
# Origins allowed to call the API from a browser (comma-separated), e.g. your GitHub Pages site.
CORS_ORIGINS = [
    o.strip().rstrip("/")
    for o in os.getenv("CORS_ORIGINS", "https://valentinakiyungi.github.io").split(",")
    if o.strip()
]

CATEGORIES = ["preference", "stack", "project", "goal", "constraint", "personal"]


def api_key() -> str:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""


def demo_mode() -> bool:
    """True when no real model is available and the offline demo provider answers instead."""
    if LLM_PROVIDER == "demo":
        return True
    if LLM_PROVIDER == "gemini":
        return False
    return not api_key()
