"""Text-to-image generation for AICF media jobs (diffusers).

Called by the miner worker runtime when it claims an `image` job. Returns PNG bytes or raises.
Never returns a placeholder (fail-closed contract — see media/__init__.py).

11.1.0 — prompt-fidelity pipeline. Accuracy against *specific* prompts comes from five
things a bare ``pipe(prompt)`` call gets wrong:

1. **Prompt compilation** (`prompt_spec`): instruction wrappers stripped, negations moved
   out of the positive prompt (a text encoder has no "no"), a machine-readable spec.
2. **Right step/guidance regime per model family**: sd-turbo/sdxl-turbo/schnell are
   1-4-step distilled models (4 is the faithful end; we used 2); CFG models get 28 steps of
   DPM++ 2M Karras with a real negative prompt.
3. **No silent truncation**: CLIP-based models see only 77 tokens. Long prompts are encoded
   in 75-token chunks and concatenated (SD1/2 and SDXL), so every detail reaches the UNet.
4. **Best-of-N with a fidelity judge** (`image_fidelity`): several seeds are rendered and
   CLIP keeps the candidate that matches the prompt and its constraint clauses best — and
   penalizes any that drew a negated concept (turbo models ignore negatives otherwise).
5. **Native resolution**: each family is rendered in its trained regime (sd-turbo 512²,
   SDXL 1024²) and brought to the EXACT requested size — optionally via an img2img refine
   pass — instead of asking a 512-model for 768² and getting doubled subjects.

Every result carries its full recipe (model, seed, steps, guidance, scheduler, prompt,
negative) in the PNG ``parameters`` chunk and in the job meta, so any image is reproducible.
"""

from __future__ import annotations

import io
import json
import math
import os
import time
from typing import Optional

from .base import MediaError, MediaBackendUnavailable, sha3_hex, validate_magic
from . import prompt_spec

# Tier -> default open model. Free tier gets NO image generation. Env overrides win.
# VRAM/disk footprints (approx): sd-turbo ~2.5GB, sdxl-turbo ~7GB, FLUX.1-schnell ~33GB.
_TIER_IMAGE_MODELS = {
    "standard": "stabilityai/sd-turbo",
    "premium": "stabilityai/sdxl-turbo",
    "elite": "black-forest-labs/FLUX.1-schnell",
}
# Turbo/schnell models are distilled: guidance 0.0, very few steps.
_TURBO_FRAGMENTS = ("turbo", "schnell", "lightning", "lcm", "hyper-sd", "hyper_sd")

_PIPELINE_CACHE: dict[str, object] = {}
# Remembers the load strategy that actually fit this box's VRAM per model, so a GPU that
# can't hold the model doesn't re-OOM (and waste a load) on every single job.
_LOAD_STRATEGY: dict[str, str] = {}

PRECISIONS = ("fast", "balanced", "high")
MAX_CANDIDATES = 8


# ── Model family knowledge ──────────────────────────────────────────────────

def model_profile(model_id: str) -> dict:
    """Rendering regime for a model id: native resolution, step/guidance defaults, whether
    it honors a negative prompt (CFG), and which text-encoder family it uses."""
    lower = model_id.lower()
    turbo = any(f in lower for f in _TURBO_FRAGMENTS)
    prof = {"native": 512, "steps": 28, "guidance": 7.0, "cfg": True, "encoder": "clip", "turbo": turbo, "align": 64}
    if "flux" in lower:
        prof.update(native=1024, encoder="t5", align=16)
        if "schnell" in lower or turbo:
            prof.update(steps=4, guidance=0.0, cfg=False)
        else:
            prof.update(steps=28, guidance=3.5, cfg=False)  # FLUX.1-dev: guidance-distilled, no negatives
    elif "stable-diffusion-3" in lower or "sd3" in lower:
        prof.update(native=1024, steps=28, guidance=7.0, cfg=True, encoder="t5")
    elif "pixart" in lower:
        prof.update(native=1024, steps=20, guidance=4.5, cfg=True, encoder="t5")
    elif "sdxl" in lower or "stable-diffusion-xl" in lower or "playground" in lower or "ssd-1b" in lower:
        prof.update(native=512 if "sdxl-turbo" in lower else 1024, encoder="clip2")
        if turbo:
            prof.update(steps=4, guidance=0.0, cfg=False)
        else:
            prof.update(steps=30, guidance=6.5, cfg=True)
    elif "sd-turbo" in lower:
        prof.update(native=512, steps=4, guidance=0.0, cfg=False)
    elif "stable-diffusion-2" in lower:
        prof.update(native=512 if "base" in lower else 768)
    if turbo and "flux" not in lower and "sdxl" not in lower and "sd-turbo" not in lower:
        # lcm / lightning / hyper variants of SD1.5 or SDXL
        if "lcm" in lower:
            prof.update(steps=6, guidance=1.5, cfg=False)
        else:
            prof.update(steps=4, guidance=0.0, cfg=False)
    return prof


def _is_oom(e: Exception) -> bool:
    """True for a CUDA out-of-memory failure (torch raises either type by version)."""
    if type(e).__name__ == "OutOfMemoryError":
        return True
    return "out of memory" in str(e).lower()


def _strategy_order(model_id: str, cuda: bool) -> list[str]:
    """Escalation order, most-capable → most-frugal. Honors what last worked on this box.

    - ``cuda``          : whole pipeline resident on the GPU (fastest, most VRAM).
    - ``cuda_offload``  : sequential CPU offload — streams weights module-by-module, runs
                          sd-turbo in a few hundred MB of VRAM (slower, tiny footprint).
    - ``cpu``           : no GPU at all — slow but always completes.
    """
    if not cuda:
        return ["cpu"]
    remembered = _LOAD_STRATEGY.get(model_id)
    if remembered == "cpu":
        return ["cpu"]                       # known not to fit — don't thrash the GPU
    if remembered == "cuda_offload":
        return ["cuda_offload", "cpu"]
    return ["cuda", "cuda_offload", "cpu"]


def resolve_image_model(tier: str | None) -> str:
    """Model id for a tier, honoring env overrides. Raises for tiers with no image capability."""
    env = os.environ.get("ANIMICA_IMAGE_MODEL")
    if env:
        return env
    t = (tier or "standard").strip().lower()
    tier_env = os.environ.get(f"ANIMICA_IMAGE_MODEL_{t.upper()}")
    if tier_env:
        return tier_env
    if t in _TIER_IMAGE_MODELS:
        return _TIER_IMAGE_MODELS[t]
    if t == "free":
        raise MediaError("image generation is not available on the free tier")
    # Unknown tier: fall back to the standard model rather than a chat coercion.
    return _TIER_IMAGE_MODELS["standard"]


def _resolve_adapter_dir(adapter: str):
    """Turn an adapter id (registered checkpoint) or a filesystem path into a LoRA weights dir."""
    from pathlib import Path
    p = Path(adapter)
    if p.is_dir():
        return p
    from . import checkpoints
    d = checkpoints.path_for(adapter)
    if not d:
        raise MediaError(f"unknown trained adapter: {adapter}")
    return d


def _install_scheduler(pipe, prof: dict) -> str:
    """DPM++ 2M Karras for CFG models: converges to a faithful image in ~28 steps where the
    default PNDM/Euler needs 50. Turbo/flow-matching schedulers are left as shipped (they are
    part of the distillation recipe)."""
    name = type(getattr(pipe, "scheduler", None)).__name__ or "default"
    if prof["turbo"] or not prof["cfg"] or name.startswith("FlowMatch"):
        return name
    if os.environ.get("ANIMICA_IMAGE_SCHEDULER", "dpmpp").lower() in ("0", "off", "default"):
        return name
    try:
        from diffusers import DPMSolverMultistepScheduler
        pipe.scheduler = DPMSolverMultistepScheduler.from_config(
            pipe.scheduler.config, use_karras_sigmas=True, algorithm_type="dpmsolver++"
        )
        return "DPM++ 2M Karras"
    except Exception:
        return name


def _load_pipeline(model_id: str, adapter: str | None = None, strategy: str = "cuda"):
    try:
        import torch
        from diffusers import AutoPipelineForText2Image
    except Exception as e:  # pragma: no cover - env dependent
        raise MediaBackendUnavailable(f"diffusers/torch not installed: {e}") from e

    base = model_id if not adapter else f"{model_id}::{adapter}"
    key = f"{base}::{strategy}"
    if key in _PIPELINE_CACHE:
        return _PIPELINE_CACHE[key]

    # Free any VRAM a previous (failed or other-model) pipeline left pinned before we try again.
    if strategy != "cpu":
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass

    dtype = torch.float16 if strategy != "cpu" else torch.float32
    try:
        pipe = AutoPipelineForText2Image.from_pretrained(
            model_id, torch_dtype=dtype, low_cpu_mem_usage=True
        )
        # Silence NSFW-checker None-image surprises by disabling it where present; we do our own
        # magic-byte validation and the marketplace runs its own content policy.
        if hasattr(pipe, "safety_checker"):
            pipe.safety_checker = None
        try:
            pipe.set_progress_bar_config(disable=True)
        except Exception:
            pass
        # Serve a trained LoRA checkpoint on top of the base model.
        if adapter:
            adir = _resolve_adapter_dir(adapter)
            try:
                pipe.load_lora_weights(str(adir))
            except Exception as e:
                raise MediaError(f"failed to load adapter {adapter!r}: {e}") from e
        _install_scheduler(pipe, model_profile(model_id))
        # Cheap memory-frugal knobs — keep tight / shared GPUs from OOMing during generation.
        for fn in ("enable_attention_slicing", "enable_vae_slicing", "enable_vae_tiling"):
            try:
                getattr(pipe, fn)()
            except Exception:
                pass
        if strategy == "cuda":
            pipe = pipe.to("cuda")
        elif strategy == "cuda_offload":
            # Stream weights CPU<->GPU module-by-module: runs sd-turbo in a few hundred MB VRAM.
            try:
                pipe.enable_sequential_cpu_offload()
            except Exception:
                pipe = pipe.to("cuda")  # offload unsupported → plain cuda (may still OOM upstream)
        else:  # cpu
            pipe = pipe.to("cpu")
    except MediaError:
        raise
    except Exception as e:
        raise MediaError(f"failed to load image model {model_id!r} [{strategy}]: {e}") from e

    _PIPELINE_CACHE[key] = pipe
    return pipe


def _reclaim_all_vram() -> None:
    """Evict every cached pipeline and reclaim VRAM. Called on OOM so a heavy model pinned by an
    earlier job (sdxl-turbo ~7GB, FLUX ~33GB — the cache never evicts on its own) can't keep a
    smaller job from fitting on the next, more frugal attempt."""
    _PIPELINE_CACHE.clear()
    # Symmetric with video_gen._reclaim_all_vram: a resident SVD/t2v pipeline is
    # just as capable of squatting the VRAM an image load needs.
    try:
        from . import video_gen as _vg
        _vg._PIPELINE_CACHE.clear()
    except Exception:
        pass
    try:
        from . import image_fidelity as _fid
        _fid.unload_scorer()
    except Exception:
        pass
    try:
        import gc
        import torch
        gc.collect()
        torch.cuda.empty_cache()
    except Exception:
        pass


# ── Resolution buckets ──────────────────────────────────────────────────────

def native_bucket(width: int, height: int, native: int, align: int = 64) -> tuple[int, int]:
    """The size to RENDER at for a requested (width, height): the requested aspect ratio at
    the model's native pixel area, aligned. A request already inside the native regime
    (0.75-1.25× per axis, aligned) is rendered as-is so nothing gets resampled."""
    width, height = int(width), int(height)
    lo, hi = native * 0.75, native * 1.25
    if lo <= width <= hi and lo <= height <= hi and width % align == 0 and height % align == 0:
        return width, height
    area = float(native * native)
    ar = width / float(height)
    bw = int(round(math.sqrt(area * ar) / align)) * align
    bh = int(round(math.sqrt(area / ar) / align)) * align
    floor_px = align * 4
    ceil_px = native * 2
    bw = max(floor_px, min(ceil_px, bw))
    bh = max(floor_px, min(ceil_px, bh))
    return bw, bh


# ── Long prompts (chunked CLIP embeddings) ──────────────────────────────────

def _encoder_kind(pipe) -> str:
    """'clip' (SD1/2), 'clip2' (SDXL), 't5' (FLUX/SD3/PixArt — long prompts are native)."""
    if getattr(pipe, "text_encoder_3", None) is not None:
        return "t5"
    enc2 = getattr(pipe, "text_encoder_2", None)
    if enc2 is not None:
        if "T5" in type(enc2).__name__:
            return "t5"
        return "clip2"
    enc = getattr(pipe, "text_encoder", None)
    if enc is not None and "T5" in type(enc).__name__:
        return "t5"
    return "clip"


def _token_count(pipe, text: str) -> int:
    tok = getattr(pipe, "tokenizer", None)
    if tok is None:
        return len(text.split())
    try:
        return len(tok(text, truncation=False, add_special_tokens=False).input_ids)
    except Exception:
        return len(text.split())


def chunk_token_ids(ids: list[int], bos: int, eos: int, pad: int, n_chunks: int, chunk: int = 75, width: int = 77) -> list[list[int]]:
    """Split raw token ids into `n_chunks` rows of `width`, each BOS + ≤chunk ids + EOS + pad.
    Pure function (unit-tested without a model)."""
    rows: list[list[int]] = []
    for i in range(n_chunks):
        seg = list(ids[i * chunk:(i + 1) * chunk])
        row = [bos] + seg + [eos]
        row = row + [pad] * (width - len(row))
        rows.append(row[:width])
    return rows


def _chunks_needed(n_tokens: int, chunk: int = 75) -> int:
    return max(1, int(math.ceil(n_tokens / float(chunk))))


def _long_prompt_embeds(pipe, prompt: str, negative: str, *, need_negative: bool, dtype):
    """Encode prompts longer than CLIP's window by concatenating 75-token chunks.

    Returns a kwargs dict for the pipeline call, or None when the prompt fits (the normal
    path is then used). Supports single-CLIP (SD1/2, sd-turbo) and dual-CLIP (SDXL).
    """
    import torch
    kind = _encoder_kind(pipe)
    if kind not in ("clip", "clip2"):
        return None
    tok = pipe.tokenizer
    enc = pipe.text_encoder
    p_ids = tok(prompt, truncation=False, add_special_tokens=False).input_ids
    n_ids = tok(negative or "", truncation=False, add_special_tokens=False).input_ids if need_negative else []
    if len(p_ids) <= 75 and len(n_ids) <= 75:
        return None
    n_chunks = max(_chunks_needed(len(p_ids)), _chunks_needed(len(n_ids)) if need_negative else 1)
    device = getattr(pipe, "_execution_device", None) or getattr(enc, "device", None) or "cpu"
    pad = tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id

    def rows_for(ids: list[int], tk) -> "torch.Tensor":
        pd = tk.pad_token_id if tk.pad_token_id is not None else tk.eos_token_id
        return torch.tensor(chunk_token_ids(ids, tk.bos_token_id, tk.eos_token_id, pd, n_chunks), device=device)

    with torch.no_grad():
        if kind == "clip":
            def encode(ids):
                out = enc(rows_for(ids, tok))[0]                       # (n, 77, hid)
                return out.reshape(1, -1, out.shape[-1]).to(dtype)     # (1, n*77, hid)
            kw = {"prompt_embeds": encode(p_ids)}
            if need_negative:
                kw["negative_prompt_embeds"] = encode(n_ids)
            return kw
        # SDXL: hidden_states[-2] of both encoders concatenated, pooled from encoder 2.
        tok2, enc2 = pipe.tokenizer_2, pipe.text_encoder_2
        _ = pad

        def encode2(text: str, ids1: list[int]):
            ids2 = tok2(text, truncation=False, add_special_tokens=False).input_ids
            r1 = rows_for(ids1, tok)
            r2 = rows_for(ids2, tok2)
            o1 = enc(r1, output_hidden_states=True)
            o2 = enc2(r2, output_hidden_states=True)
            h1 = o1.hidden_states[-2]
            h2 = o2.hidden_states[-2]
            pooled = o2[0][0:1]                                        # first chunk's text_embeds
            h = torch.cat([h1, h2], dim=-1)                             # (n, 77, 2048)
            return h.reshape(1, -1, h.shape[-1]).to(dtype), pooled.to(dtype)
        pe, pp = encode2(prompt, p_ids)
        kw = {"prompt_embeds": pe, "pooled_prompt_embeds": pp}
        if need_negative:
            ne, np_ = encode2(negative or "", n_ids)
            kw["negative_prompt_embeds"] = ne
            kw["negative_pooled_prompt_embeds"] = np_
        return kw


# ── Candidates / precision ──────────────────────────────────────────────────

def default_candidates(precision: str, device: str, turbo: bool) -> int:
    """How many seeds to render before the fidelity judge picks one. Env override wins.
    Turbo models are cheap (4 steps), so they get more draws; a CPU box gets fewer."""
    if precision == "fast":
        return 1
    env = os.environ.get("ANIMICA_IMAGE_CANDIDATES", "").strip()
    if env.isdigit():
        return max(1, min(MAX_CANDIDATES, int(env)))
    if device == "cuda":
        table = {"balanced": 4 if turbo else 2, "high": 8 if turbo else 4}
    else:
        table = {"balanced": 2 if turbo else 1, "high": 3 if turbo else 2}
    return table.get(precision, 1)


def _time_budget_s(device: str) -> float:
    env = os.environ.get("ANIMICA_IMAGE_TIME_BUDGET_S", "").strip()
    if env:
        try:
            return max(10.0, float(env))
        except ValueError:
            pass
    return 150.0 if device == "cuda" else 420.0


def _png_with_recipe(image, recipe: dict) -> bytes:
    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    # A1111-compatible "parameters" chunk: every common viewer/reader understands it.
    params = (
        f"{recipe.get('prompt','')}\n"
        f"Negative prompt: {recipe.get('negative_prompt','')}\n"
        f"Steps: {recipe.get('steps')}, Sampler: {recipe.get('scheduler')}, CFG scale: {recipe.get('guidance')}, "
        f"Seed: {recipe.get('seed')}, Size: {recipe.get('width')}x{recipe.get('height')}, Model: {recipe.get('model')}"
    )
    info.add_text("parameters", params)
    info.add_text("animica", json.dumps(recipe, separators=(",", ":"), default=str))
    buf = io.BytesIO()
    image.save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


def _refine_to_size(pipe, image, target: tuple[int, int], *, prompt_kwargs: dict, steps_hint: int,
                    guidance: float, generator, turbo: bool):
    """img2img 'hires fix': upscale the winner to the exact requested size and let the same
    model re-denoise lightly so the upscaled pixels get real detail instead of Lanczos blur.
    Fail-open: any error → the caller falls back to a plain resize."""
    from PIL import Image
    from diffusers import AutoPipelineForImage2Image
    i2i = AutoPipelineForImage2Image.from_pipe(pipe)
    try:
        i2i.set_progress_bar_config(disable=True)
    except Exception:
        pass
    up = image.resize(target, Image.LANCZOS)
    strength = 0.4 if turbo else 0.35
    # diffusers runs int(steps * strength) denoising steps: keep ≥2 for turbo, ~8 for CFG.
    steps = max(5, int(math.ceil(2 / strength))) if turbo else max(steps_hint, int(math.ceil(8 / strength)))
    kw = dict(prompt_kwargs)
    kw.update(image=up, strength=strength, num_inference_steps=steps, guidance_scale=guidance)
    if generator is not None:
        kw["generator"] = generator
    import torch
    with torch.no_grad():
        out = i2i(**kw)
    return out.images[0]


def generate_image(
    prompt: str,
    *,
    tier: str = "standard",
    width: int = 512,
    height: int = 512,
    steps: Optional[int] = None,
    guidance: Optional[float] = None,
    seed: Optional[int] = None,
    negative_prompt: Optional[str] = None,
    model: Optional[str] = None,
    adapter: Optional[str] = None,
    candidates: Optional[int] = None,
    precision: str = "balanced",
    refine: Optional[bool] = None,
    references: Optional[list] = None,
    learner=None,
) -> dict:
    """Generate one image. Returns {"bytes": PNG, "mime": "image/png", "model", "sha3", "width",
    "height", "steps", "seed", "guidance", "scheduler", "candidates", "fidelity", ...}.

    ``candidates`` seeds are rendered (default by ``precision`` and device) and the CLIP
    fidelity judge keeps the best. ``adapter`` optionally serves a trained LoRA checkpoint.
    Raises MediaError / MediaBackendUnavailable on any failure — the caller must fail closed.
    """
    if not prompt or not prompt.strip():
        raise MediaError("empty prompt")

    model_id = model or resolve_image_model(tier)
    prof = model_profile(model_id)
    precision = (precision or "balanced").strip().lower()
    if precision not in PRECISIONS:
        precision = "balanced"

    import torch

    # 1. Compile the request (idempotent — the gateway usually did this already).
    compiled = prompt_spec.compile_image_prompt(prompt, negative_prompt)
    final_prompt = compiled.prompt or prompt.strip()
    if prof["cfg"]:
        final_negative = prompt_spec.quality_negative(compiled.negative, compiled.spec)
    else:
        final_negative = compiled.negative  # recorded, enforced by the reranker only

    # 2. Step / guidance regime.
    is_turbo = prof["turbo"]
    if steps:
        n_steps = int(steps)
        n_steps = max(1, min(n_steps, 8)) if is_turbo else max(10, min(n_steps, 60))
    else:
        n_steps = int(prof["steps"])
        if precision == "high" and not is_turbo:
            n_steps = min(60, n_steps + 10)
    g = float(guidance) if guidance is not None else float(prof["guidance"])
    if is_turbo and guidance is None:
        g = float(prof["guidance"])

    # 3. Sizes: exact output size (/8 for the VAE) and the native render bucket.
    width = max(64, min(int(width), 1536)) // 8 * 8
    height = max(64, min(int(height), 1536)) // 8 * 8
    if os.environ.get("ANIMICA_IMAGE_NATIVE_BUCKETS", "1") in ("0", "off", "false"):
        rw, rh = width // prof["align"] * prof["align"] or width, height // prof["align"] * prof["align"] or height
    else:
        rw, rh = native_bucket(width, height, prof["native"], prof["align"])

    cuda = torch.cuda.is_available()
    device_hint = "cuda" if cuda else "cpu"
    n_cand = int(candidates) if candidates else default_candidates(precision, device_hint, is_turbo)
    n_cand = max(1, min(MAX_CANDIDATES, n_cand))
    if seed is None and learner is not None and precision != "fast":
        # Self-teaching: an identical compiled prompt that already produced a high-scoring
        # draw starts from that seed (the other candidates still explore).
        try:
            hint = learner.best_seed_hint(final_prompt)
        except Exception:
            hint = None
        seed = hint
    base_seed = int(seed) if seed is not None else int.from_bytes(os.urandom(4), "big")
    budget = _time_budget_s(device_hint)
    # Reference photos of the subject (gateway web lookup, or remembered from last time):
    # the judge also rewards looking like the real thing.
    ref_urls = [u for u in (references or []) if isinstance(u, str)]
    if not ref_urls and learner is not None:
        try:
            ref_urls = learner.references_for(final_prompt)
        except Exception:
            ref_urls = []

    order = _strategy_order(model_id, cuda)
    images: list = []
    seeds: list[int] = []
    last_err: Exception | None = None
    used_strat = order[-1]
    long_mode = "native"
    pipe = None
    prompt_kwargs: dict = {}
    t0 = time.monotonic()

    for strat in order:
        try:
            pipe = _load_pipeline(model_id, adapter=adapter, strategy=strat)
            gen_device = "cpu" if strat == "cpu" else "cuda"
            dtype = torch.float32 if strat == "cpu" else torch.float16
            # Prompt kwargs: chunked embeddings when the prompt exceeds CLIP's window.
            prompt_kwargs = {"prompt": final_prompt}
            if prof["cfg"] and final_negative:
                prompt_kwargs["negative_prompt"] = final_negative
            if prof["encoder"] == "t5" or _encoder_kind(pipe) == "t5":
                long_mode = "native"
                if "flux" in model_id.lower():
                    prompt_kwargs["max_sequence_length"] = 512
            else:
                ntok = _token_count(pipe, final_prompt)
                if ntok > 75:
                    try:
                        emb = _long_prompt_embeds(pipe, final_prompt, final_negative,
                                                  need_negative=bool(prof["cfg"]) and g > 1.0, dtype=dtype)
                    except Exception as e:
                        emb = None
                        long_mode = f"truncated ({type(e).__name__})"
                    if emb:
                        prompt_kwargs = emb
                        long_mode = "chunked"
                    elif long_mode == "native":
                        long_mode = "truncated"
            images, seeds = [], []
            for i in range(n_cand):
                s = (base_seed + i) % (2 ** 32)
                generator = torch.Generator(device=gen_device).manual_seed(s)
                kwargs = dict(prompt_kwargs)
                kwargs.update(num_inference_steps=n_steps, guidance_scale=g, width=rw, height=rh, generator=generator)
                with torch.no_grad():
                    result = pipe(**kwargs)
                img = result.images[0]
                images.append(img)
                seeds.append(s)
                elapsed = time.monotonic() - t0
                # Time budget: stop drawing more candidates when the next one would overrun.
                if i + 1 < n_cand and elapsed / (i + 1) * (i + 2) > budget:
                    break
            _LOAD_STRATEGY[model_id] = strat  # this fit — reuse it next time
            used_strat = strat
            break
        except MediaBackendUnavailable:
            raise
        except Exception as e:
            last_err = e
            if _is_oom(e) and strat != order[-1]:
                _reclaim_all_vram()  # free VRAM, then try a frugal strategy
                continue
            raise MediaError(f"image generation failed [{strat}]: {e}") from e

    if not images:
        if last_err is not None:
            raise MediaError(f"image generation failed: {last_err}") from last_err
        raise MediaError("model returned no image (possibly NSFW filter)")

    # 4. Fidelity judge: keep the candidate that matches the prompt (and its constraints)
    #    best and did not draw a negated concept. Fail-open to candidate 0.
    best = 0
    fidelity_meta: dict = {"rerank": "single" if len(images) == 1 else "skipped"}
    if len(images) > 1:
        from . import image_fidelity
        if image_fidelity.rerank_enabled():
            try:
                refs = image_fidelity.fetch_reference_images(ref_urls) if ref_urls else []
                if refs and learner is not None and references:
                    try:
                        learner.remember_references(final_prompt, ref_urls)
                    except Exception:
                        pass
                rep = image_fidelity.score_candidates(
                    images, final_prompt, views=compiled.constraint_views(), negated=compiled.spec.negated,
                    references=refs,
                )
                best = rep.best
                fidelity_meta = {"rerank": "clip", **rep.to_meta()}
            except Exception as e:  # FidelityUnavailable or anything else: never fail the job
                fidelity_meta = {"rerank": f"unavailable: {str(e)[:160]}"}
        else:
            fidelity_meta = {"rerank": "disabled"}
    image = images[best]
    chosen_seed = seeds[best]

    # 5. Bring the winner to the EXACT requested size. Refine (img2img) when we are
    #    upscaling meaningfully; otherwise a plain high-quality resample.
    refined = False
    if (image.width, image.height) != (width, height):
        do_refine = refine if refine is not None else (
            os.environ.get("ANIMICA_IMAGE_REFINE", "1") not in ("0", "off", "false")
            and precision != "fast"
            and (used_strat != "cpu" or precision == "high")
        )
        scale = max(width / float(image.width), height / float(image.height))
        if do_refine and scale > 1.15 and pipe is not None:
            try:
                generator = torch.Generator(device="cpu" if used_strat == "cpu" else "cuda").manual_seed(chosen_seed)
                image = _refine_to_size(pipe, image, (width, height), prompt_kwargs=prompt_kwargs,
                                        steps_hint=n_steps, guidance=g, generator=generator, turbo=is_turbo)
                refined = True
            except Exception:
                refined = False
        if (image.width, image.height) != (width, height):
            from PIL import Image
            image = image.resize((width, height), Image.LANCZOS)

    scheduler = type(getattr(pipe, "scheduler", None)).__name__ if pipe is not None else "default"
    if scheduler == "DPMSolverMultistepScheduler":
        scheduler = "DPM++ 2M Karras"
    recipe = {
        "prompt": final_prompt,
        "negative_prompt": final_negative,
        "steps": n_steps,
        "guidance": g,
        "scheduler": scheduler,
        "seed": chosen_seed,
        "width": width,
        "height": height,
        "model": model_id,
        "render_size": f"{rw}x{rh}",
        "refined": refined,
        "long_prompt": long_mode,
        "precision": precision,
        "candidates": len(images),
        "candidate_seeds": seeds,
        "prompt_raw": prompt if prompt != final_prompt else None,
        "spec": compiled.spec.__dict__,
        "notes": compiled.notes,
        "version": "animica-image-fidelity/1",
    }
    recipe.update({k: v for k, v in fidelity_meta.items() if k in ("rerank", "scorer", "scores", "best", "fidelity", "refs_used")})
    data = _png_with_recipe(image, recipe)

    # Fail-closed self-check: the bytes MUST be a real PNG.
    if not validate_magic(data, "png"):
        raise MediaError("generated output is not a valid PNG")

    out = {
        "bytes": data,
        "mime": "image/png",
        "model": model_id,
        "sha3": sha3_hex(data),
        "width": width,
        "height": height,
        "steps": n_steps,
        "seed": chosen_seed,
        "guidance": g,
        "scheduler": scheduler,
        "device": used_strat,
        "candidates": len(images),
        "render_size": f"{rw}x{rh}",
        "refined": refined,
        "long_prompt": long_mode,
        "precision": precision,
        "prompt": final_prompt,
        "negative_prompt": final_negative,
        "notes": compiled.notes,
    }
    out.update(fidelity_meta)
    if learner is not None:
        try:
            learner.record_image(prompt, out)
        except Exception:
            pass
    return out
