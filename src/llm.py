"""Model access: Google Gemini in production, an offline demo provider otherwise.

Everything else in the app talks to the four module-level functions below
(`generate`, `stream`, `embed`, `provider_info`), so tests can monkeypatch them.

Messages are plain dicts: {"role": "user" | "assistant", "content": str}.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import time
from functools import lru_cache
from typing import Iterator, Optional, Type

from pydantic import BaseModel

from src import config

log = logging.getLogger("kumbuka.llm")
BUSY = {429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
class GeminiProvider:
    name = "gemini"

    def __init__(self) -> None:
        from google import genai  # imported lazily so demo mode needs no SDK

        if not config.api_key():
            raise RuntimeError("No Gemini API key found. Put GEMINI_API_KEY=... in the .env file.")
        self.client = genai.Client(api_key=config.api_key())
        self.model = config.CHAT_MODELS[0]

    # -- helpers -----------------------------------------------------------
    def _contents(self, prompt):
        from google.genai import types

        if isinstance(prompt, str):
            return prompt
        return [
            types.Content(
                role="user" if m["role"] == "user" else "model",
                parts=[types.Part.from_text(text=m["content"])],
            )
            for m in prompt
        ]

    def _config(self, system, schema, temperature):
        from google.genai import types

        kw = {"temperature": temperature}
        if system:
            kw["system_instruction"] = system
        if schema is not None:
            kw["response_mime_type"] = "application/json"
            kw["response_schema"] = schema
        return types.GenerateContentConfig(**kw)

    def _models(self):
        # Start with the model that worked last time.
        return [self.model] + [m for m in config.CHAT_MODELS if m != self.model]

    # -- API ---------------------------------------------------------------
    def generate(self, prompt, system=None, schema=None, temperature=0.7, retries=3, task="") -> str:
        from google.genai import errors

        last = "no response"
        for model in self._models():
            for attempt in range(retries):
                try:
                    res = self.client.models.generate_content(
                        model=model,
                        contents=self._contents(prompt),
                        config=self._config(system, schema, temperature),
                    )
                    self.model = model
                    return res.text or ""
                except errors.APIError as e:
                    last = f"{model}: {e.code} {e.message}"
                    if e.code in BUSY:
                        time.sleep(min(2**attempt, 4))
                        continue
                    break  # 404 / auth: try the next model
        raise RuntimeError(f"All Gemini models failed. Last error: {last}")

    def stream(self, prompt, system=None, temperature=0.7, task="") -> Iterator[str]:
        from google.genai import errors

        last = "no response"
        for model in self._models():
            emitted = False
            try:
                for chunk in self.client.models.generate_content_stream(
                    model=model,
                    contents=self._contents(prompt),
                    config=self._config(system, None, temperature),
                ):
                    if chunk.text:
                        emitted = True
                        yield chunk.text
                self.model = model
                return
            except errors.APIError as e:
                last = f"{model}: {e.code} {e.message}"
                if emitted:  # cannot restart a half-sent answer on another model
                    raise RuntimeError(f"Gemini stream interrupted: {last}")
                if e.code in BUSY:
                    time.sleep(1)
                continue
        raise RuntimeError(f"All Gemini models failed. Last error: {last}")

    def embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        from google.genai import errors, types

        last = "no response"
        for attempt in range(3):
            try:
                res = self.client.models.embed_content(
                    model=config.EMBED_MODEL,
                    contents=texts,
                    config=types.EmbedContentConfig(
                        task_type=task_type, output_dimensionality=config.VECTOR_SIZE
                    ),
                )
                return [_normalize(list(e.values)) for e in res.embeddings]
            except errors.APIError as e:
                last = f"{e.code} {e.message}"
                if e.code in BUSY:
                    time.sleep(2**attempt)
                    continue
                break
        raise RuntimeError(f"Embedding failed ({config.EMBED_MODEL}): {last}")


# ---------------------------------------------------------------------------
# Offline demo provider: deterministic, no network, good enough to explore the UI
# ---------------------------------------------------------------------------
STOP = set(
    "a an the and or but if of to in on at for with by from as is are was were be been am i me my "
    "mine we our you your it its this that these those do does did have has had so very really just "
    "what how why when where which who can could would should will shall may might about into than "
    "then them they their there here also any some all not no yes please thanks thank hi hello".split()
)
_CATEGORY_HINTS = [
    ("constraint", r"\b(must|can't|cannot|can not|deadline|budget|only allowed|not allowed|limited|restricted|need to support)\b"),
    ("preference", r"\b(prefer|like|love|hate|dislike|enjoy|favou?rite|always|never|rather)\b"),
    ("goal", r"\b(want to|goal|plan to|planning|learning|aim|hope to|trying to)\b"),
    ("project", r"\b(building|working on|project|app|startup|product|repo|side project|shipping)\b"),
    ("stack", r"\b(use|using|stack|written in|framework|deploy|python|typescript|javascript|rust|go|java|react|vue|fastapi|django|postgres|docker|kubernetes|aws|gcp|qdrant|langgraph)\b"),
]


def _tokens(text: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9+#.]+", text.lower()) if w not in STOP and len(w) > 1]
    return [w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words]


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _categorize(sentence: str) -> str:
    s = sentence.lower()
    for cat, pat in _CATEGORY_HINTS:
        if re.search(pat, s):
            return cat
    return "personal"


class DemoProvider:
    name = "demo"
    model = "offline-demo"

    def embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * config.VECTOR_SIZE
            toks = _tokens(t)
            feats = toks + [a + "_" + b for a, b in zip(toks, toks[1:])]
            for f in feats:
                h = int(hashlib.md5(f.encode()).hexdigest(), 16)
                v[h % config.VECTOR_SIZE] += 1.0 if "_" not in f else 0.5
            out.append(_normalize(v))
        return out

    # -- tasks -------------------------------------------------------------
    def extract(self, text: str) -> str:
        import json

        facts = []
        for raw in re.split(r"(?<=[.!?])\s+|\n+", text):
            s = raw.strip().strip("-• ")
            low = s.lower()
            if len(s) < 8 or s.endswith("?"):
                continue
            if re.match(r"^(can|could|would|will|please|how|what|why|help|tell|show|write|explain|give)\b", low):
                continue
            if not re.search(r"\b(i|i'm|i've|i'd|my|we|we're|our)\b", low):
                continue
            facts.append({"content": s.rstrip("."), "category": _categorize(s)})
        return json.dumps({"memories": facts[:5]})

    def summarize(self, prompt: str) -> str:
        prev = re.search(r"PREVIOUS SUMMARY:\n(.*?)\n\nNEW MESSAGES:", prompt, re.S)
        parts = [prev.group(1).strip()] if prev and prev.group(1).strip() not in ("", "(none)") else []
        for line in prompt.split("\n"):
            m = re.match(r"^(user|assistant): (.*)", line)
            if m:
                first = re.split(r"(?<=[.!?])\s", m.group(2).strip())[0][:140]
                parts.append(("User said: " if m.group(1) == "user" else "Assistant: ") + first)
        return " ".join(parts)[-config.SUMMARY_MAX_CHARS:]

    def reply(self, prompt, system: str) -> str:
        last = prompt[-1]["content"] if isinstance(prompt, list) else str(prompt)
        recalled = re.findall(r"^- (.+)$", system.split("WHAT YOU REMEMBER")[-1].split("CONVERSATION SUMMARY")[0], re.M)
        lines = []
        if recalled:
            lines.append("Here's what I'm keeping in mind about you:")
            lines += [f"- {r.split(' [')[0]}" for r in recalled[:4]]
            lines.append("")
        if last.strip().endswith("?"):
            lines.append(
                "I'm running in **offline demo mode**, so I can't produce a real answer to that yet. "
                "Add a `GEMINI_API_KEY` to the `.env` file and restart to get full replies from Gemini."
            )
        else:
            lines.append(
                "Got it. Anything you tell me about your tools, projects, or preferences gets saved to "
                "long-term memory, and you can review or edit it in the **Memory** panel."
            )
        lines.append("\n_Demo mode: replies are templated, but memory recall, storage and compression are real._")
        return "\n".join(lines)

    def generate(self, prompt, system=None, schema=None, temperature=0.7, retries=1, task="") -> str:
        import json

        text = prompt if isinstance(prompt, str) else prompt[-1]["content"]
        if task == "extract":
            m = re.search(r"USER MESSAGE:\n(.*)$", text, re.S)
            return self.extract(m.group(1) if m else text)
        if task == "summarize":
            return self.summarize(text)
        if task == "suggest":
            return json.dumps({"suggestions": [
                "What do you remember about me?",
                "I prefer concise answers with code examples",
                "Help me plan the next step of my project",
            ]})
        if task == "title":
            m = re.search(r"MESSAGE:\n(.*)$", text, re.S)
            return " ".join((m.group(1) if m else text).split()[:6])
        return self.reply(prompt, system or "")

    def stream(self, prompt, system=None, temperature=0.7, task="") -> Iterator[str]:
        text = self.reply(prompt, system or "")
        for piece in re.findall(r"\S+\s*", text):
            time.sleep(0.012)
            yield piece


# ---------------------------------------------------------------------------
# Module API
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def provider():
    if config.demo_mode():
        log.warning("No GEMINI_API_KEY set: running in offline demo mode.")
        return DemoProvider()
    return GeminiProvider()


def generate(prompt, system: Optional[str] = None, schema: Optional[Type[BaseModel]] = None,
             temperature: float = 0.7, retries: int = 3, task: str = "") -> str:
    return provider().generate(prompt, system=system, schema=schema, temperature=temperature,
                               retries=retries, task=task)


def stream(prompt, system: Optional[str] = None, temperature: float = 0.7, task: str = "") -> Iterator[str]:
    yield from provider().stream(prompt, system=system, temperature=temperature, task=task)


def embed(texts: list[str], task_type: str) -> list[list[float]]:
    return provider().embed(texts, task_type)


def provider_info() -> dict:
    p = provider()
    return {"provider": p.name, "model": getattr(p, "model", ""), "embed_model":
            "hashing-bow" if p.name == "demo" else config.EMBED_MODEL, "demo": p.name == "demo"}


def approx_tokens(chars: int) -> int:
    return max(0, round(chars / 4))
