"""Deterministic image-prompt compiler (miner side; pure Python, no model).

Port of the gateway's ``lib/imagePrompt.ts`` — both pass the shared conformance vectors in
``prompt_vectors.json`` so a prompt compiles identically whether it arrived through
animica.dev or a direct ``animica media`` CLI call. The gateway normally compiles before
enqueueing; the miner re-runs it because the pass is idempotent and direct submissions
(CLI, self-hosted gateways, older gateways) never saw it.

What it does, and why it matters for accuracy:

* Diffusion text encoders are literal — ``"Make an image of X"`` spends scarce CLIP tokens
  on the instruction, so it is stripped.
* They have no negation: ``"a street with no cars"`` DRAWS cars. Negated concepts are moved
  into the negative prompt (and the common ones mapped to their positive equivalent:
  ``"without colour"`` → ``"black and white, monochrome"``).
* It extracts a machine-readable spec (quoted text, counts, NxM grids, colors, layout words,
  negated concepts) that ``image_fidelity`` scores candidates against.
* It estimates the CLIP token budget so ``image_gen`` knows when to switch to chunked
  long-prompt embeddings instead of letting the tokenizer silently truncate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

CLIP_TOKEN_BUDGET = 75

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "twenty": 20, "dozen": 12, "single": 1, "pair": 2, "couple": 2,
}

COLORS = [
    "red", "orange", "yellow", "green", "blue", "purple", "violet", "pink", "magenta", "cyan", "teal",
    "turquoise", "brown", "beige", "tan", "black", "white", "gray", "grey", "gold", "golden", "silver",
    "bronze", "copper", "navy", "maroon", "crimson", "scarlet", "indigo", "lavender", "lime", "olive",
    "amber", "ivory", "cream", "charcoal", "emerald", "ruby", "sapphire", "phthalo",
]

LAYOUT_WORDS = [
    "top", "bottom", "left", "right", "center", "centre", "middle", "corner", "foreground",
    "background", "above", "below", "beside", "behind", "front", "upper", "lower", "centered", "centred",
]

STYLE_WORDS = [
    "photo", "photograph", "photorealistic", "realistic", "painting", "oil", "watercolor", "watercolour",
    "sketch", "drawing", "illustration", "vector", "flat", "logo", "icon", "pixel", "anime", "manga",
    "cartoon", "3d", "render", "cinematic", "isometric", "minimal", "minimalist", "line art", "lineart",
    "ink", "charcoal", "pastel", "comic", "poster", "diagram", "blueprint", "sticker", "emoji", "meme",
    "studio", "macro", "wide angle", "portrait", "landscape", "low poly", "voxel", "pencil", "gouache",
    "concept art", "digital art", "engraving", "woodcut", "clipart", "clip art",
]

_NO_STOPLIST = {"one", "matter", "doubt", "longer", "more", "way", "idea", "problem", "less", "other", "sooner"}

_NEGATION_MAP: dict[str, dict] = {
    "colour": {"pos": "black and white, monochrome", "neg": "color, colorful, saturated"},
    "colours": {"pos": "black and white, monochrome", "neg": "color, colorful, saturated"},
    "color": {"pos": "black and white, monochrome", "neg": "color, colorful, saturated"},
    "colors": {"pos": "black and white, monochrome", "neg": "color, colorful, saturated"},
    "people": {"pos": "empty, deserted", "neg": "people, person, crowd, figures"},
    "person": {"pos": "empty, deserted", "neg": "people, person, crowd, figures"},
    "humans": {"pos": "empty, deserted", "neg": "people, person, crowd, figures"},
    "crowd": {"pos": "empty, deserted", "neg": "people, person, crowd, figures"},
    "crowds": {"pos": "empty, deserted", "neg": "people, person, crowd, figures"},
    "background": {"pos": "plain white background, isolated", "neg": "cluttered background, scenery"},
    "text": {"neg": "text, words, letters, typography, watermark"},
    "words": {"neg": "text, words, letters, typography, watermark"},
    "letters": {"neg": "text, words, letters, typography, watermark"},
    "writing": {"neg": "text, words, letters, typography, watermark"},
    "captions": {"neg": "text, words, letters, typography, watermark"},
    "caption": {"neg": "text, words, letters, typography, watermark"},
    "watermark": {"neg": "watermark, signature, logo"},
    "watermarks": {"neg": "watermark, signature, logo"},
    "shadows": {"pos": "flat even lighting", "neg": "shadows, shading"},
    "shadow": {"pos": "flat even lighting", "neg": "shadows, shading"},
}

# Quality negatives added ONLY for CFG-capable models (turbo/schnell ignore negatives).
QUALITY_NEGATIVE = (
    "blurry, out of focus, low quality, lowres, jpeg artifacts, deformed, disfigured, "
    "extra limbs, extra fingers, mutated hands, bad anatomy, duplicate, cropped, watermark, signature"
)


@dataclass
class ImagePromptSpec:
    text: list[str] = field(default_factory=list)
    counts: list[dict] = field(default_factory=list)
    grid: Optional[list[int]] = None
    colors: list[str] = field(default_factory=list)
    layout: list[str] = field(default_factory=list)
    negated: list[str] = field(default_factory=list)
    style: list[str] = field(default_factory=list)


@dataclass
class CompiledImagePrompt:
    prompt: str
    negative: str
    spec: ImagePromptSpec
    est_tokens: int
    truncation_risk: bool
    notes: list[str]

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    # Short phrases the reranker can score individually ("the top center log highlighted
    # red") — a candidate that nails the subject but misses a constraint loses to one
    # that satisfies both.
    def constraint_views(self) -> list[str]:
        views: list[str] = []
        for clause in re.split(r"[,;]|\s+(?:and|with|while|plus)\s+", self.prompt):
            c = _squash(clause)
            if len(c.split()) >= 3 and c.lower() not in ("black and white", "highly detailed"):
                views.append(c)
        for t in self.spec.text:
            views.append(f'the text "{t}"')
        # de-dup, cap
        seen: set[str] = set()
        out: list[str] = []
        for v in views:
            k = v.lower()
            if k not in seen:
                seen.add(k)
                out.append(v)
        return out[:8]


def _squash(s: str) -> str:
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r"([,;:])\1+", r"\1", s)
    s = re.sub(r",\s*,", ",", s)
    s = re.sub(r"^[\s,;:.]+", "", s)
    s = re.sub(r"[\s,;:.]+$", "", s)
    return s.strip()


_HEAD_PATTERNS = [
    re.compile(r"^(?:please|pls|kindly|hey|hi|ok|okay)[,!]?\s+", re.I),
    re.compile(r"^(?:can|could|would|will)\s+you\s+(?:please\s+)?", re.I),
    re.compile(r"^(?:i\s+(?:want|need|would like|'d like|d like)\s+(?:you\s+to\s+)?|i\s+want\s+|i\s+need\s+)", re.I),
    re.compile(
        r"^(?:make|create|generate|draw|paint|render|design|produce|build|give|show|imagine|illustrate|depict|visualize|visualise|picture)\s+"
        r"(?:me\s+|us\s+)?(?:an?\s+|the\s+|some\s+)?(?:(?:high[- ]quality|detailed|nice|cool|beautiful|realistic|quick|simple)\s+)?"
        r"(?:image|picture|photo|photograph|pic|graphic|artwork|visual|render|rendering|illustration|drawing|painting|shot)s?\s+"
        r"(?:of|showing|depicting|with|that shows|featuring)\s+",
        re.I,
    ),
    re.compile(r"^(?:make|create|generate|draw|design|produce|build|imagine|illustrate|depict|visualize|visualise)\s+(?:me\s+|us\s+)?(?!of\b)(?=\S)", re.I),
    re.compile(r"^(?:an?\s+|the\s+)?(?:image|picture|photo|photograph|pic|graphic|artwork)\s+(?:of|showing|depicting)\s+", re.I),
    re.compile(r"^(?:show|give)\s+me\s+(?=\S)", re.I),
]
_LOOK_RE = re.compile(r"^(?:show\s+me\s+)?what\s+(?:does\s+|do\s+)?(.+?)\s+looks?\s+like\b[,:]?\s*(.*)$", re.I)


def _strip_instructions(p: str, notes: list[str]) -> str:
    out = p
    m = _LOOK_RE.match(out)
    if m:
        out = _squash(f"{m.group(1)} {m.group(2) or ''}")
        notes.append('stripped "what X looks like" wrapper')
    for _ in range(4):
        changed = False
        for rx in _HEAD_PATTERNS:
            nxt = rx.sub("", out, count=1)
            if nxt != out and nxt.strip():
                out = nxt
                changed = True
        if not changed:
            break
    out = _squash(out)
    if out != _squash(p) and not any(n.startswith("stripped") for n in notes):
        notes.append("stripped instruction wrapper")
    return out


_QUOTE_RE = re.compile(r'"([^"]{1,80})"|“([^”]{1,80})”|\'([^\']{2,80})\'(?=\s|[,.;:!?]|$)')


def _extract_quoted(p: str):
    text: list[str] = []
    holders: list[str] = []

    def repl(m: re.Match) -> str:
        inner = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if not inner:
            return m.group(0)
        text.append(inner)
        holders.append(f'"{inner}"')
        return f" «{len(holders) - 1}» "

    masked = _QUOTE_RE.sub(repl, p)

    def restore(s: str) -> str:
        return re.sub(r"«(\d+)»", lambda mm: holders[int(mm.group(1))] if int(mm.group(1)) < len(holders) else "", s)

    return text, masked, restore


_NEG_TAIL = r"(?=[,.;:!?]|$|\s+(?:and|but|with|in|on|at|that|which|while|under|over|near|beside|behind|against)\b)"
_NEG_PATTERNS = [
    re.compile(r"\b(?:with\s+no|without(?:\s+any)?|with\s+zero|no)\s+((?:[a-z][a-z'\-]*)(?:\s+[a-z][a-z'\-]*){0,3}?)" + _NEG_TAIL, re.I),
    re.compile(
        r"\b(?:who|that|which)?\s*(?:is|are|isn't|aren't|is\s+not|are\s+not)\s+(?:not\s+)?(?:wearing|holding|carrying|showing|using)\s+"
        r"((?:[a-z][a-z'\-]*)(?:\s+[a-z][a-z'\-]*){0,3}?)" + _NEG_TAIL,
        re.I,
    ),
]
_IS_WEARING_RE = re.compile(r"\b(?:is|are)\s+(?:wearing|holding|carrying|showing|using)", re.I)
_NOT_RE = re.compile(r"not|n't", re.I)


def _extract_negations(masked: str, notes: list[str]):
    negated: list[str] = []
    neg_phrases: list[str] = []
    pos_adds: list[str] = []
    positive = masked

    def repl(m: re.Match) -> str:
        whole = m.group(0)
        phrase = m.group(1)
        if _IS_WEARING_RE.search(whole) and not _NOT_RE.search(whole):
            return whole
        key = re.sub(r"^(?:a|an|the|any|some)\s+", "", phrase.strip().lower())
        first = key.split()[0] if key else ""
        if not key or first in _NO_STOPLIST:
            return whole
        if "«" in phrase:
            return whole
        negated.append(key)
        mapped = _NEGATION_MAP.get(key) or _NEGATION_MAP.get(first)
        neg_phrases.append(mapped["neg"] if mapped else key)
        if mapped and mapped.get("pos"):
            pos_adds.append(mapped["pos"])
        return " "

    for rx in _NEG_PATTERNS:
        positive = rx.sub(repl, positive)
    if negated:
        notes.append("moved to negative prompt: " + ", ".join(negated))
        positive = re.sub(r"\b(?:and|or|but|with|without|plus)\s*(?=[,;.:]|$)", "", positive, flags=re.I)
        positive = re.sub(r"([,;])\s*(?:and|or|but)\b\s*", r"\1 ", positive, flags=re.I)
        positive = re.sub(r"^\s*(?:and|or|but)\b\s*", "", positive, flags=re.I)
    return _squash(positive), negated, neg_phrases, pos_adds


def estimate_clip_tokens(s: str) -> int:
    n = 0
    for w in re.findall(r"[A-Za-z0-9]+|[^\sA-Za-z0-9]", s):
        if re.fullmatch(r"[A-Za-z0-9]+", w):
            n += 1 + max(0, len(w) - 2) // 6
        else:
            n += 1
    return n


def _dedupe_join(parts: list[str]) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        for x in p.split(","):
            x = _squash(x)
            if not x:
                continue
            k = x.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(x)
    return ", ".join(out)


def _in_order(body: str, words: list[str]) -> list[str]:
    rx = re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b")
    out: list[str] = []
    for m in rx.finditer(body):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


_COUNT_RE = re.compile(
    r"\b(" + "|".join(_NUMBER_WORDS) + r"|\d{1,2})\s+"
    r"(?:(?:" + "|".join(COLORS) + r"|small|large|big|tiny|little|huge|giant|identical|different|matching|wooden|metal|glass|stone|round|square)\s+){0,2}"
    r"([a-z]{3,}s?)\b"
)
_COUNT_SKIP = re.compile(r"^(?:x|by|of|and|with|in|on|at|to|for|the|k|px|mm|cm|inch|inches|hours?|minutes?|years?|steps?|percent|degrees?)$")
_GRID_RE = re.compile(r"\b(\d{1,2})\s*[x×]\s*(\d{1,2})\b")


def extract_spec(prompt: str, quoted: list[str], negated: list[str]) -> ImagePromptSpec:
    spec = ImagePromptSpec(text=list(quoted), negated=list(negated))
    body = re.sub(r'"[^"]*"', " ", prompt).lower()
    g = _GRID_RE.search(body)
    if g:
        spec.grid = [int(g.group(1)), int(g.group(2))]
    for m in _COUNT_RE.finditer(body):
        n = _NUMBER_WORDS.get(m.group(1))
        if n is None:
            try:
                n = int(m.group(1))
            except ValueError:
                continue
        noun = m.group(2)
        if n <= 0 or _COUNT_SKIP.match(noun):
            continue
        spec.counts.append({"n": n, "noun": noun})
    spec.colors = _in_order(body, COLORS)
    spec.layout = _in_order(body, LAYOUT_WORDS)
    spec.style = _in_order(body, STYLE_WORDS)
    return spec


def compile_image_prompt(raw: str, negative: Optional[str] = None) -> CompiledImagePrompt:
    notes: list[str] = []
    original = _squash(str(raw or ""))
    if not original:
        return CompiledImagePrompt("", _squash(negative or ""), ImagePromptSpec(), 0, False, notes)

    text, masked, restore = _extract_quoted(original)
    work = _strip_instructions(masked, notes)
    work, negated, neg_phrases, pos_adds = _extract_negations(work, notes)
    if pos_adds:
        adds = [a for a in pos_adds if a.split(",")[0].strip().lower() not in work.lower()]
        if adds:
            work = _squash(f"{work}, {_dedupe_join(adds)}")
    prompt = _squash(restore(work))
    spec = extract_spec(prompt, text, negated)
    neg = _dedupe_join([_squash(negative or ""), *neg_phrases])
    est = estimate_clip_tokens(prompt)
    risk = est > CLIP_TOKEN_BUDGET
    if risk:
        notes.append(f"~{est} CLIP tokens (budget {CLIP_TOKEN_BUDGET}) — put the most important details first")
    return CompiledImagePrompt(prompt, neg, spec, est, risk, notes)


def quality_negative(user_negative: str, spec: ImagePromptSpec) -> str:
    """Negative prompt for a CFG-capable model: the user's/extracted negatives plus generic
    quality negatives — minus anything the prompt explicitly asked for (requested text must
    not be negated away; a requested watermark/signature likewise)."""
    q = QUALITY_NEGATIVE
    if spec.text:
        q = ", ".join(t for t in q.split(", ") if t not in ("watermark", "signature"))
    return _dedupe_join([user_negative or "", q])


def load_vectors() -> dict:
    return json.loads((Path(__file__).parent / "prompt_vectors.json").read_text())
