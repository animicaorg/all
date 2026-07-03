"""
Lightweight AICF inference engine for miners.

Resolves a model identifier (ANIMICA_AICF_MODEL or per-tier defaults),
downloads weights via `huggingface_hub` if not cached, and serves
single-turn generation. Uses `transformers` if available; otherwise
falls back to a clearly-labeled stub response so the mining.aicf.*
protocol round-trip still completes.

The engine is intentionally minimal — single-threaded, single-model.
Production miners would wrap this with a multi-model scheduler. For
the first end-to-end demonstration, "one miner serves one model"
matches the architecture-miners-as-aicf-workers note.

Activation:
- ANIMICA_AICF_TIERS=standard,premium      (advertise these to the pool)
- ANIMICA_AICF_MODEL=Qwen/Qwen2.5-0.5B-Instruct   (HF repo id)
- ANIMICA_AICF_MAX_TOKENS=128              (cap per job; default 128)
- ANIMICA_AICF_DEVICE=cpu                  (cpu|cuda; default auto)

If `transformers` and `torch` aren't importable, the engine still
produces output (the stub), so chat round-trips work without the
~2GB heavy-deps install. Users who actually want real inference must
`pip install animica[gpu]` (or equivalent) to pull the ML stack.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

log = logging.getLogger("animica.stratum_pool.aicf_inference")


# Fallback per-tier defaults used when nothing is auto-detected and no
# operator override is set. Conservative (small) so the round-trip
# still works on a fresh install. Auto-detection (see resolve_tier_model)
# replaces these with larger models from the HF cache or a local bundle
# dir whenever they're available.
_FALLBACK_TIER_MODEL: dict[str, str] = {
    "free":     "Qwen/Qwen2.5-0.5B-Instruct",
    "standard": "Qwen/Qwen2.5-1.5B-Instruct",
    "premium":  "Qwen/Qwen2.5-3B-Instruct",
    "elite":    "Qwen/Qwen2.5-7B-Instruct",
}
_DEFAULT_MODEL = _FALLBACK_TIER_MODEL["standard"]

# Tier → (min_billion_params, max_billion_params) used to bucket detected
# models. Top of range is exclusive so a 7B model lands in "premium",
# not "elite". A model whose size we can't parse goes to "standard".
_TIER_RANGES: list[tuple[str, float, float]] = [
    ("free",     0.0,   1.5),
    ("standard", 1.5,   5.0),
    ("premium",  5.0,  15.0),
    ("elite",   15.0,  10_000.0),
]

_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def _parse_param_billions(name: str) -> Optional[float]:
    """Best-effort parameter count in billions from a model id.

    Recognizes patterns like `7B`, `13b`, `0.5B`, `70B`. Returns None if
    no clear size token is found — callers default to the standard tier.
    """
    # Avoid matching transformer-block counts ("32B-block") by requiring a
    # word boundary on both sides via the regex.
    for m in _SIZE_RE.finditer(name):
        try:
            return float(m.group(1))
        except ValueError:
            continue
    # A "-mini" / "-small" / "-tiny" hint without an explicit count.
    # Anchor these to a hyphen / underscore boundary so substring matches
    # like "MiniLM" or "tiny-model-config" don't trigger.
    lname = name.lower()
    if re.search(r"[-_/]mini([-_/]|$)", lname):
        return 3.8   # Phi-3-mini class
    if re.search(r"[-_/]small([-_/]|$)", lname):
        return 7.0
    if re.search(r"[-_/]tiny([-_/]|$)", lname):
        return 0.5
    if re.search(r"[-_/]medium([-_/]|$)", lname):
        return 14.0
    return None


# Repo prefixes / name fragments that identify models we cannot serve as
# chat workers (embedding-only, audio, vision-only, etc.). Discovery
# silently filters these out so they never get picked for a chat tier.
_NON_GENERATIVE_PREFIXES = (
    "sentence-transformers/",
    "BAAI/bge-",
    "intfloat/e5-",
    "nomic-ai/nomic-embed",
    "thenlper/gte-",
    "jinaai/jina-embeddings",
)
_NON_GENERATIVE_FRAGMENTS = (
    "minilm",       # sentence embeddings
    "-embed",       # generic embedding suffix
    "embedding",
    "reranker",
    "cross-encoder",
    "whisper",      # ASR
    "wav2vec",
    "clip-vit",     # vision encoder
)


def _is_generative_chat_model(identifier: str) -> bool:
    lname = identifier.lower()
    if any(lname.startswith(p.lower()) for p in _NON_GENERATIVE_PREFIXES):
        return False
    if any(frag in lname for frag in _NON_GENERATIVE_FRAGMENTS):
        return False
    return True


def _classify_tier(model_id: str) -> str:
    p = _parse_param_billions(model_id)
    if p is None:
        return "standard"
    for tier, lo, hi in _TIER_RANGES:
        if lo <= p < hi:
            return tier
    return "elite"


def _scan_hf_cache() -> list[str]:
    """Return HuggingFace repo IDs present in the local HF hub cache."""
    cache_root = Path(
        os.environ.get("HUGGINGFACE_HUB_CACHE")
        or os.environ.get("HF_HOME", "")
        or str(Path.home() / ".cache" / "huggingface" / "hub")
    )
    if not cache_root.is_dir():
        return []
    out: list[str] = []
    for child in cache_root.iterdir():
        # HF lays out cache as: models--<org>--<repo> (snapshots inside).
        name = child.name
        if not (child.is_dir() and name.startswith("models--")):
            continue
        repo = name[len("models--"):].replace("--", "/", 1)
        # Only count it if at least one snapshot exists.
        snaps = child / "snapshots"
        if snaps.is_dir() and any(snaps.iterdir()):
            out.append(repo)
    return sorted(out)


def _scan_local_bundles() -> list[str]:
    """Return absolute paths of local model bundles ready to load.

    A bundle is any directory containing a `config.json` (transformers
    convention). Scans operator-provided dirs first, then the Animica
    flagship-agent default.
    """
    candidates: list[Path] = []
    env_dir = os.environ.get("ANIMICA_AICF_MODEL_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(Path("/root/animica/ai/flagship_agent/models"))
    out: list[str] = []
    seen: set[str] = set()
    for root in candidates:
        if not root.is_dir():
            continue
        # Two patterns supported:
        #   <root>/<bundle>/config.json     — flat layout
        #   <root>/<family>/<bundle>/config.json — grouped (e.g. by org)
        for cfg in list(root.glob("*/config.json")) + list(root.glob("*/*/config.json")):
            bundle = cfg.parent
            key = str(bundle.resolve())
            if key not in seen:
                seen.add(key)
                out.append(key)
    return sorted(out)


@dataclass(frozen=True)
class ModelChoice:
    identifier: str          # HF repo id or absolute bundle path
    source: str              # "env_override" | "hf_cache" | "local_bundle" | "fallback"
    approx_billions: Optional[float]


def discover_models() -> list[ModelChoice]:
    """Enumerate all models the miner could load without downloading.

    Order: operator env overrides first, then local bundles, then HF
    cache hits. Fallback Qwen models are appended at the tail so the
    caller can always pick something even on a bare install.
    """
    out: list[ModelChoice] = []
    seen: set[str] = set()

    def _add(ident: str, source: str) -> None:
        key = ident.strip()
        if not key or key in seen:
            return
        # Skip non-chat models discovered from HF cache / bundles. Env
        # overrides bypass the filter — operator gets the final say.
        if source not in ("env_override",) and not _is_generative_chat_model(key):
            return
        seen.add(key)
        out.append(ModelChoice(
            identifier=key,
            source=source,
            approx_billions=_parse_param_billions(key),
        ))

    # Operator overrides (per-tier env vars).
    for tier, _, _ in _TIER_RANGES:
        ident = os.environ.get(f"ANIMICA_AICF_MODEL_{tier.upper()}", "").strip()
        if ident:
            _add(ident, "env_override")
    # Operator-pinned single model wins for whichever tier it lands in.
    pinned = os.environ.get("ANIMICA_AICF_MODEL", "").strip()
    if pinned:
        _add(pinned, "env_override")
    # Local bundles + HF cache (no network needed).
    for path in _scan_local_bundles():
        _add(path, "local_bundle")
    for repo in _scan_hf_cache():
        _add(repo, "hf_cache")
    # Fallback Qwen ladder so a fresh install still has something to
    # advertise per tier; the engine downloads on first use.
    for model in _FALLBACK_TIER_MODEL.values():
        _add(model, "fallback")
    return out


def resolve_tier_model(tier: str) -> str:
    """Pick the best available model for `tier`.

    Resolution order:
      1. ``ANIMICA_AICF_MODEL_<TIER>`` env override.
      2. ``ANIMICA_AICF_MODEL`` global pin (used regardless of tier).
      3. Largest local bundle or HF-cache hit that classifies into `tier`.
      4. Largest model that lands in any tier ≤ requested (downshift so
         a free-tier request never tries to load a 70B model).
      5. ``_FALLBACK_TIER_MODEL`` hard-coded default.
    """
    override = os.environ.get(f"ANIMICA_AICF_MODEL_{tier.upper()}", "").strip()
    if override:
        return override
    global_pin = os.environ.get("ANIMICA_AICF_MODEL", "").strip()
    if global_pin:
        return global_pin

    target_tier_rank = {t: i for i, (t, _, _) in enumerate(_TIER_RANGES)}.get(tier, 1)
    in_tier: list[ModelChoice] = []
    in_or_below: list[tuple[int, ModelChoice]] = []
    for mc in discover_models():
        if mc.source == "fallback":
            continue
        mc_tier = _classify_tier(mc.identifier)
        if mc_tier == tier:
            in_tier.append(mc)
        rank = {t: i for i, (t, _, _) in enumerate(_TIER_RANGES)}.get(mc_tier, -1)
        if rank != -1 and rank <= target_tier_rank:
            in_or_below.append((rank, mc))

    def _size_key(m: ModelChoice) -> float:
        return m.approx_billions or 0.0

    if in_tier:
        return max(in_tier, key=_size_key).identifier
    if in_or_below:
        # Highest tier (closest to requested) and within that, largest model.
        in_or_below.sort(key=lambda r: (r[0], _size_key(r[1])), reverse=True)
        return in_or_below[0][1].identifier
    return _FALLBACK_TIER_MODEL.get(tier, _DEFAULT_MODEL)


# Back-compat shim: anything still reading the static dict (tests, CLI)
# gets the resolved per-tier picks at import time. Resolution is also
# re-done on every generate() call so a miner can pick up new bundles
# without restarting.
_TIER_DEFAULT_MODEL: dict[str, str] = {
    tier: resolve_tier_model(tier) for tier, _, _ in _TIER_RANGES
}


# System prompt prepended to every chat turn so off-the-shelf small
# models (Qwen 0.5B, etc.) have factual grounding about Animica instead
# of hallucinating a "mobile games division" or a generic
# "Hello! How can I assist you today?" greeting. Every token here costs
# the user at quote time, so it stays dense and factual — no marketing
# copy. Override with ANIMICA_AICF_SYSTEM_PROMPT to customize (set to
# empty string to disable).
_DEFAULT_SYSTEM_PROMPT = (
    "You are the Animica assistant, served by a live Animica AICF worker. "
    "This reply is generated by a real miner on the Animica network and "
    "the miner is paid in ANIMICA tokens for serving it. Answer the user's "
    "question directly; do NOT open with a generic greeting. If you don't "
    "know an Animica-specific detail, say so plainly rather than guessing.\n"
    "\n"
    "Reference facts about Animica (use these as ground truth):\n"
    "- Animica is a fully decentralized Layer-1 blockchain for verifiable "
    "AI and quantum-secure execution. Native token: ANIMICA (denominated in "
    "9-decimal nano-units on-chain). Native CLI: the `animica` PyPI package.\n"
    "- Consensus: PoIES (Proof of Integrated External Services) — hash-share "
    "PoW combined with AI / quantum / storage proofs. Public mainnet "
    "Stratum pool: pool.animica.org:3333. Public mainnet RPC: "
    "https://rpc.animica.org/rpc.\n"
    "- Post-quantum signatures: Dilithium3 (ML-DSA-65) for transactions, "
    "SPHINCS+ as a fallback. No ECDSA / secp256k1.\n"
    "- Execution: deterministic Python-VM smart contracts with gas metering. "
    "Required raw-tx fields: kind, data, gasLimit, fee, nonce, chainId. "
    "Submit via tx.sendRawTransaction; debug failures via tx.explainReject "
    "and tx.debugVerifyRawTransaction.\n"
    "- AICF (AI Compute Fund): miners earn ANIMICA per inference turn. Tiers "
    "are tiny (CPU), small (~7B, single GPU), flagship (16B MoE — recommended), "
    "large (datacenter).\n"
    "- DA layer: namespaced data availability via an NMT (Namespaced Merkle "
    "Tree). RPC namespace is `da.*`. Chat threads are persisted as DA blobs "
    "so any client owned by the same wallet (CLI, studio.animica.org, "
    "mobile) can resume them.\n"
    "- RPC namespaces exposed by nodes: state.*, chain.*, miner.*, tx.*, "
    "aicf.*, da.*. There is NO `animica.*` namespace — do not invent one.\n"
    "- Studio: a web IDE at studio.animica.org to edit → simulate → deploy "
    "→ verify Python-VM contracts. The Animica browser extension is the "
    "user's wallet on web.\n"
    "- To run a miner: `pip install animica` then "
    "`animica mine --pool pool.animica.org:3333 --wallet <addr>`. Set "
    "ANIMICA_AICF_TIERS=<tier> to additionally serve AICF jobs.\n"
    "\n"
    "Style: concise (1–3 short paragraphs unless asked for detail), use "
    "markdown when helpful, stay grounded in the facts above."
)


def _resolve_system_prompt() -> str:
    raw = os.environ.get("ANIMICA_AICF_SYSTEM_PROMPT")
    if raw is None:
        return _DEFAULT_SYSTEM_PROMPT
    # Explicit empty string disables the system message.
    return raw


@dataclass
class InferenceResult:
    text: str
    latency_ms: int
    used_model: str
    used_backend: str   # "transformers" | "stub"


def _stub_response(prompt: str, *, used_model: str, reason: str) -> str:
    truncated = prompt[:280] + ("…" if len(prompt) > 280 else "")
    return (
        f"[aicf-miner-stub: {reason}; intended model={used_model}] "
        f"Echoing your prompt so the protocol round-trip completes: "
        f"{truncated}"
    )


class InferenceEngine:
    """Lazy-loaded single-model inference engine for miners.

    Thread-safe: a single lock serializes generation so concurrent
    AICF jobs claim the GPU one at a time. Multi-model / batched
    serving is a future extension.
    """

    def __init__(
        self,
        *,
        model_id: Optional[str] = None,
        device: Optional[str] = None,
        max_new_tokens_cap: int = 128,
    ) -> None:
        self._model_id = (model_id or os.environ.get("ANIMICA_AICF_MODEL") or "").strip()
        self._device = (device or os.environ.get("ANIMICA_AICF_DEVICE") or "").strip().lower()
        try:
            self._max_new_tokens_cap = int(
                os.environ.get("ANIMICA_AICF_MAX_TOKENS", str(max_new_tokens_cap))
            )
        except (TypeError, ValueError):
            self._max_new_tokens_cap = int(max_new_tokens_cap)
        self._lock = threading.Lock()
        self._loaded = False
        self._tokenizer: Any = None
        self._model: Any = None
        self._effective_model: str = self._model_id

    @staticmethod
    def _resolve_model(spec: Mapping[str, Any], tier: str, override: str) -> str:
        if override:
            return override
        meta = spec.get("metadata") if isinstance(spec, Mapping) else None
        if isinstance(meta, Mapping):
            m = meta.get("model")
            if isinstance(m, str) and m.strip():
                return m.strip()
        # Re-resolve every call so freshly-downloaded models get picked up
        # without restarting the miner. The lookup walks ~/.cache/huggingface
        # and the local bundle dir — both cheap (no network).
        return resolve_tier_model(tier)

    def _try_load(self, model_id: str) -> Optional[str]:
        """Load tokenizer + model. Returns None on success, error string on fail."""
        try:
            import torch  # type: ignore
            from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore
        except Exception as exc:
            return f"ml_stack_unavailable: {exc}"
        try:
            device = self._device or ("cuda" if torch.cuda.is_available() else "cpu")
            self._tokenizer = AutoTokenizer.from_pretrained(model_id)
            kwargs: dict[str, Any] = {}
            # startswith so an explicit "cuda:0"/"cuda:1" also GPU-loads
            # (a bare `== "cuda"` left those on CPU).
            if device.startswith("cuda"):
                kwargs["torch_dtype"] = torch.float16
                kwargs["device_map"] = "auto"
            self._model = AutoModelForCausalLM.from_pretrained(model_id, **kwargs)
            if device == "cpu":
                self._model = self._model.to("cpu")
            self._loaded = True
            self._effective_model = model_id
            log.info(
                "aicf inference engine ready: model=%s device=%s", model_id, device
            )
            return None
        except Exception as exc:
            log.warning("aicf model load failed for %s: %s", model_id, exc)
            self._tokenizer = None
            self._model = None
            self._loaded = False
            return f"model_load_failed: {exc}"

    def generate(
        self, spec: Mapping[str, Any], *, tier: str = "standard"
    ) -> InferenceResult:
        prompt = ""
        if isinstance(spec, Mapping):
            prompt = str(spec.get("prompt") or "")
        if not prompt:
            return InferenceResult(
                text=_stub_response("", used_model="(none)", reason="empty_prompt"),
                latency_ms=0,
                used_model="(none)",
                used_backend="stub",
            )

        requested_model = self._resolve_model(spec, tier, self._model_id)
        t0 = time.perf_counter()
        with self._lock:
            if not self._loaded or self._effective_model != requested_model:
                err = self._try_load(requested_model)
                if err is not None:
                    return InferenceResult(
                        text=_stub_response(prompt, used_model=requested_model, reason=err),
                        latency_ms=int((time.perf_counter() - t0) * 1000),
                        used_model=requested_model,
                        used_backend="stub",
                    )
            try:
                import torch  # type: ignore
                max_out = int(spec.get("max_output_tokens") or 64)
                max_out = max(1, min(max_out, self._max_new_tokens_cap))
                # Use the tokenizer's chat template when available so the
                # system + user roles are honored. Without this, the
                # model sees a raw string with no context and happily
                # hallucinates (e.g. "Animica's mobile games division").
                system_prompt = _resolve_system_prompt()
                # RAG: pull top-k relevant doc chunks for this query and
                # prepend them to the system message. The retriever is
                # silent-noop when the index or encoder isn't available,
                # so this stays cheap and safe on minimal installs.
                try:
                    from .aicf_rag import retrieve_context
                    rag_block = retrieve_context(prompt, top_k=3)
                except Exception as rag_exc:
                    log.debug("aicf-rag: retrieve_context unavailable: %s", rag_exc)
                    rag_block = ""
                if rag_block:
                    system_prompt = (
                        (system_prompt + "\n\n" if system_prompt else "")
                        + "Reference excerpts from Animica documentation "
                        "(use these as ground truth; cite sources inline "
                        "when relevant):\n\n" + rag_block
                    )
                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                messages.append({"role": "user", "content": prompt})
                chat_tmpl = getattr(self._tokenizer, "apply_chat_template", None)
                if callable(chat_tmpl):
                    try:
                        out = chat_tmpl(
                            messages,
                            tokenize=True,
                            add_generation_prompt=True,
                            return_tensors="pt",
                        )
                        # transformers ≥5 returns a BatchEncoding (dict-like)
                        # where 4.x returned the raw tensor. Passing the
                        # BatchEncoding straight into model.generate() crashes
                        # in generation/utils.py reading .shape on it, which
                        # surfaced as `aicf generation failed: AttributeError`
                        # and stub-completed every chat job on transformers 5.
                        if hasattr(out, "input_ids"):
                            inputs = {"input_ids": out.input_ids}
                            if getattr(out, "attention_mask", None) is not None:
                                inputs["attention_mask"] = out.attention_mask
                        elif isinstance(out, dict) and "input_ids" in out:
                            inputs = {k: v for k, v in out.items() if k in ("input_ids", "attention_mask")}
                        else:
                            inputs = {"input_ids": out}
                    except Exception as tmpl_exc:
                        # Some tokenizers ship without a chat template;
                        # fall back to a plain-text framing.
                        log.debug("apply_chat_template failed: %s", tmpl_exc)
                        framed = (
                            (system_prompt + "\n\n" if system_prompt else "")
                            + prompt
                        )
                        inputs = self._tokenizer(framed, return_tensors="pt")
                else:
                    framed = (
                        (system_prompt + "\n\n" if system_prompt else "")
                        + prompt
                    )
                    inputs = self._tokenizer(framed, return_tensors="pt")
                # Move inputs onto the model's ACTUAL device rather than
                # guessing from a config string. A hardcoded `== "cuda"`
                # check missed `cuda:0`, `device_map="auto"` sharded models,
                # and mps — leaving input_ids on cpu while the weights sat on
                # cuda:0, which crashes generate() with "Expected all tensors
                # to be on the same device … index is on cpu, different from
                # cuda:0" and took down every useful-work miner's PoW loop.
                try:
                    _model_device = next(self._model.parameters()).device
                except (StopIteration, AttributeError):
                    _model_device = getattr(self._model, "device", None)
                if _model_device is not None and str(_model_device) != "cpu":
                    inputs = {
                        k: (v.to(_model_device) if hasattr(v, "to") else v)
                        for k, v in inputs.items()
                    }
                with torch.inference_mode():
                    output_ids = self._model.generate(
                        **inputs,
                        max_new_tokens=max_out,
                        do_sample=False,
                        pad_token_id=getattr(
                            self._tokenizer, "eos_token_id", None
                        ),
                    )
                # Strip prompt tokens from the output
                in_len = inputs["input_ids"].shape[-1] if "input_ids" in inputs else 0
                gen_ids = output_ids[0][in_len:]
                text = self._tokenizer.decode(gen_ids, skip_special_tokens=True)
                return InferenceResult(
                    text=text.strip(),
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                    used_model=self._effective_model,
                    used_backend="transformers",
                )
            except Exception as exc:
                log.warning("aicf generation failed: %s", exc)
                return InferenceResult(
                    text=_stub_response(
                        prompt,
                        used_model=self._effective_model,
                        reason=f"generation_error: {exc}",
                    ),
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                    used_model=self._effective_model,
                    used_backend="stub",
                )


_GLOBAL_ENGINE: Optional[InferenceEngine] = None
_GLOBAL_LOCK = threading.Lock()


def get_engine() -> InferenceEngine:
    """Process-singleton engine. Initialized lazily so simply importing
    this module doesn't trigger a ~2GB model download."""
    global _GLOBAL_ENGINE
    with _GLOBAL_LOCK:
        if _GLOBAL_ENGINE is None:
            _GLOBAL_ENGINE = InferenceEngine()
        return _GLOBAL_ENGINE


__all__ = ["InferenceEngine", "InferenceResult", "get_engine"]
