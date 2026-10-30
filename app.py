"""Terminal chat. Same agent and stores as the web app (stop the server first in local Qdrant mode)."""
import os

from src import llm
from src.agent import turn_stream
from src.memory import list_memories

HELP = "Commands: /memories  /new  /help  exit"


def main() -> None:
    user_id = os.getenv("MEMORY_USER_ID", "terminal-user")
    info = llm.provider_info()
    print(f"Kumbuka · {info['provider']} ({info['model']}) · user {user_id}\n{HELP}\n")
    cid = None
    while True:
        try:
            text = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nKwaheri!")
            break
        if not text:
            continue
        if text.lower() in {"exit", "quit"}:
            print("Kwaheri!")
            break
        if text == "/help":
            print(HELP)
            continue
        if text == "/new":
            cid = None
            print("  (started a new conversation)")
            continue
        if text == "/memories":
            rows = list_memories(user_id)
            print("\n".join(f"  [{r['category']}] {r['content']}" for r in rows) or "  (no memories yet)")
            continue
        print("\nKumbuka: ", end="", flush=True)
        for ev in turn_stream(user_id, text, cid):
            t = ev["type"]
            if t == "start":
                cid = ev["conversation_id"]
            elif t == "token":
                print(ev["text"], end="", flush=True)
            elif t == "error":
                print(f"\n[error] {ev['message']}")
            elif t == "done":
                m = ev["metrics"]
                print()
                if ev["recalled"]:
                    print(f"  · recalled {len(ev['recalled'])} memories in {m.get('lookup_ms', 0):.1f} ms")
                for s in ev["saved"]:
                    print(f"  · remembered [{s['category']}] {s['content']}")
                if m.get("has_summary"):
                    print(f"  · prompt {m['saved_pct']}% smaller than the full transcript")
        print()


if __name__ == "__main__":
    main()
