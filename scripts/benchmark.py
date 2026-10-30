"""Reproducible numbers for vector-lookup latency and prompt compression.

Runs a scripted multi-session conversation through the real agent and reports:
  * Qdrant lookup latency (p50 / p95, excluding the embedding call)
  * prompt size vs. a naive "send the whole transcript" baseline

    python -m scripts.benchmark            # uses Gemini if GEMINI_API_KEY is set, else demo mode
    python -m scripts.benchmark --turns 30 --seed-memories 200
"""
import argparse
import os
import statistics
import sys
import tempfile
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOPICS = [
    "I build APIs with FastAPI and Postgres and deploy them on Fly.io.",
    "I'm working on a CLI that summarises noisy server logs; it must run on Windows too.",
    "I prefer short answers with a code example first.",
    "How should I structure the log parser so new formats are easy to add?",
    "What's a good way to test the parser against huge log files?",
    "Show me how to stream a file line by line without loading it into memory.",
    "How would I expose the summaries through my FastAPI service?",
    "Which Postgres schema would you use to store summaries per host?",
    "How do I add authentication to that endpoint?",
    "Can you write a GitHub Actions workflow for the tests?",
    "How do I cache repeated summaries?",
    "What's the simplest way to ship the CLI as a single binary?",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=24)
    ap.add_argument("--seed-memories", type=int, default=0, help="extra synthetic memories to stress the index")
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="kumbuka-bench-")
    os.environ.setdefault("KUMBUKA_DB", os.path.join(tmp, "bench.db"))
    os.environ.setdefault("QDRANT_PATH", os.path.join(tmp, "qdrant"))

    from src import agent, llm, memory

    user = "bench-" + uuid.uuid4().hex[:8]
    print(f"provider: {llm.provider_info()}  |  data: {tmp}")
    for i in range(args.seed_memories):
        memory.save_memory(user, f"Synthetic fact {i}: I maintain service-{i} written in language-{i % 17}", "stack")

    cid, rows = None, []
    t0 = time.perf_counter()
    for i in range(args.turns):
        msg = TOPICS[i % len(TOPICS)]
        res = agent.chat(user, msg, cid)
        cid = res["conversation_id"]
        rows.append(res["metrics"])
        print(f"turn {i + 1:>2}: lookup {res['metrics'].get('lookup_ms', 0):6.2f} ms  "
              f"prompt {res['metrics']['prompt_tokens']:>5} tok vs {res['metrics']['baseline_tokens']:>5}  "
              f"recalled {len(res['recalled'])}  saved {len(res['saved'])}")
    wall = time.perf_counter() - t0

    look = sorted(m["lookup_ms"] for m in rows if "lookup_ms" in m)
    comp = [m for m in rows if m.get("has_summary")]
    p95 = look[int(0.95 * (len(look) - 1))]
    print("\n--- results ---")
    print(f"vector lookup  p50 {statistics.median(look):.2f} ms   p95 {p95:.2f} ms   max {look[-1]:.2f} ms")
    if comp:
        avg = statistics.mean(m["saved_pct"] for m in comp)
        tok = sum(m["baseline_tokens"] - m["prompt_tokens"] for m in rows)
        print(f"prompt size    {avg:.1f}% smaller on average once compression kicks in ({len(comp)} turns), "
              f"{tok} tokens saved overall")
    else:
        print("prompt size    conversation too short to compress; increase --turns")
    print(f"wall time      {wall:.1f} s for {args.turns} turns")


if __name__ == "__main__":
    main()
