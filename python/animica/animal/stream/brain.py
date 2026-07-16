"""The interactive brain.

Turns live YouTube chat + network state + the character's personality (and an optional
per-character knowledge base / RAG) into things the mascot says. Prefers an OpenAI-
compatible LLM (a local one, else the free animica.dev /v1) but always has a strong
in-character rule-based fallback so the stream stays lively and never stalls waiting on
a model. When it answers a chat message it also posts a short reply back into the live
chat via `chat_sink`.
"""
from __future__ import annotations

import json
import os
import random
import time
import urllib.request
from typing import Callable, Optional

from .contract import Character, ChatMessage, SceneContext, SpeechItem, StreamConfig

_POSITIVE = ("happy", "excited", "love")
_EMOTES = {
    "happy": ["purr-fect", "yay", "so cozy"],
    "excited": ["let's gooo", "wowowow", "zoomies incoming"],
    "curious": ["hmm", "ooh what's that", "tell me more"],
    "love": ["i love this community", "you're paw-some"],
}


class Brain:
    def __init__(self, cfg: StreamConfig, char: Character, *,
                 chat_source: Callable[[], "list[ChatMessage]"] = None,
                 chat_sink: Callable[[str], None] = None,
                 rag: Callable[[str], str] = None,
                 log=print):
        self.cfg = cfg
        self.char = char
        self.chat_source = chat_source
        self.chat_sink = chat_sink
        self.rag = rag
        self.log = log
        self._pending: "list[ChatMessage]" = []
        self._seen: set = set()
        self._last_idle = 0.0
        self.idle_cooldown = float(os.environ.get("ANIMICA_STREAM_IDLE_SECS", "16"))
        self.rng = random.Random(cfg.seed ^ 0x5EED)
        self._llm_url = os.environ.get("ANIMICA_STREAM_LLM_URL", "").rstrip("/")
        self._llm_key = os.environ.get("ANIMICA_STREAM_LLM_KEY", "")
        self._llm_model = os.environ.get("ANIMICA_STREAM_LLM_MODEL", "animica-flagship")
        if not self._llm_url:
            # default to the free keyless gateway; short timeout + fallback keep it snappy
            self._llm_url = os.environ.get("ANIMICA_FREE_V1", "https://animica.dev/v1").rstrip("/")
        self.llm_timeout = float(os.environ.get("ANIMICA_STREAM_LLM_TIMEOUT", "8"))

    # ── the line provider the pipeline calls ─────────────────────────────────
    def step(self, ctx: SceneContext) -> Optional[SpeechItem]:
        now = time.monotonic()
        if self.chat_source:
            try:
                for m in self.chat_source():
                    if m.id not in self._seen:
                        self._seen.add(m.id)
                        self._pending.append(m)
            except Exception as e:
                self.log(f"[brain] chat source error: {e}")

        # 1) answer a chat message (highest priority)
        if self._pending:
            m = self._pending.pop(0)
            ctx.chat.append({"author": m.author[:18], "text": m.text[:160]})
            reply = self._say(user=m, ctx=ctx)
            ctx.chat.append({"author": self.char.name, "text": reply, "reply": True})
            if self.chat_sink:
                try:
                    self.chat_sink(self._short(reply))
                except Exception as e:
                    self.log(f"[brain] chat post failed: {e}")
            return SpeechItem(reply, emotion=self._emotion(reply, "excited"),
                              behavior="react", priority=10, source="chat")

        # 2) idle chatter on a cooldown
        if now - self._last_idle >= self.idle_cooldown:
            self._last_idle = now
            line = self._say(user=None, ctx=ctx)
            ctx.chat.append({"author": self.char.name, "text": line, "reply": True})
            return SpeechItem(line, emotion=self._emotion(line, self.char.default_emotion),
                              behavior="sing" if self.rng.random() < 0.4 else "react",
                              priority=1, source="idle")
        return None

    # ── generation ───────────────────────────────────────────────────────────
    def _say(self, user: Optional[ChatMessage], ctx: SceneContext) -> str:
        text = self._llm(user, ctx)
        if text:
            return text
        return self._fallback(user, ctx)

    def _system_prompt(self, ctx: SceneContext) -> str:
        c = self.char
        return (
            f"You are {c.name}, {c.personality}. You are the live host of a 24/7 stream "
            f"about {', '.join(c.topics[:5])}. Speak in the first person, {c.speaking_style}. "
            f"Never break character, never mention being an AI or a model. Keep replies under 200 "
            f"characters. Occasionally use one of these catchphrases naturally: "
            f"{', '.join(c.catchphrases[:4])}."
        )

    def _llm(self, user: Optional[ChatMessage], ctx: SceneContext) -> str:
        stats = ctx.net_stats or {}
        ground = f"Network right now: block {stats.get('height','?')}, ANM ${stats.get('price','?')}, {stats.get('peers','?')} peers."
        if user:
            usr = f"A viewer named {user.author} says in chat: \"{user.text}\". Reply to them by name, warmly and briefly."
        else:
            usr = f"Say one short, upbeat thing to the stream. {ground}"
        rag_ctx = ""
        if self.rag:
            try:
                q = user.text if user else " ".join(self.char.topics[:3])
                rag_ctx = (self.rag(q) or "")[:800]
            except Exception:
                rag_ctx = ""
        messages = [{"role": "system", "content": self._system_prompt(ctx)}]
        if rag_ctx:
            messages.append({"role": "system", "content": f"Relevant knowledge you know:\n{rag_ctx}"})
        messages.append({"role": "user", "content": usr})
        body = json.dumps({"model": self._llm_model, "messages": messages,
                           "max_tokens": 80, "temperature": 0.9}).encode()
        req = urllib.request.Request(self._llm_url + "/chat/completions", data=body,
                                     headers={"content-type": "application/json"})
        if self._llm_key:
            req.add_header("authorization", f"Bearer {self._llm_key}")
        try:
            with urllib.request.urlopen(req, timeout=self.llm_timeout) as r:
                d = json.loads(r.read())
            txt = (d.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
            txt = txt.strip('"').replace("\n", " ")
            return txt[:220] if txt and not _is_stub(txt) else ""
        except Exception:
            return ""

    # ── rule-based fallback (always in-character, grounded, varied) ───────────
    def _fallback(self, user: Optional[ChatMessage], ctx: SceneContext) -> str:
        c = self.char
        cp = self.rng.choice(c.catchphrases) if c.catchphrases else "mrrp"
        stats = ctx.net_stats or {}
        if user:
            t = user.text.lower()
            name = user.author
            if any(w in t for w in ("gm", "hello", "hi", "hey")):
                return f"{cp} gm {name}! so happy you're here~"
            if "height" in t or "block" in t:
                return f"{name}, we're at block {stats.get('height','—')} and climbing! {cp}"
            if "hashrate" in t or "mining" in t or "miner" in t:
                return f"{name}, miners are carrying us — {stats.get('hashrate','lots of')} hash! keep it up~"
            if "price" in t or "anm" in t:
                return f"ANM is ${stats.get('price','—')} right now, {name} — but i'm here for the tech, {cp}!"
            if "?" in t:
                return f"ooh good question {name}! {cp}, let me think about that one~"
            return f"{name} says '{user.text[:40]}' — {cp}! love the energy~"
        # idle
        pool = [
            f"{cp} block {stats.get('height','—')} and the network's purring today!",
            f"who's mining ANM right now? drop a paw in chat~ {cp}",
            f"the .anm internet is growing every day, it's paw-some!",
            f"{stats.get('peers','lots of')} peers keeping us decentralized — i love this community!",
            f"remember to stretch, hydrate, and hug a miner today~ {cp}",
            f"generative media, on-chain AI, a whole sovereign internet... we live in the future, friends!",
        ]
        return self.rng.choice(pool)

    def _emotion(self, text: str, default: str) -> str:
        t = text.lower()
        if any(w in t for w in ("love", "cozy", "community", "hug")):
            return "love"
        if any(w in t for w in ("let's go", "gooo", "wow", "amazing", "!!")):
            return "excited"
        if "?" in t:
            return "curious"
        return default

    @staticmethod
    def _short(s: str) -> str:
        return s if len(s) <= 180 else s[:177] + "…"


def _is_stub(txt: str) -> bool:
    low = txt.lower()
    return any(k in low for k in ("[aicf-miner-stub", "model_load_failed", "distributed-aicf stub",
                                  "unrecognized model", "temporarily unavailable"))


def make_line_provider(brain: Brain):
    return brain.step
