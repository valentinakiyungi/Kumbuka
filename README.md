# Kumbuka

**An AI collaborator that remembers you.** *Kumbuka* means "remember" in Swahili.

Tell Kumbuka about your stack, projects and preferences once. It extracts the lasting facts, stores them as vectors, and recalls the relevant ones in every later conversation. Long chats stay fast because older turns are folded into a running summary. You can see, search, edit and delete everything it keeps.

**Stack:** LangGraph · Google Gemini · Qdrant · FastAPI · SQLite · vanilla JS (no build step)

## Highlights

- **Dual-tier memory.** Session tier: every conversation, persisted in SQLite, with a running summary. Vector tier: facts about you in Qdrant, scoped per visitor, de-duplicated, categorised and ranked by similarity.
- **Parallel LangGraph pipeline.** Answering and fact extraction run side by side, so remembering adds no latency to the reply.
- **Context compression.** After `HISTORY_WINDOW` messages, older turns are summarised; only the summary, the recalled facts and the recent tail are sent to the model. Every reply records how much smaller its prompt was than the full transcript.
- **Streaming UI.** Token-by-token replies over server-sent events, with a live *memory trace* under each answer: which memories were recalled, their similarity, and the Qdrant lookup time.
- **Memory you control.** Semantic search, category filters, inline edit (re-embeds the text), manual add, forget one (with undo), forget everything, JSON export.
- **Insights.** Lookup latency (p50/p95 + sparkline), prompt reduction, tokens saved, what Kumbuka knows by category, most-recalled memories.
- **Works offline.** Without an API key it runs in demo mode: templated replies, but real recall, storage and compression, so you can explore the whole app.
- Chat history with search, pinning, renaming; light/dark themes; keyboard shortcuts; responsive down to phones; accessible focus states and reduced-motion support.

## How a turn works

```
          ┌────────┐      ┌──────────┐
 START ──▶│ recall │──┬──▶│ respond  │──┐   ┌──────────┐
          └────────┘  │   └──────────┘  ├──▶│ compress │──▶ END
                      │   ┌──────────┐  │   └──────────┘
                      └──▶│ remember │──┘
                          └──────────┘
```

1. **recall** embeds your message (`RETRIEVAL_QUERY`) and searches your memories in Qdrant.
2. **respond** streams a Gemini answer from: recalled facts + conversation summary + recent messages.
3. **remember** (in parallel) asks Gemini for lasting first-person facts as structured JSON, embeds them, skips near-duplicates (cosine ≥ 0.92) and stores the rest with a category.
4. **compress** folds older turns into the running summary once the window grows past `HISTORY_WINDOW` messages or `MAX_HISTORY_CHARS`.

## Run locally

```bash
git clone https://github.com/valentinakiyungi/Kumbuka-memory-agent.git
cd Kumbuka-memory-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # add GEMINI_API_KEY (or leave empty for demo mode)
python -m pytest -q             # tests need no API key
uvicorn src.api:app --port 8000 # open http://127.0.0.1:8000
```

Other entry points:

```bash
python app.py                   # terminal chat (stop the server first in local-files mode)
python check_memory.py          # diagnose model, embeddings and Qdrant step by step
python -m scripts.benchmark     # measure lookup latency and prompt compression
```

Get a free key at [Google AI Studio](https://aistudio.google.com). Local mode keeps memories in `data/qdrant/` and chats in `data/kumbuka.db`. Only one process can open the local Qdrant folder at a time.

### With Docker

```bash
cp .env.example .env            # add your key
docker compose up --build       # app on :8000, Qdrant server on :6333
```

## Measuring the numbers

`scripts/benchmark.py` runs a scripted multi-turn session through the real agent and reports Qdrant lookup latency and prompt size versus a "send the whole transcript" baseline. The same figures appear live in the **Insights** tab.

A sample run in demo mode (local Qdrant, 500+ stored memories, 24 turns):

```
vector lookup  p50 6.96 ms   p95 9.17 ms   max 10.33 ms
prompt size    53.6% smaller on average once compression kicks in (19 turns)
```

Lookup time excludes the embedding API call, which is reported separately (`embed_ms`). Compression depends on how long the model's replies and summaries are, so re-run the benchmark with your Gemini key for numbers to quote.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `GEMINI_API_KEY` | Gemini key. Empty means demo mode | none |
| `GEMINI_MODELS` | Chat models, tried in order | `gemini-3.8-flash,gemini-3.5-flash,gemini-2.5-flash` |
| `EMBED_MODEL`, `VECTOR_SIZE` | Embedding model and dimension | `gemini-embedding-001`, `768` |
| `QDRANT_URL`, `QDRANT_API_KEY` | Use a Qdrant server or Qdrant Cloud | unset (local files) |
| `RECALL_THRESHOLD`, `RECALL_LIMIT` | Minimum similarity and max memories per turn | `0.55`, `4` |
| `DUPLICATE_THRESHOLD` | Similarity above which a new fact counts as a duplicate | `0.92` |
| `HISTORY_WINDOW`, `KEEP_RECENT` | Fold history after N messages, keeping the last M verbatim | `8`, `4` |
| `SUMMARY_MAX_CHARS` | Length cap for the running summary | `1200` |
| `KUMBUKA_DB` | SQLite file for conversations | `data/kumbuka.db` |
| `CHAT_PER_HOUR` | Messages per IP per hour | `60` |
| `CORS_ORIGINS` | Browser origins allowed to call the API (comma-separated) | `https://valentinakiyungi.github.io` |
| `LLM_PROVIDER` | `auto`, `gemini` or `demo` | `auto` |

If you see a 404 from Gemini, set `GEMINI_MODELS` or `EMBED_MODEL` to names your key supports.

## API

Interactive docs at `/docs`. All routes take `user_id` (the anonymous browser key).

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/chat/stream` | `{user_id, message, conversation_id?}` → SSE: `start`, `recall`, `context`, `token`…, `saved`, `compressed`, `done` / `error` |
| `POST` | `/api/chat` | Same, as one JSON response |
| `GET` `POST` | `/api/conversations` | List (with search metadata) / create |
| `GET` `PATCH` `DELETE` | `/api/conversations/{id}` | Messages / rename or pin / delete |
| `GET` | `/api/memories?q=&category=` | List, or semantic search when `q` is set |
| `POST` | `/api/memories` | Add a memory manually |
| `PATCH` `DELETE` | `/api/memories/{id}` | Edit (re-embeds) / forget |
| `DELETE` | `/api/memories?confirm=true` | Forget everything |
| `GET` | `/api/memories/export` | Download all memories as JSON |
| `GET` | `/api/stats` | Latency, compression and memory insights |
| `POST` | `/api/suggest` | Suggested follow-up messages |
| `GET` | `/api/config`, `/health` | Runtime info, liveness |

## Project layout

```
src/
  api.py          FastAPI routes + static web app
  agent.py        one chat turn: load session, run graph, persist
  graph.py        LangGraph state machine (recall → respond ∥ remember → compress)
  memory.py       vector tier: save / recall / search / edit / forget
  vectorstore.py  Qdrant backend (+ in-process fallback for tests)
  sessions.py     session tier: conversations, messages, running summary (SQLite)
  llm.py          Gemini provider with retries + model fallback; offline demo provider
  suggest.py      follow-up suggestions
web/              index.html + static/ (app.js, app.css, markdown.js)
scripts/          benchmark.py
tests/            pytest suite (no API key or network needed)
```

## Deploy

The UI is static and runs on **GitHub Pages**; the Python API (LangGraph, Gemini, Qdrant) runs on **Render**. GitHub Pages can't run Python, so the two are split.

**API on Render (+ Qdrant Cloud)**

1. Create a free cluster at cloud.qdrant.io; copy its URL and an API key.
2. On Render, create a Web Service from this repo. Build: `pip install -r requirements.txt`. Start: `uvicorn src.api:app --host 0.0.0.0 --port $PORT`. Health check: `/health`.
3. Set `GEMINI_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`, and `CORS_ORIGINS=https://<your-github-username>.github.io`. Attach a disk and set `KUMBUKA_DB` to a path on it to keep chat history across deploys.

The Render URL also serves the full app on its own, which is handy for testing.

**UI on GitHub Pages**

1. In the repo: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. Push to `main`. The `Deploy UI to GitHub Pages` workflow publishes `web/` and points it at `https://kumbuka-memory-agent.onrender.com`. To use another backend, add a repository variable `KUMBUKA_API_URL` (Settings → Secrets and variables → Actions → Variables).
3. Open `https://<your-github-username>.github.io/Kumbuka-memory-agent/`.

On Render's free plan the API sleeps when idle; the UI shows "Waking up the server" and retries for up to 90 seconds.

## Limitations and privacy

- Messages go to Google Gemini. The free tier may use submitted content to improve Google's products, so avoid sensitive information.
- Visitors are identified by a random key in the browser. Anyone with the key can read those memories; clearing browser data loses access unless you saved the key (Settings → Your memory key).
- Memories come from a model's judgment and can occasionally be wrong or miss facts. Everything is visible and editable in the Memory panel.
- Rate limiting is in-process and per IP; use a shared store if you run several replicas.

## License

MIT
