"""Media model training — LoRA fine-tuning of the image diffusion model.

A miner can train the base image model further on its own data (or on feedback-curated data from
animica.dev), producing a small LoRA adapter that is then registered + distributed via
``animica.media.checkpoints`` so other miners can download and serve it.

Fail-closed: produces a real adapter directory or raises. GPU-gated: real training needs CUDA;
CPU is allowed only for a tiny smoke run (ANIMICA_MEDIA_ALLOW_CPU_TRAIN=1) since it is impractically
slow otherwise. This mirrors image/video generation — built here, runs on GPU miner rigs.

Dataset: a directory of images (*.png/*.jpg/*.webp) each with a caption in a sibling ``<stem>.txt``,
or a ``captions.jsonl`` of {"file": ..., "caption": ...}. Images are resized to ``resolution``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .base import MediaError, MediaBackendUnavailable


def _load_pairs(dataset_dir: Path, resolution: int):
    """Yield (PIL image, caption) pairs from the dataset dir. Raises if none found."""
    from PIL import Image
    pairs: list[tuple[str, str]] = []
    jsonl = dataset_dir / "captions.jsonl"
    if jsonl.is_file():
        for line in jsonl.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            f = rec.get("file")
            cap = rec.get("caption") or ""
            if f:
                pairs.append((str(dataset_dir / f), cap))
    else:
        for img in sorted(dataset_dir.rglob("*")):
            if img.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                cap = img.with_suffix(".txt")
                pairs.append((str(img), cap.read_text().strip() if cap.is_file() else img.stem.replace("_", " ")))
    if not pairs:
        raise MediaError(f"no image/caption pairs found in {dataset_dir}")
    out = []
    for path, cap in pairs:
        try:
            im = Image.open(path).convert("RGB").resize((resolution, resolution))
            out.append((im, cap))
        except Exception:
            continue
    if not out:
        raise MediaError("no readable images in dataset")
    return out


def train_image_lora(
    dataset_dir: str,
    *,
    base_model: Optional[str] = None,
    tier: str = "standard",
    output_dir: Optional[str] = None,
    steps: int = 400,
    lr: float = 1e-4,
    rank: int = 8,
    resolution: int = 512,
    batch_size: int = 1,
    seed: int = 0,
) -> dict:
    """LoRA-fine-tune the tier's image model on a dataset. Returns a registered checkpoint record.

    Raises MediaBackendUnavailable (missing deps) / MediaError (bad data, CPU-gated) — fail closed.
    """
    from .image_gen import resolve_image_model
    model_id = base_model or resolve_image_model(tier)

    try:
        import torch
        import torch.nn.functional as F
        from diffusers import AutoPipelineForText2Image
        from peft import LoraConfig, get_peft_model_state_dict
    except Exception as e:  # pragma: no cover - env dependent
        raise MediaBackendUnavailable(f"training needs diffusers+peft+torch: {e}") from e

    cuda = torch.cuda.is_available()
    if not cuda and os.environ.get("ANIMICA_MEDIA_ALLOW_CPU_TRAIN") != "1":
        raise MediaError("LoRA training requires a CUDA GPU (set ANIMICA_MEDIA_ALLOW_CPU_TRAIN=1 for a tiny smoke run)")
    device = "cuda" if cuda else "cpu"
    dtype = torch.float32  # train in fp32 for stability

    dataset_dir_p = Path(dataset_dir)
    if not dataset_dir_p.is_dir():
        raise MediaError(f"dataset dir not found: {dataset_dir}")
    pairs = _load_pairs(dataset_dir_p, resolution)

    try:
        pipe = AutoPipelineForText2Image.from_pretrained(model_id, torch_dtype=dtype)
    except Exception as e:
        raise MediaError(f"failed to load base model {model_id!r}: {e}") from e

    unet, vae, text_encoder = pipe.unet, pipe.vae, pipe.text_encoder
    tokenizer, scheduler = pipe.tokenizer, pipe.scheduler
    for m in (unet, vae, text_encoder):
        m.to(device)
        m.requires_grad_(False)

    # Attach LoRA to the UNet attention projections and train ONLY those.
    lora = LoraConfig(r=rank, lora_alpha=rank,
                      target_modules=["to_q", "to_k", "to_v", "to_out.0"])
    unet.add_adapter(lora)
    params = [p for p in unet.parameters() if p.requires_grad]
    if not params:
        raise MediaError("no trainable LoRA parameters were created")
    opt = torch.optim.AdamW(params, lr=lr)
    gen = torch.Generator(device=device).manual_seed(int(seed))

    unet.train()
    n = len(pairs)
    losses: list[float] = []
    for step in range(int(steps)):
        img, caption = pairs[step % n]
        import numpy as np
        arr = (np.asarray(img).astype("float32") / 127.5) - 1.0
        px = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).to(device, dtype)
        with torch.no_grad():
            latents = vae.encode(px).latent_dist.sample(generator=gen) * vae.config.scaling_factor
            tok = tokenizer(caption, padding="max_length", truncation=True,
                            max_length=tokenizer.model_max_length, return_tensors="pt").to(device)
            enc = text_encoder(tok.input_ids)[0]
        noise = torch.randn(latents.shape, generator=gen, device=device, dtype=dtype)
        ts = torch.randint(0, scheduler.config.num_train_timesteps, (latents.shape[0],), device=device).long()
        noisy = scheduler.add_noise(latents, noise, ts)
        pred = unet(noisy, ts, encoder_hidden_states=enc).sample
        target = noise if scheduler.config.prediction_type == "epsilon" else scheduler.get_velocity(latents, noise, ts)
        loss = F.mse_loss(pred.float(), target.float())
        opt.zero_grad()
        loss.backward()
        opt.step()
        losses.append(float(loss.detach().cpu()))

    out = Path(output_dir) if output_dir else (Path(os.environ.get("ANIMICA_MEDIA_CHECKPOINTS",
            str(Path.home() / ".animica" / "media-checkpoints"))) / "_train_out")
    out.mkdir(parents=True, exist_ok=True)
    # Save just the LoRA weights (small, distributable).
    state = get_peft_model_state_dict(unet)
    try:
        from safetensors.torch import save_file
        save_file({k: v.contiguous().cpu() for k, v in state.items()}, str(out / "adapter_model.safetensors"))
    except Exception:
        torch.save(state, out / "adapter_model.bin")
    (out / "adapter_config.json").write_text(json.dumps({
        "base_model": model_id, "r": rank, "lora_alpha": rank,
        "target_modules": ["to_q", "to_k", "to_v", "to_out.0"], "peft_type": "LORA",
    }, indent=2))

    from . import checkpoints
    avg_loss = sum(losses[-20:]) / max(1, len(losses[-20:]))
    rec = checkpoints.register(out, base_model=model_id, tier=tier, kind="image_lora",
                               notes=f"steps={steps} imgs={n} avg_loss={avg_loss:.4f}")
    rec["avg_loss"] = round(avg_loss, 4)
    rec["steps"] = int(steps)
    rec["images"] = n
    return rec
