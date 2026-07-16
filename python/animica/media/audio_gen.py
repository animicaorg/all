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
        import torch
        from transformers import AutoProcessor, MusicgenForConditionalGeneration
    except Exception as e:
        raise MediaBackendUnavailable(f"transformers/torch not installed: {e}") from e
    if model_id in _MODEL_CACHE:
        return _MODEL_CACHE[model_id]
    try:
        proc = AutoProcessor.from_pretrained(model_id)
        # Run on the miner's GPU when present — MusicGen on CPU is minutes-slow.
        # Default to fp32 even on CUDA: MusicGen's EnCodec audio decoder is known
        # to emit silence/NaN in fp16 on several transformers versions, so fp32 is
        # the reliable default (still far faster than CPU; musicgen-small ≈ 2.4 GiB
        # / medium ≈ 6 fit the audio VRAM floor). Opt into fp16 with
        # ANIMICA_AUDIO_FP16=1 to halve VRAM for the large model on tight cards.
        cuda = torch.cuda.is_available() and \
            os.environ.get("ANIMICA_AUDIO_FORCE_CPU", "") not in ("1", "true")
        fp16 = cuda and os.environ.get("ANIMICA_AUDIO_FP16", "") in ("1", "true")
        dtype = torch.float16 if fp16 else torch.float32
        model = MusicgenForConditionalGeneration.from_pretrained(
            model_id, torch_dtype=dtype)
        model = model.to("cuda" if cuda else "cpu")
        model.eval()
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

    # Place inputs on the model's device (GPU on a miner) so generation actually
    # runs there, not on CPU.
    device = next(mdl.parameters()).device
    inputs = proc(text=[prompt], padding=True, return_tensors="pt")
    inputs = {k: (v.to(device) if hasattr(v, "to") else v) for k, v in inputs.items()}
    # MusicGen: ~50 tokens/sec of audio at the model's frame rate.
    max_new = max(64, int(seconds * 50))

    def _run() -> tuple:
        with torch.no_grad():
            audio = mdl.generate(**inputs, max_new_tokens=max_new)
        sr = mdl.config.audio_encoder.sampling_rate
        # .float() so a half-precision (GPU fp16) tensor converts cleanly to WAV.
        return audio[0, 0].float().cpu().numpy(), sr

    try:
        samples, sr = _run()
    except RuntimeError as e:
        # CUDA OOM (torch.cuda.OutOfMemoryError subclasses RuntimeError) / dtype
        # issue → reclaim VRAM and retry on CPU (slow but reliable)
        # so a claimed audio job still produces real audio instead of failing.
        if device.type == "cuda":
            try:
                mdl_cpu = mdl.to("cpu").float()
                _MODEL_CACHE[model_id] = (proc, mdl_cpu)
                torch.cuda.empty_cache()
                inputs_cpu = {k: (v.to("cpu") if hasattr(v, "to") else v)
                              for k, v in inputs.items()}
                with torch.no_grad():
                    audio = mdl_cpu.generate(**inputs_cpu, max_new_tokens=max_new)
                sr = mdl_cpu.config.audio_encoder.sampling_rate
                samples = audio[0, 0].float().cpu().numpy()
            except Exception as e2:
                raise MediaError(f"audio generation failed (gpu+cpu): {e2}") from e2
        else:
            raise MediaError(f"audio generation failed: {e}") from e
    except Exception as e:
        raise MediaError(f"audio generation failed: {e}") from e

    data = encode_wav(samples, sr)
    return {"bytes": data, "mime": "audio/wav", "model": model_id, "sha3": sha3_hex(data),
            "sample_rate": sr, "seconds": round(len(samples) / sr, 2)}
