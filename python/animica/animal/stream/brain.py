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
        # Default to the small chat tier the free network actually serves (flagship often
        # has no worker → the AICF job never gets claimed and the reply comes back empty).
        self._llm_model = os.environ.get("ANIMICA_STREAM_LLM_MODEL", "animica-chat")
        if not self._llm_url:
            # default to the free keyless gateway; short timeout + fallback keep it snappy
            self._llm_url = os.environ.get("ANIMICA_FREE_V1", "https://animica.dev/v1").rstrip("/")
        self.llm_timeout = float(os.environ.get("ANIMICA_STREAM_LLM_TIMEOUT", "12"))
        # Circuit breaker: if the free-AI network isn't answering, stop waiting the full
        # timeout on every single line — trip after a few misses and lean on the in-character
        # engine for a cooldown, then probe again (so real replies resume when a worker returns).
        self._llm_fails = 0
        self._llm_cooldown_until = 0.0
        self._last_said = ""       # never speak the exact same line twice in a row (anti-loop)

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
        text = ""
        # Prefer the real model on Animica's free network, but don't hang on it every line
        # when it's down — the circuit breaker skips it during a cooldown after repeated misses.
        if time.monotonic() >= self._llm_cooldown_until:
            text = self._llm(user, ctx)
            if text:
                self._llm_fails = 0
            else:
                self._llm_fails += 1
                if self._llm_fails >= 3:
                    self._llm_cooldown_until = time.monotonic() + 60.0
                    self.log("[brain] free-AI network not answering — using the in-character "
                             "engine for 60s, then probing again")
        if not text:
            text = self._fallback(user, ctx)
        # Anti-loop: never emit the exact same line twice running. Idle fallback is random, so
        # re-rolling breaks a repeat; if it still collides (deterministic chat reply), nudge it.
        for _ in range(5):
            if text and text != self._last_said:
                break
            text = self._fallback(user, ctx)
        if text == self._last_said:
            text = f"{text} {self.rng.choice(('~', 'mrrp~', 'nya~', ':3'))}"
        self._last_said = text
        return text

    def _system_prompt(self, ctx: SceneContext) -> str:
        c = self.char
        facts = (
            "Animica is a post-quantum layer-1 blockchain (ML-DSA-65 signatures, bech32m 'anim1' "
            "addresses, ticker ANM, ~1e9 nANM per ANM). Miners do useful work and it runs on-chain "
            "AI inference, a free keyless OpenAI-compatible API, generative image/video/music rendered "
            "by GPU miners, a sovereign '.anm' internet with its own browser, a decentralized VPN, and "
            "an AI creator marketplace."
        )
        return (
            f"You are {c.name}, {c.personality}. You host a live 24/7 stream about "
            f"{', '.join(c.topics[:5])}. Speak in the first person, {c.speaking_style}. "
            f"Answer the viewer's ACTUAL question with something specific and genuinely true — be warm "
            f"and playful but really helpful, never vague filler. What you know: {facts} "
            f"If you truly don't know, say so cutely and invite them to explore. Never break character; "
            f"never say you are an AI, a model, or a language model. Reply in one or two short sentences, "
            f"under 200 characters. Use a catchphrase only when it feels natural: "
            f"{', '.join(c.catchphrases[:4])}."
        )

    def _llm(self, user: Optional[ChatMessage], ctx: SceneContext) -> str:
        stats = ctx.net_stats or {}
        ground = f"Network right now: block {stats.get('height','?')}, ANM ${stats.get('price','?')}, {stats.get('peers','?')} peers."
        recent = ""
        try:
            hist = [m for m in (ctx.chat or []) if m.get("text")][-4:]
            if len(hist) > 1:
                recent = " Recent chat: " + " | ".join(
                    f"{m.get('author','?')}: {m.get('text','')[:60]}" for m in hist[:-1]) + "."
        except Exception:
            recent = ""
        if user:
            usr = (f"A viewer named {user.author} says in chat: \"{user.text}\".{recent} "
                   f"Reply to {user.author} by name — answer what they actually asked, warmly and briefly. {ground}")
        else:
            usr = f"Say one short, upbeat, specific thing to the stream (not generic filler). {ground}"
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
                           "max_tokens": 120, "temperature": 0.85}).encode()
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
            has = lambda *ws: any(w in t for w in ws)  # noqa: E731
            # Real, specific Animica answers in-character — ordered most-specific first.
            if has("gm", "hello", "hi ", "hey", "yo ", "sup"):
                return f"{cp} gm {name}! so cozy you dropped by the stream~"
            if has("quantum", "post-quantum", "pq", "ml-dsa", "secure"):
                return f"{name}, Animica is post-quantum — we sign with ML-DSA-65, so a quantum computer can't forge our keys! {cp}"
            if has("what is animica", "about animica", "explain", "what's animica", "whats animica"):
                return f"{name}, Animica's a post-quantum chain where mining does USEFUL work — on-chain AI, media, a whole .anm internet! {cp}"
            if has("wallet", "address", "anim1", "send", "receive"):
                return f"{name}, wallets are bech32m and start with 'anim1' — grab one at wallet.animica.org, fully in your browser~"
            if has("buy", "exchange", "nonkyc", "trade", "listed"):
                return f"you can trade ANM on nonkyc.io, {name}! but psst, i'm just here for the tech and the zoomies~ {cp}"
            if has("free ai", "chatgpt", "llm", "inference", "api"):
                return f"{name}, Animica has a FREE keyless AI API — miners serve the models. it's real AI, no signup! {cp}"
            if has("media", "image", "video", "music", "generate", "art"):
                return f"{name}, our GPU miners render images, video AND music on-chain — try the Media Studio on animica.dev! {cp}"
            if has(".anm", "internet", "browser", "website", "domain"):
                return f"the .anm internet is a whole sovereign web with its own browser, {name} — names, sites, all on-chain! {cp}"
            if has("vpn", "privacy", "private"):
                return f"{name}, Animica even has a decentralized VPN — relays run by the network, paid in ANM~ {cp}"
            if has("height", "block", "tip"):
                return f"{name}, we're at block {stats.get('height','—')} and climbing! {cp}"
            if has("hashrate", "mining", "miner", "mine", "gpu"):
                return f"{name}, miners power everything here — proof-of-USEFUL-work, not wasted cycles! come mine with us~ {cp}"
            if has("price", "anm", "worth", "market"):
                return f"ANM's ${stats.get('price','—')} right now, {name} — but honestly i'm here for the tech, {cp}!"
            if has("who are you", "your name", "what are you", "cat"):
                return f"i'm {c.name}, your resident stream kitty and Animica's mascot! {cp}"
            if "?" in t:
                return f"ooh good question {name}! ask me about Animica's AI, mining, media or the .anm internet~ {cp}"
            return f"love that, {name}! {cp} — stick around, we talk AI, mining and the .anm internet all day~"
        # idle — a wide, rotating pool so the stream never feels repetitive
        pool = [
            f"{cp} block {stats.get('height','—')} and the network's purring today!",
            f"who's mining ANM right now? drop a paw in chat~ {cp}",
            f"fun fact: Animica is post-quantum — our ML-DSA keys laugh at quantum computers! {cp}",
            f"{stats.get('peers','lots of')} peers keeping us decentralized — i love this community!",
            f"remember to stretch, hydrate, and hug a miner today~ {cp}",
            f"generative media, on-chain AI, a whole sovereign internet... we live in the future, friends!",
            f"did you know Animica has a FREE keyless AI API? real models, no signup~ {cp}",
            f"the .anm internet has its own browser and names — a whole web that's truly yours!",
            f"our miners do USEFUL work — every block trains AI or renders media, not wasted hashes~",
            f"psst… you can spin up images, video AND music on-chain over at animica.dev {cp}",
            f"grab a wallet at wallet.animica.org — 'anim1…', all in your browser, keys stay with you!",
            f"there's even a decentralized VPN running on the network — privacy, paid in ANM~ {cp}",
            f"ask me anything about Animica, friends — AI, mining, the .anm web, i'm all ears (big ones)!",
            f"stretch break with me? paws up, tails high~ then back to building the future! {cp}",
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
    """True for worker stubs AND the bridge's no-miner/system placeholder — Momo must never
    speak these (they're identical every time, so they loop on-screen). Falls back instead."""
    low = txt.lower()
    return any(k in low for k in (
        "[aicf-miner-stub", "model_load_failed", "distributed-aicf stub",
        "unrecognized model", "temporarily unavailable",
        # bridge no-miner placeholder / any leaked system message
        "couldn't complete your request", "couldn’t complete your request",
        "wasn't able to load", "wasn’t able to load", "try again in a moment",
        "providers come online", "come online or finish", "pip install", "animica up",
        "provider that picked it up", "language model", "⚠️"))


def make_line_provider(brain: Brain):
    return brain.step
