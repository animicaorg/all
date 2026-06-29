"""
animica.ena.synth
=================

Worker-local LLM synthesis: turn an inline corpus chunk into ``{prompt,
response}`` training pairs by calling **the miner's own model** (the GPU/ollama
behind ``LOCAL_INFERENCE_URL``).

This module is deliberately model-transport-agnostic: it takes an already-built
:class:`~animica.ena.providers.ModelAdapter` and a corpus string and returns
candidate pairs. The LLM call therefore happens wherever the adapter points —
which, on a worker, is the worker's local runtime, never the coordinator. The
coordinator-side curation that decides what may enter the training genome lives
in :mod:`animica.ena.curation` and never calls a model.

The synthesis is best-effort and total: a weak/absent local model yields zero
pairs rather than raising, so the useful-work job still completes and the miner
still earns. Only well-formed, grounded pairs survive the downstream gate and
grow ENA's genome.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Optional

_FENCE = re.compile(r"^```[a-zA-Z0-9]*\s*|\s*```$", re.MULTILINE)

DEFAULT_NUM_PAIRS = 3
DEFAULT_MAX_TOKENS = 768
DEFAULT_TEMPERATURE = 0.4
# Hard cap on how much corpus we feed the local model in one job, so a fat inline
# chunk can't blow up the worker's context window / latency.
MAX_CORPUS_CHARS = 6000


def build_prompt(corpus: str, num_pairs: int) -> str:
    """The instruction handed to the worker's local model."""
    corpus = (corpus or "")[:MAX_CORPUS_CHARS]
    return (
        f"You are building a supervised fine-tuning dataset. Read the SOURCE text "
        f"below and write {num_pairs} self-contained question/answer pairs that a "
        f"student could learn from. Every answer MUST be fully grounded in the "
        f"SOURCE (do not invent facts). Each question must stand on its own "
        f"(no 'according to the text').\n\n"
        f"Reply with ONLY JSON Lines — one object per line, no prose, no code "
        f'fences — each exactly: {{"prompt": "<question>", "response": "<answer>"}}\n\n'
        f"SOURCE:\n{corpus}"
    )


def parse_pairs(text: str) -> list[dict[str, str]]:
    """Tolerantly parse a model reply into ``{prompt, response}`` dicts.

    Accepts JSONL (one object per line), a single JSON array, or fenced variants
    of either. Anything unparseable is skipped — never raises.
    """
    if not text:
        return []
    body = _FENCE.sub("", text).strip()
    out: list[dict[str, str]] = []

    # Whole-body JSON (array or single object) first.
    try:
        obj = json.loads(body)
        items = obj if isinstance(obj, list) else [obj]
        for it in items:
            pair = _coerce_pair(it)
            if pair:
                out.append(pair)
        if out:
            return out
    except ValueError:
        pass

    # Fall back to line-by-line JSONL.
    for line in body.splitlines():
        line = line.strip().rstrip(",")
        if not line or line in ("[", "]"):
            continue
        try:
            it = json.loads(line)
        except ValueError:
            continue
        pair = _coerce_pair(it)
        if pair:
            out.append(pair)
    return out


def _coerce_pair(it: Any) -> dict[str, str] | None:
    if not isinstance(it, dict):
        return None
    prompt = it.get("prompt") or it.get("question") or it.get("instruction")
    response = it.get("response") or it.get("answer") or it.get("output")
    if not prompt or not response:
        return None
    return {"prompt": str(prompt).strip(), "response": str(response).strip()}


def resolve_beacon(beacon: Optional[dict] = None) -> Optional[dict]:
    """Resolve the quantum/pseudo-quantum beacon to synthesize UNDER.

    Priority: an explicit ``beacon`` dict (the coordinator stamps the current
    round onto each ``synthesize_qa`` job) → env ``ANIMICA_ENA_BEACON_SEED_HEX`` /
    ``ANIMICA_ENA_BEACON_ROUND``. Returns ``{seed_hex, round, attested}`` or None.
    A real attested QRNG sets ``attested=True``; the software pseudo-quantum
    fallback sets it False — both drive synthesis identically, only the seal's
    trust label differs.
    """
    b = dict(beacon or {})
    seed_hex = b.get("seed_hex") or b.get("value") or os.environ.get("ANIMICA_ENA_BEACON_SEED_HEX", "")
    if not seed_hex:
        return None
    try:
        bytes.fromhex(seed_hex)
    except ValueError:
        return None
    rnd = b.get("round")
    if rnd is None:
        rnd = os.environ.get("ANIMICA_ENA_BEACON_ROUND", "0")
    try:
        rnd = int(rnd)
    except (TypeError, ValueError):
        rnd = 0
    return {"seed_hex": seed_hex, "round": rnd, "attested": bool(b.get("attested", False))}


def _quantum_temperature(base: float, qseed) -> float:
    """Deterministically jitter the sampling temperature from the quantum seed.

    Drives output DIVERSITY across rounds/miners (anti-collapse) while staying
    reproducible from the round — the entropy actually shapes the synthesis, it
    isn't just a timestamp. Bounded to a sane sampling range.
    """
    frac = (qseed.as_int(16) / 65535.0) - 0.5      # [-0.5, +0.5]
    return max(0.1, min(1.2, float(base) + frac * 0.4))


def synthesize(model, corpus: str, *, num_pairs: int = DEFAULT_NUM_PAIRS,
               max_tokens: int = DEFAULT_MAX_TOKENS,
               temperature: float = DEFAULT_TEMPERATURE,
               beacon: Optional[dict] = None,
               bind_id: Optional[str] = None) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Generate candidate pairs from ``corpus`` using ``model`` (a ModelAdapter),
    optionally SYNTHESIZED UNDER a quantum/pseudo-quantum beacon round.

    When a ``beacon`` (or env) is resolvable, the worker derives a quantum seed
    bound to (this round, this corpus), applies it to local RNG + the model's
    sampler, and jitters the temperature from it — so the generation is driven by
    verifiable quantum entropy: diverse (anti-collapse), reproducible from the
    round, and provably "synthesized under round R". ``meta['provenance']
    ['quantum_seed']`` records the seal so a gene can be verified later without
    re-running the model. Returns ``(pairs, meta)``; any model/parse error
    degrades to an empty pair list (best-effort, never raises).
    """
    num_pairs = max(1, min(int(num_pairs or DEFAULT_NUM_PAIRS), 20))
    prompt = build_prompt(corpus, num_pairs)
    provider = getattr(getattr(model, "cfg", None), "provider", "unknown")
    model_name = getattr(getattr(model, "cfg", None), "model", "unknown")

    # --- synthesize-under-round: quantum/pseudo-quantum seal ---------------
    seal = None
    model_seed = None
    rb = resolve_beacon(beacon)
    if rb is not None:
        try:
            from aicf.integration import quantum_seed as _qs
            corpus_hash = hashlib.sha3_256((corpus or "").encode("utf-8")).hexdigest()
            job_id = "ena/synth/" + (bind_id or corpus_hash[:16])
            qseed = _qs.quantum_seed_for(
                beacon_seed=bytes.fromhex(rb["seed_hex"]),
                beacon_round=int(rb["round"]),
                job_id=job_id,
                attested=bool(rb["attested"]),
            )
            _qs.seed_everything(qseed)                 # seeds python/numpy/torch RNG
            model_seed = qseed.as_int(63)              # sampler seed for the local model
            temperature = _quantum_temperature(temperature, qseed)
            seal = {
                "kind": "synthesized_under_round",
                "seed_hex": qseed.seed_hex,
                "beacon_round": int(rb["round"]),
                "beacon_value_hex": rb["seed_hex"],
                "attested": bool(rb["attested"]),
                "job_id": job_id,
                "corpus_hash": corpus_hash,
                "domain": getattr(qseed, "domain", None),
            }
        except Exception:  # noqa: BLE001 - sealing is best-effort, never blocks synthesis
            seal = None
            model_seed = None

    raw = ""
    error = None
    try:
        if model_seed is not None:
            try:
                raw = model.generate(prompt, max_tokens=max_tokens,
                                     temperature=temperature, seed=model_seed) or ""
            except TypeError:
                # adapter doesn't forward a sampler seed; local RNG + quantum
                # temperature still make it synthesized-under-round
                raw = model.generate(prompt, max_tokens=max_tokens, temperature=temperature) or ""
                if seal is not None:
                    seal["model_seed_forwarded"] = False
            else:
                if seal is not None:
                    seal["model_seed_forwarded"] = True
        else:
            raw = model.generate(prompt, max_tokens=max_tokens, temperature=temperature) or ""
    except Exception as exc:  # noqa: BLE001 - synthesis is best-effort
        error = str(exc)
    pairs = parse_pairs(raw)
    provenance = {
        "kind": "worker_local_synthesis",
        "model_provider": provider,
        "model_name": model_name,
        "corpus_chars": len(corpus or ""),
        "temperature": round(float(temperature), 4),
    }
    if seal is not None:
        provenance["quantum_seed"] = seal
    meta = {
        "provenance": provenance,
        "num_pairs_requested": num_pairs,
        "raw_chars": len(raw),
        "pairs_parsed": len(pairs),
        "synthesized_under_round": (seal or {}).get("beacon_round") if seal else None,
    }
    if error:
        meta["error"] = error
    return pairs, meta
