"""Audio generation for AICF media jobs (music / sfx / short clips).

Uses MusicGen (transformers) by default. Output is WAV bytes, fail-closed. The WAV encoder uses the
stdlib `wave` module so it is CPU-verifiable without loading a model.
"""

from __future__ import annotations

import io
import os
import wave
from typing import Optional

from .base import MediaError, MediaBackendUnavailable, sha3_hex, validate_magic

_TIER_AUDIO_MODELS = {
    "standard": "facebook/musicgen-small",
    "premium": "facebook/musicgen-medium",
    "elite": "facebook/musicgen-large",
}

_MODEL_CACHE: dict[str, object] = {}


def resolve_audio_model(tier: str | None) -> str:
    env = os.environ.get("ANIMICA_AUDIO_MODEL")
    if env:
        return env
    t = (tier or "standard").strip().lower()
    return _TIER_AUDIO_MODELS.get(t, _TIER_AUDIO_MODELS["standard"])


def encode_wav(samples, sample_rate: int) -> bytes:
    """Encode a mono/stereo float or int16 array to 16-bit PCM WAV. CPU-verifiable, no model."""
    try:
        import numpy as np
    except Exception as e:
        raise MediaBackendUnavailable(f"numpy not installed: {e}") from e
    arr = np.asarray(samples)
    if arr.size == 0:
        raise MediaError("no audio samples")
    if arr.dtype.kind == "f":
        peak = float(np.max(np.abs(arr))) or 1.0
        arr = np.clip(arr / peak, -1.0, 1.0)
        arr = (arr * 32767.0).astype("<i2")
    else:
        arr = arr.astype("<i2")
    channels = 1 if arr.ndim == 1 else arr.shape[-1]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(arr.tobytes())
    data = buf.getvalue()
    if not validate_magic(data, "wav"):
        raise MediaError("encoded output is not a valid WAV")
    return data


def _load(model_id: str):
    try:
        import torch  # noqa: F401
        from transformers import AutoProcessor, MusicgenForConditionalGeneration
    except Exception as e:
        raise MediaBackendUnavailable(f"transformers/torch not installed: {e}") from e
    if model_id in _MODEL_CACHE:
        return _MODEL_CACHE[model_id]
    try:
        proc = AutoProcessor.from_pretrained(model_id)
        model = MusicgenForConditionalGeneration.from_pretrained(model_id)
    except Exception as e:
        raise MediaError(f"failed to load audio model {model_id!r}: {e}") from e
    _MODEL_CACHE[model_id] = (proc, model)
    return proc, model


def generate_audio(prompt: str, *, tier: str = "standard", seconds: float = 5.0,
                   model: Optional[str] = None) -> dict:
    """Generate a short audio clip from a text prompt. Returns WAV bytes (fail-closed)."""
    if not prompt or not prompt.strip():
        raise MediaError("empty prompt")
    model_id = model or resolve_audio_model(tier)
    proc, mdl = _load(model_id)
    import torch

    inputs = proc(text=[prompt], padding=True, return_tensors="pt")
    # MusicGen: ~50 tokens/sec of audio at the model's frame rate.
    max_new = max(64, int(seconds * 50))
    try:
        with torch.no_grad():
            audio = mdl.generate(**inputs, max_new_tokens=max_new)
        sr = mdl.config.audio_encoder.sampling_rate
        samples = audio[0, 0].cpu().numpy()
    except Exception as e:
        raise MediaError(f"audio generation failed: {e}") from e

    data = encode_wav(samples, sr)
    return {"bytes": data, "mime": "audio/wav", "model": model_id, "sha3": sha3_hex(data),
            "sample_rate": sr, "seconds": round(len(samples) / sr, 2)}
