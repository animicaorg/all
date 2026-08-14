# ai-summarizer — summarize text with animica.ai.infer (AI_INFERENCE capability).
#
# The AI call is metered per token and billed to the execution. AI on Animica is served by
# the miner network; when no healthy provider is serving, animica.ai.infer raises
# animica.AnimicaError — this function then falls back to a real, deterministic extractive
# summary computed in the sandbox, and reports which engine produced the result. Callers
# always get a genuine summary and an honest `engine` field, never a fake one.

import re
from collections import Counter

import animica

_STOP = frozenset(
    """a an the and or but if then else for while of to in on at by with from as is are was
    were be been being it its this that these those he she they we you i not no yes do does
    did have has had will would can could should may might must about into over under between
    also there their our your my his her them us out up down more most some any very just""".split()
)


def _sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if len(p.strip()) >= 20]


def _extractive_summary(text, max_sentences=3):
    """Classic frequency-based extractive summarization (no model involved)."""
    sentences = _sentences(text)
    if len(sentences) <= max_sentences:
        return " ".join(sentences) if sentences else text[:280]
    words = [w for w in re.findall(r"[a-zA-Z][a-zA-Z'-]+", text.lower()) if w not in _STOP]
    freq = Counter(words)
    scored = []
    for idx, s in enumerate(sentences):
        toks = [w for w in re.findall(r"[a-zA-Z][a-zA-Z'-]+", s.lower()) if w not in _STOP]
        if not toks:
            continue
        scored.append((sum(freq[w] for w in toks) / len(toks), idx, s))
    top = sorted(scored, reverse=True)[:max_sentences]
    return " ".join(s for _, _, s in sorted(top, key=lambda t: t[1]))


def main(request, ctx):
    text = (request or {}).get("text", "") if isinstance(request, dict) else ""
    if not isinstance(text, str) or len(text.strip()) < 40:
        raise ValueError("send {\"text\": \"...\"} with at least 40 characters to summarize")
    text = text[:20000]

    try:
        summary = animica.ai.infer(
            "Summarize the following text in 2-3 clear sentences. "
            "Reply with the summary only, no preamble.\n\n" + text,
            max_tokens=220,
        ).strip()
        engine = "animica-ai"
    except animica.AnimicaError as exc:
        # The miner network could not serve inference right now. Degrade honestly to a
        # deterministic extractive summary computed inside the sandbox.
        animica.log("ai.infer unavailable, using extractive fallback:", str(exc)[:160], level="warn")
        summary = _extractive_summary(text)
        engine = "extractive-fallback"

    animica.log(f"summarized {len(text)} chars via {engine}")
    return {
        "summary": summary,
        "engine": engine,
        "chars_in": len(text),
        "request_id": ctx.request_id,
    }
