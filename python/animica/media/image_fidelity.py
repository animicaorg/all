"""Prompt-fidelity scoring for image candidates (miner side).

A diffusion model is a lottery: the same prompt gives a faithful image on one seed and a
wrong one on the next. The cheapest large accuracy win is therefore to render several
candidates and KEEP THE ONE THAT ACTUALLY MATCHES THE PROMPT. This module scores a
candidate against the compiled prompt with CLIP (text↔image cosine similarity):

    score = sim(full prompt)                       the whole request
          + w_c · mean(sim(constraint clause_i))   each specific detail on its own
          - w_n · max(sim(negated concept_j))      penalty for drawing what was excluded

The constraint views are what make this work for *specific* prompts: "a crib trailer with
nine logs, the top center log highlighted red" scores the red-log clause separately, so a
candidate that nails the trailer but not the red log loses to one that has both. Negated
concepts matter because turbo/schnell models ignore negative prompts (guidance 0) — the
reranker is the only place a "no cars" constraint can be enforced for them.

Fail-open by design: scoring never fails a job. If CLIP is unavailable the caller keeps
its first candidate and records why in the result meta.

Default scorer: ``openai/clip-vit-base-patch32`` (~600 MB, sub-second on CPU for a handful
of images). Override with ``ANIMICA_IMAGE_SCORER`` (e.g. ``openai/clip-vit-large-patch14``
for a stronger judge on a GPU box). ``ANIMICA_IMAGE_RERANK=0`` disables reranking.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, Sequence

DEFAULT_SCORER = "openai/clip-vit-base-patch32"
CONSTRAINT_WEIGHT = 0.5
NEGATION_WEIGHT = 0.6
REFERENCE_WEIGHT = 0.35   # image↔reference-photo similarity (what the real thing looks like)

_SCORER_CACHE: dict[str, tuple] = {}


class FidelityUnavailable(RuntimeError):
    """CLIP could not be loaded/run — the caller must fall back, never fail the job."""


def rerank_enabled() -> bool:
    return os.environ.get("ANIMICA_IMAGE_RERANK", "1").strip().lower() not in ("0", "false", "no", "off")


def scorer_model_id() -> str:
    return os.environ.get("ANIMICA_IMAGE_SCORER", "").strip() or DEFAULT_SCORER


@dataclass
class FidelityReport:
    scores: list[float]                 # per candidate, higher = better
    best: int                           # index of the winner
    scorer: str
    views: list[str] = field(default_factory=list)
    negated: list[str] = field(default_factory=list)
    detail: list[dict] = field(default_factory=list)  # per candidate: {full, constraints, negation, reference}
    references: int = 0

    def to_meta(self) -> dict:
        return {
            "scorer": self.scorer,
            "scores": [round(s, 4) for s in self.scores],
            "best": self.best,
            "fidelity": round(self.scores[self.best], 4) if self.scores else None,
            "views": self.views,
            "negated": self.negated,
            "refs_used": self.references,
        }


def _device_for(prefer_cuda: bool) -> str:
    try:
        import torch
        if prefer_cuda and torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def load_scorer(model_id: Optional[str] = None, device: Optional[str] = None):
    """(model, processor, device). Cached per model id; raises FidelityUnavailable."""
    mid = model_id or scorer_model_id()
    dev = device or _device_for(os.environ.get("ANIMICA_IMAGE_SCORER_CPU", "0") != "1")
    key = f"{mid}::{dev}"
    if key in _SCORER_CACHE:
        return _SCORER_CACHE[key]
    try:
        import torch
        from transformers import CLIPModel, CLIPProcessor
    except Exception as e:  # pragma: no cover - env dependent
        raise FidelityUnavailable(f"transformers CLIP not installed: {e}") from e
    try:
        model = CLIPModel.from_pretrained(mid, low_cpu_mem_usage=True)
        proc = CLIPProcessor.from_pretrained(mid)
        model.eval()
        try:
            model = model.to(dev)
        except Exception:
            dev = "cpu"
            model = model.to(dev)
    except Exception as e:
        raise FidelityUnavailable(f"failed to load CLIP scorer {mid!r}: {e}") from e
    _SCORER_CACHE[key] = (model, proc, dev)
    return _SCORER_CACHE[key]


def _text_feats(model, proc, dev, texts: Sequence[str]):
    import torch
    inp = proc(text=list(texts), return_tensors="pt", padding=True, truncation=True, max_length=77)
    inp = {k: v.to(dev) for k, v in inp.items()}
    with torch.no_grad():
        f = model.get_text_features(**inp)
    return f / f.norm(dim=-1, keepdim=True)


def _image_feats(model, proc, dev, images):
    import torch
    inp = proc(images=list(images), return_tensors="pt")
    inp = {k: v.to(dev) for k, v in inp.items()}
    with torch.no_grad():
        f = model.get_image_features(**inp)
    return f / f.norm(dim=-1, keepdim=True)


def fetch_reference_images(urls: Sequence[str], *, max_n: int = 4, timeout: float = 6.0, max_bytes: int = 6_000_000) -> list:
    """Download reference photos (from the gateway's web lookup) as PIL images. Best-effort:
    bad/slow/huge URLs are skipped, never raised."""
    import io
    import urllib.request
    out = []
    for u in list(urls)[:max_n * 2]:
        if len(out) >= max_n:
            break
        if not isinstance(u, str) or not u.lower().startswith(("http://", "https://")):
            continue
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "animica-media-miner/11.1 (+https://animica.dev)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
                data = r.read(max_bytes + 1)
            if len(data) > max_bytes:
                continue
            from PIL import Image
            im = Image.open(io.BytesIO(data)).convert("RGB")
            if im.width < 64 or im.height < 64:
                continue
            im.thumbnail((512, 512))
            out.append(im)
        except Exception:
            continue
    return out


def score_candidates(
    images: Sequence,
    prompt: str,
    *,
    views: Sequence[str] = (),
    negated: Sequence[str] = (),
    references: Sequence = (),
    model_id: Optional[str] = None,
    constraint_weight: float = CONSTRAINT_WEIGHT,
    negation_weight: float = NEGATION_WEIGHT,
    reference_weight: float = REFERENCE_WEIGHT,
) -> FidelityReport:
    """Score PIL images against the prompt (and optional reference photos of the subject).
    Raises FidelityUnavailable (caller falls back)."""
    if not images:
        raise ValueError("no candidates to score")
    if not prompt or not prompt.strip():
        raise ValueError("empty prompt")
    model, proc, dev = load_scorer(model_id)
    try:
        views = [v for v in views if v and v.strip() and v.strip().lower() != prompt.strip().lower()]
        negated = [n for n in negated if n and n.strip()]
        texts = [prompt] + list(views) + list(negated)
        tf = _text_feats(model, proc, dev, texts)
        imf = _image_feats(model, proc, dev, images)
        sims = (imf @ tf.T).float().cpu()  # (n_images, n_texts)
        refsims = None
        refs = [r for r in references if r is not None]
        if refs:
            try:
                rf = _image_feats(model, proc, dev, refs)
                refsims = (imf @ rf.T).float().cpu()  # (n_images, n_refs)
            except Exception:
                refsims = None
        n_v = len(views)
        scores: list[float] = []
        detail: list[dict] = []
        for i in range(sims.shape[0]):
            full = float(sims[i, 0])
            cons = float(sims[i, 1:1 + n_v].mean()) if n_v else 0.0
            negs = float(sims[i, 1 + n_v:].max()) if negated else 0.0
            ref = float(refsims[i].mean()) if refsims is not None else 0.0
            s = full + (constraint_weight * cons if n_v else 0.0) - (negation_weight * negs if negated else 0.0)
            s += reference_weight * ref if refsims is not None else 0.0
            scores.append(s)
            detail.append({"full": round(full, 4), "constraints": round(cons, 4), "negation": round(negs, 4), "reference": round(ref, 4)})
        best = max(range(len(scores)), key=lambda i: scores[i])
        rep = FidelityReport(scores=scores, best=best, scorer=model_id or scorer_model_id(),
                             views=list(views), negated=list(negated), detail=detail)
        rep.references = len(refs) if refsims is not None else 0
        return rep
    except FidelityUnavailable:
        raise
    except Exception as e:
        raise FidelityUnavailable(f"CLIP scoring failed: {e}") from e


def unload_scorer() -> None:
    """Drop the cached CLIP model (VRAM reclaim path)."""
    _SCORER_CACHE.clear()
