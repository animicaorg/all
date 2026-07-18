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

_MODEL_CACHE: dict[tuple, object] = {}


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


def _cuda_on(force_cpu: bool) -> bool:
    if force_cpu:
        return False
    try:
        import torch
    except Exception:
        return False
    return bool(torch.cuda.is_available()) and \
        os.environ.get("ANIMICA_AUDIO_FORCE_CPU", "") not in ("1", "true")


# Each loader strategy returns a `synth(prompt, max_new) -> (samples_ndarray, sample_rate)`
# closure or raises. We try them in order because the low-level Auto path
# (MusicgenForConditionalGeneration.from_pretrained WITHOUT an explicit config) regresses on
# transformers>=4.44 with "'MusicgenDecoderConfig' object has no attribute 'decoder'" — the
# Auto config resolves to the decoder sub-config instead of the composite MusicgenConfig.

def _synth_lowlevel(model_id: str, cuda: bool, fp16: bool, explicit_config: bool):
    import torch
    from transformers import AutoProcessor, MusicgenForConditionalGeneration
    # Default fp32 even on CUDA: MusicGen's EnCodec decoder emits silence/NaN in fp16 on
    # several transformers versions; opt into fp16 with ANIMICA_AUDIO_FP16=1.
    dtype = torch.float16 if fp16 else torch.float32
    proc = AutoProcessor.from_pretrained(model_id)
    kwargs = {"torch_dtype": dtype}
    if explicit_config:
        # Force the COMPOSITE config class so from_pretrained can't fall back to the
        # decoder-only config (the root cause of the 4.44+ 'decoder' AttributeError).
        from transformers import MusicgenConfig
        kwargs["config"] = MusicgenConfig.from_pretrained(model_id)
    model = MusicgenForConditionalGeneration.from_pretrained(model_id, **kwargs)
    model = model.to("cuda" if cuda else "cpu")
    model.eval()

    def synth(prompt: str, max_new: int):
        device = next(model.parameters()).device
        inputs = proc(text=[prompt], padding=True, return_tensors="pt")
        inputs = {k: (v.to(device) if hasattr(v, "to") else v) for k, v in inputs.items()}
        with torch.no_grad():
            audio = model.generate(**inputs, max_new_tokens=max_new)
        sr = model.config.audio_encoder.sampling_rate
        return audio[0, 0].float().cpu().numpy(), int(sr)
    return synth


def _synth_pipeline(model_id: str, cuda: bool, fp16: bool):
    # The high-level text-to-audio pipeline builds MusicGen's composite model correctly and
    # is the officially documented entry point, so it sidesteps the low-level config bug.
    import torch
    from transformers import pipeline
    kw: dict = {"device": 0 if cuda else -1}
    if fp16:
        kw["torch_dtype"] = torch.float16
    pipe = pipeline("text-to-audio", model=model_id, **kw)

    def synth(prompt: str, max_new: int):
        out = pipe(prompt, forward_params={"max_new_tokens": max_new, "do_sample": True})
        if isinstance(out, list):
            out = out[0]
        return out["audio"], int(out["sampling_rate"])
    return synth


# Order: explicit-config low-level (the precise fix, keeps our fp32/EnCodec handling) →
# pipeline (robust alternative) → plain Auto low-level (original; works where un-regressed).
_STRATEGIES = ("lowlevel_config", "pipeline", "lowlevel_auto")


def _load(model_id: str, force_cpu: bool = False):
    key = (model_id, force_cpu)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except Exception as e:
        raise MediaBackendUnavailable(f"transformers/torch not installed: {e}") from e
    cuda = _cuda_on(force_cpu)
    fp16 = cuda and os.environ.get("ANIMICA_AUDIO_FP16", "") in ("1", "true")
    errors = []
    for strat in _STRATEGIES:
        try:
            if strat == "lowlevel_config":
                synth = _synth_lowlevel(model_id, cuda, fp16, explicit_config=True)
            elif strat == "pipeline":
                synth = _synth_pipeline(model_id, cuda, fp16)
            else:
                synth = _synth_lowlevel(model_id, cuda, fp16, explicit_config=False)
            _MODEL_CACHE[key] = synth
            return synth
        except Exception as e:  # try the next strategy
            errors.append(f"{strat}: {type(e).__name__}: {e}")
    raise MediaError(f"failed to load audio model {model_id!r}: " + " | ".join(errors))


def _to_wav_array(audio):
    """Normalize a synth's audio output to what encode_wav expects: 1-D mono, or
    2-D (samples, channels). The pipeline yields (1, n)/(channels, n); the low-level path
    yields 1-D already."""
    import numpy as np
    a = np.asarray(audio)
    a = np.squeeze(a)
    if a.ndim == 2 and a.shape[0] < a.shape[1]:   # (channels, samples) -> (samples, channels)
        a = a.T
    return a


def _is_gpu_error(e: Exception) -> bool:
    s = str(e).lower()
    return isinstance(e, RuntimeError) or "out of memory" in s or "cuda" in s or "cublas" in s


def generate_audio(prompt: str, *, tier: str = "standard", seconds: float = 5.0,
                   model: Optional[str] = None) -> dict:
    """Generate a short audio clip from a text prompt. Returns WAV bytes (fail-closed)."""
    if not prompt or not prompt.strip():
        raise MediaError("empty prompt")
    model_id = model or resolve_audio_model(tier)
    # MusicGen: ~50 tokens/sec of audio at the model's frame rate.
    max_new = max(64, int(seconds * 50))
    synth = _load(model_id)
    try:
        samples, sr = synth(prompt, max_new)
    except Exception as e:
        # GPU OOM / dtype issue → reclaim VRAM and retry on CPU (slow but reliable) so a
        # claimed audio job still produces real audio instead of failing.
        if _cuda_on(force_cpu=False) and _is_gpu_error(e):
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass
            try:
                samples, sr = _load(model_id, force_cpu=True)(prompt, max_new)
            except Exception as e2:
                raise MediaError(f"audio generation failed (gpu+cpu): {e2}") from e2
        else:
            raise MediaError(f"audio generation failed: {e}") from e

    samples = _to_wav_array(samples)
    data = encode_wav(samples, sr)
    n = samples.shape[0]
    return {"bytes": data, "mime": "audio/wav", "model": model_id, "sha3": sha3_hex(data),
            "sample_rate": sr, "seconds": round(n / sr, 2)}
