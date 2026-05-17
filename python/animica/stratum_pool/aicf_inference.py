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
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional

log = logging.getLogger("animica.stratum_pool.aicf_inference")


# Per-tier default model when ANIMICA_AICF_MODEL isn't set.
_TIER_DEFAULT_MODEL: dict[str, str] = {
    "free":     "Qwen/Qwen2.5-0.5B-Instruct",
    "standard": "Qwen/Qwen2.5-0.5B-Instruct",
    "premium":  "Qwen/Qwen2.5-1.5B-Instruct",
    "elite":    "Qwen/Qwen2.5-3B-Instruct",
}
_DEFAULT_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"


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
        return _TIER_DEFAULT_MODEL.get(tier, _DEFAULT_MODEL)

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
            if device == "cuda":
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
                inputs = self._tokenizer(prompt, return_tensors="pt")
                if self._device == "cuda":
                    inputs = {k: v.to("cuda") for k, v in inputs.items()}
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
