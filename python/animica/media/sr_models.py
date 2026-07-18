"""Vendored super-resolution archs for the 9.0.0 Video Studio (video_upscale kind).

Real-ESRGAN model family re-implemented on plain torch — NO basicsr / realesrgan
dependency (those pin stale torchvision APIs and drag in opencv). Weights come from
the OFFICIAL xinntao/Real-ESRGAN GitHub releases, are downloaded once into
``~/.animica/media-weights/`` and are sha256-PINNED below, so a tampered or
truncated download can never load (fail-closed, matching the media contract).

Archs (verified key-for-key against the official checkpoints on this box):
  * RRDBNet         — "quality": RealESRGAN_x4plus (scale 4) / RealESRGAN_x2plus
                      (scale 2, pixel-unshuffled input), num_feat 64, num_block 23.
  * SRVGGNetCompact — "fast": realesr-general-x4v3, num_feat 64, num_conv 32, x4.

The official .pth files nest the state dict under ``params_ema`` (RRDB checkpoints)
or ``params`` (SRVGG) — ``_state_dict_of`` handles both.

This module is itself a heavy import (torch at module level, needed to define the
nn.Module classes) — video_studio imports it lazily, inside functions only.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request

import torch
import torch.nn.functional as F
from torch import nn

from .base import MediaError

WEIGHTS_DIR = os.path.expanduser("~/.animica/media-weights")

# name -> official release URL + sha256 pin (computed from the real downloads).
WEIGHT_REGISTRY = {
    "realesrgan-x4plus": {
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
        "sha256": "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1",
        "arch": "rrdb",
        "scale": 4,
    },
    "realesrgan-x2plus": {
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
        "sha256": "49fafd45f8fd7aa8d31ab2a22d14d91b536c34494a5cfe31eb5d89c2fa266abb",
        "arch": "rrdb",
        "scale": 2,
    },
    "realesr-general-x4v3": {
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth",
        "sha256": "8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292",
        "arch": "srvgg",
        "scale": 4,
    },
}


# ── archs ────────────────────────────────────────────────────────────────────
class ResidualDenseBlock(nn.Module):
    """5-conv dense block with local residual (Real-ESRGAN lineage, ESRGAN paper)."""

    def __init__(self, num_feat: int = 64, num_grow_ch: int = 32):
        super().__init__()
        self.conv1 = nn.Conv2d(num_feat, num_grow_ch, 3, 1, 1)
        self.conv2 = nn.Conv2d(num_feat + num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv3 = nn.Conv2d(num_feat + 2 * num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv4 = nn.Conv2d(num_feat + 3 * num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv5 = nn.Conv2d(num_feat + 4 * num_grow_ch, num_feat, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, x):
        x1 = self.lrelu(self.conv1(x))
        x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
        x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
        x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
        x5 = self.conv5(torch.cat((x, x1, x2, x3, x4), 1))
        return x5 * 0.2 + x


class RRDB(nn.Module):
    """Residual-in-residual dense block."""

    def __init__(self, num_feat: int, num_grow_ch: int = 32):
        super().__init__()
        self.rdb1 = ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb2 = ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb3 = ResidualDenseBlock(num_feat, num_grow_ch)

    def forward(self, x):
        out = self.rdb1(x)
        out = self.rdb2(out)
        out = self.rdb3(out)
        return out * 0.2 + x


class RRDBNet(nn.Module):
    """RealESRGAN x4plus / x2plus generator (num_feat 64, num_block 23).

    scale 4 feeds RGB straight in; scale 2 pixel-unshuffles the input 2x first
    (so conv_first sees 12 channels) and the two nearest+conv up-stages restore
    a net 2x — exactly the official checkpoint layout.
    """

    def __init__(self, num_in_ch: int = 3, num_out_ch: int = 3, scale: int = 4,
                 num_feat: int = 64, num_block: int = 23, num_grow_ch: int = 32):
        super().__init__()
        if scale not in (2, 4):
            raise MediaError(f"RRDBNet supports scale 2 or 4, not {scale}")
        self.scale = scale
        in_ch = num_in_ch * 4 if scale == 2 else num_in_ch
        self.conv_first = nn.Conv2d(in_ch, num_feat, 3, 1, 1)
        self.body = nn.Sequential(*[RRDB(num_feat, num_grow_ch) for _ in range(num_block)])
        self.conv_body = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_up1 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_up2 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_hr = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_last = nn.Conv2d(num_feat, num_out_ch, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, x):
        feat = F.pixel_unshuffle(x, downscale_factor=2) if self.scale == 2 else x
        feat = self.conv_first(feat)
        feat = feat + self.conv_body(self.body(feat))
        feat = self.lrelu(self.conv_up1(F.interpolate(feat, scale_factor=2, mode="nearest")))
        feat = self.lrelu(self.conv_up2(F.interpolate(feat, scale_factor=2, mode="nearest")))
        return self.conv_last(self.lrelu(self.conv_hr(feat)))


class SRVGGNetCompact(nn.Module):
    """realesr-general-x4v3: flat conv/PReLU stack + PixelShuffle + nearest skip.

    The official checkpoint stores everything under a single ``body`` ModuleList
    with activations interleaved (conv at even indices, PReLU at odd), final conv
    at index 2 + 2*num_conv — mirrored here so the state dict loads strictly.
    """

    def __init__(self, num_in_ch: int = 3, num_out_ch: int = 3, num_feat: int = 64,
                 num_conv: int = 32, upscale: int = 4):
        super().__init__()
        self.upscale = upscale
        body: list[nn.Module] = [nn.Conv2d(num_in_ch, num_feat, 3, 1, 1), nn.PReLU(num_parameters=num_feat)]
        for _ in range(num_conv):
            body += [nn.Conv2d(num_feat, num_feat, 3, 1, 1), nn.PReLU(num_parameters=num_feat)]
        body.append(nn.Conv2d(num_feat, num_out_ch * upscale * upscale, 3, 1, 1))
        self.body = nn.ModuleList(body)
        self.upsampler = nn.PixelShuffle(upscale)

    def forward(self, x):
        out = x
        for layer in self.body:
            out = layer(out)
        out = self.upsampler(out)
        return out + F.interpolate(x, scale_factor=self.upscale, mode="nearest")


# ── weight download (once, sha256-pinned) ────────────────────────────────────
def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def weight_path(name: str) -> str:
    """Local path of a registry weight file, downloading + verifying it on first use."""
    spec = WEIGHT_REGISTRY.get(name)
    if not spec:
        raise MediaError(f"unknown SR weight: {name!r}")
    os.makedirs(WEIGHTS_DIR, exist_ok=True)
    dest = os.path.join(WEIGHTS_DIR, os.path.basename(spec["url"]))
    if os.path.exists(dest):
        if _sha256_file(dest) == spec["sha256"]:
            return dest
        os.unlink(dest)  # corrupt/tampered cache — re-fetch
    fd, tmp = tempfile.mkstemp(dir=WEIGHTS_DIR, suffix=".part")
    try:
        h = hashlib.sha256()
        req = urllib.request.Request(spec["url"], headers={"User-Agent": "animica-media/9.0"})
        with urllib.request.urlopen(req, timeout=120) as r, os.fdopen(fd, "wb") as f:  # nosec B310
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
                f.write(chunk)
        if h.hexdigest() != spec["sha256"]:
            raise MediaError(f"SR weight {name}: sha256 mismatch (got {h.hexdigest()[:16]}…) — refusing to load")
        os.replace(tmp, dest)
    except MediaError:
        raise
    except Exception as e:
        raise MediaError(f"SR weight download failed for {name}: {e}") from e
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return dest


def _state_dict_of(path: str) -> dict:
    ckpt = torch.load(path, map_location="cpu", weights_only=True)
    if isinstance(ckpt, dict):
        for key in ("params_ema", "params"):
            if key in ckpt and isinstance(ckpt[key], dict):
                return ckpt[key]
    return ckpt


def load_upscaler(name: str, *, device: str = "cpu", dtype=None) -> nn.Module:
    """Build the arch for a registry entry, load its pinned weights strictly, and
    return the model in eval() mode on the requested device/dtype."""
    spec = WEIGHT_REGISTRY.get(name)
    if not spec:
        raise MediaError(f"unknown SR weight: {name!r}")
    path = weight_path(name)
    if spec["arch"] == "rrdb":
        model = RRDBNet(scale=spec["scale"])
    else:
        model = SRVGGNetCompact(num_conv=32, upscale=spec["scale"])
    try:
        model.load_state_dict(_state_dict_of(path), strict=True)
    except Exception as e:
        raise MediaError(f"SR weights {name} do not fit the vendored arch: {e}") from e
    model.eval()
    if dtype is None:
        dtype = torch.float16 if device.startswith("cuda") else torch.float32
    return model.to(device=device, dtype=dtype)


# ── tiled inference (4K frames must never OOM) ───────────────────────────────
def iter_tiles(h: int, w: int, tile: int = 512, overlap: int = 16):
    """Tile plan over an HxW frame: core tiles stepping ``tile``, each padded by
    ``overlap`` (clamped at the borders) for seam-free SR. Yields
    (y0, y1, x0, x1, py0, py1, px0, px1): core bounds + padded input bounds."""
    if h <= 0 or w <= 0:
        raise MediaError(f"bad tile plan dims {h}x{w}")
    tile = max(32, int(tile))
    overlap = max(0, int(overlap))
    out = []
    for y0 in range(0, h, tile):
        y1 = min(y0 + tile, h)
        for x0 in range(0, w, tile):
            x1 = min(x0 + tile, w)
            out.append((y0, y1, x0, x1,
                        max(0, y0 - overlap), min(h, y1 + overlap),
                        max(0, x0 - overlap), min(w, x1 + overlap)))
    return out


def upscale_tiled(model: nn.Module, x: "torch.Tensor", scale: int, *,
                  tile: int = 512, overlap: int = 16) -> "torch.Tensor":
    """Run ``model`` (an SR net upscaling by ``scale``) over a (1,3,H,W) 0..1 float
    tensor in tiles so arbitrarily large frames fit in VRAM. fp16 happens naturally
    on cuda (the model is loaded fp16 there; input is cast to the model's dtype).
    Returns a float32 (1,3,H*scale,W*scale) tensor on CPU, clamped to 0..1.
    CUDA OOM propagates to the caller (video_studio halves the tile and retries)."""
    p = next(model.parameters())
    _, _, h, w = x.shape
    with torch.no_grad():
        if h <= tile and w <= tile:
            out = _forward_padded(model, x.to(device=p.device, dtype=p.dtype), scale)
            return out.float().clamp_(0, 1).cpu()
        canvas = torch.zeros(1, 3, h * scale, w * scale, dtype=torch.float32)
        for (y0, y1, x0, x1, py0, py1, px0, px1) in iter_tiles(h, w, tile, overlap):
            tin = x[:, :, py0:py1, px0:px1].to(device=p.device, dtype=p.dtype)
            tout = _forward_padded(model, tin, scale).float()
            cy0, cx0 = (y0 - py0) * scale, (x0 - px0) * scale
            canvas[:, :, y0 * scale:y1 * scale, x0 * scale:x1 * scale] = \
                tout[:, :, cy0:cy0 + (y1 - y0) * scale, cx0:cx0 + (x1 - x0) * scale].cpu()
        return canvas.clamp_(0, 1)


def _forward_padded(model: nn.Module, x: "torch.Tensor", scale: int) -> "torch.Tensor":
    """Forward with input padded to a multiple of 4 (x2plus pixel-unshuffles, so odd
    tile edges would break it), cropping the output back to the exact size."""
    _, _, h, w = x.shape
    ph, pw = (-h) % 4, (-w) % 4
    if ph or pw:
        x = F.pad(x, (0, pw, 0, ph), mode="replicate")
    out = model(x)
    return out[:, :, :h * scale, :w * scale]
