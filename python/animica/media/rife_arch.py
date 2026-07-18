"""Vendored RIFE IFNet (Practical-RIFE 4.13 lineage, MIT) for video_interpolate.

Re-implemented on plain torch against the actual checkpoint published on Hugging
Face as ``imaginairy/rife-interpolation / rife-flownet-4.13.2.safetensors`` (pinned
revision + sha256 below). The state dict was inspected on this box and the arch is
built key-for-key to load it with ``strict=True``:

  * ``encode``   — Head feature extractor: 3 convs (first stride-2) + a
                   ConvTranspose back up → 8-ch per-image features.
  * ``block0..3``— coarse→fine IFBlocks (c = 192/128/96/64) run at scales
                   8/4/2/1. Each: 2 stride-2 convs in, 8 ResConv (beta-scaled
                   residual) blocks, ConvTranspose(c→24) + PixelShuffle(2) out
                   → 6 channels: bidirectional flow (4) + blend mask (1) + 1 spare.
    block0 sees (img0, img1, f0, f1, t) = 23 ch; later blocks see the warped
    images/features + t + mask (24 ch) with the running flow concatenated inside
    the block (28 ch).

Original: https://github.com/hzwer/Practical-RIFE (MIT License, hzwer).
Heavy module (torch at import time) — video_studio imports it lazily.
"""

from __future__ import annotations

import hashlib

import torch
import torch.nn.functional as F
from torch import nn

from .base import MediaError

RIFE_REPO = "imaginairy/rife-interpolation"
RIFE_FILE = "rife-flownet-4.13.2.safetensors"
RIFE_REVISION = "26442e52cc30b88c5cb490702647b8de9aaee8a7"
RIFE_SHA256 = "c5544dfda88abead0f713c3a79338a1eb0ac6a4573e1c5de76d21e2af3839555"

# IFNet downsamples by up to 8 (block0 scale) x 4 (its two stride-2 convs);
# inputs are padded to this multiple so every stage divides cleanly.
_PAD_MULTIPLE = 32


def _conv(in_planes: int, out_planes: int, stride: int = 1) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(in_planes, out_planes, 3, stride, 1),
        nn.LeakyReLU(0.2, inplace=True),
    )


class Head(nn.Module):
    """Per-image feature encoder (checkpoint prefix ``encode.``)."""

    def __init__(self):
        super().__init__()
        self.cnn0 = nn.Conv2d(3, 32, 3, 2, 1)
        self.cnn1 = nn.Conv2d(32, 32, 3, 1, 1)
        self.cnn2 = nn.Conv2d(32, 32, 3, 1, 1)
        self.cnn3 = nn.ConvTranspose2d(32, 8, 4, 2, 1)
        self.relu = nn.LeakyReLU(0.2, inplace=True)

    def forward(self, x):
        x = self.relu(self.cnn0(x))
        x = self.relu(self.cnn1(x))
        x = self.relu(self.cnn2(x))
        return self.cnn3(x)


class ResConv(nn.Module):
    def __init__(self, c: int):
        super().__init__()
        self.conv = nn.Conv2d(c, c, 3, 1, 1)
        self.beta = nn.Parameter(torch.ones((1, c, 1, 1)))
        self.relu = nn.LeakyReLU(0.2, inplace=True)

    def forward(self, x):
        return self.relu(self.conv(x) * self.beta + x)


class IFBlock(nn.Module):
    def __init__(self, in_planes: int, c: int = 64):
        super().__init__()
        self.conv0 = nn.Sequential(_conv(in_planes, c // 2, 2), _conv(c // 2, c, 2))
        self.convblock = nn.Sequential(*[ResConv(c) for _ in range(8)])
        self.lastconv = nn.Sequential(
            nn.ConvTranspose2d(c, 4 * 6, 4, 2, 1),
            nn.PixelShuffle(2),
        )

    def forward(self, x, flow=None, scale: int = 1):
        if scale != 1:
            x = F.interpolate(x, scale_factor=1.0 / scale, mode="bilinear", align_corners=False)
        if flow is not None:
            flow = F.interpolate(flow, scale_factor=1.0 / scale, mode="bilinear",
                                 align_corners=False) * (1.0 / scale)
            x = torch.cat((x, flow), 1)
        feat = self.conv0(x)
        feat = self.convblock(feat)
        tmp = self.lastconv(feat)
        if scale != 1:
            tmp = F.interpolate(tmp, scale_factor=float(scale), mode="bilinear", align_corners=False)
        return tmp[:, :4] * scale, tmp[:, 4:5]  # flow, mask-logits


def _warp(x, flow):
    """Backward-warp ``x`` by a per-pixel flow field (grid_sample, border pad)."""
    n, _, h, w = x.shape
    gx = torch.linspace(-1.0, 1.0, w, device=x.device, dtype=x.dtype).view(1, 1, 1, w).expand(n, -1, h, -1)
    gy = torch.linspace(-1.0, 1.0, h, device=x.device, dtype=x.dtype).view(1, 1, h, 1).expand(n, -1, -1, w)
    grid = torch.cat([gx, gy], 1)
    norm = torch.cat([flow[:, 0:1] / ((w - 1.0) / 2.0), flow[:, 1:2] / ((h - 1.0) / 2.0)], 1)
    g = (grid + norm).permute(0, 2, 3, 1)
    return F.grid_sample(x, g, mode="bilinear", padding_mode="border", align_corners=True)


class IFNet(nn.Module):
    """Flow-based mid-frame synthesis: img0, img1 (N,3,H,W in 0..1) → frame at t."""

    def __init__(self):
        super().__init__()
        self.block0 = IFBlock(7 + 16, c=192)
        self.block1 = IFBlock(8 + 4 + 16, c=128)
        self.block2 = IFBlock(8 + 4 + 16, c=96)
        self.block3 = IFBlock(8 + 4 + 16, c=64)
        self.encode = Head()

    def forward(self, img0, img1, timestep: float = 0.5, scale_list=(8, 4, 2, 1)):
        ts = torch.full_like(img0[:, :1], float(timestep))
        f0 = self.encode(img0)
        f1 = self.encode(img1)
        flow = mask = None
        warped0, warped1 = img0, img1
        for block, scale in zip((self.block0, self.block1, self.block2, self.block3), scale_list):
            if flow is None:
                flow, mask = block(torch.cat((img0, img1, f0, f1, ts), 1), None, scale)
            else:
                wf0 = _warp(f0, flow[:, :2])
                wf1 = _warp(f1, flow[:, 2:4])
                fd, mask = block(torch.cat((warped0, warped1, wf0, wf1, ts, mask), 1), flow, scale)
                flow = flow + fd
            warped0 = _warp(img0, flow[:, :2])
            warped1 = _warp(img1, flow[:, 2:4])
        m = torch.sigmoid(mask)
        return (warped0 * m + warped1 * (1 - m)).clamp_(0, 1)


def load_rife(*, device: str = "cpu", dtype=None) -> IFNet:
    """Download (once, HF-cached) + verify + strictly load the pinned flownet.
    Returns the eval() model on device/dtype. Raises MediaError on any failure —
    callers fall back to ffmpeg minterpolate."""
    try:
        from huggingface_hub import hf_hub_download
        from safetensors.torch import load_file
    except Exception as e:  # pragma: no cover - env dependent
        raise MediaError(f"huggingface_hub/safetensors not installed: {e}") from e
    try:
        path = hf_hub_download(repo_id=RIFE_REPO, filename=RIFE_FILE, revision=RIFE_REVISION)
    except Exception as e:
        raise MediaError(f"RIFE weight download failed: {e}") from e
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != RIFE_SHA256:
        raise MediaError("RIFE weights failed the sha256 pin — refusing to load")
    model = IFNet()
    try:
        model.load_state_dict(load_file(path), strict=True)
    except Exception as e:
        raise MediaError(f"RIFE checkpoint does not fit the vendored arch: {e}") from e
    model.eval()
    if dtype is None:
        dtype = torch.float16 if device.startswith("cuda") else torch.float32
    return model.to(device=device, dtype=dtype)


def interpolate_mid(model: IFNet, frame0, frame1, timestep: float = 0.5):
    """Synthesize the frame at ``timestep`` between two (H,W,3) uint8 RGB frames.
    Handles padding to the net's /32 requirement; returns (H,W,3) uint8."""
    import numpy as np

    if frame0.shape != frame1.shape or frame0.ndim != 3 or frame0.shape[2] != 3:
        raise MediaError("interpolate_mid needs two same-shape (H,W,3) frames")
    p = next(model.parameters())
    h, w = frame0.shape[:2]
    t0 = torch.from_numpy(np.ascontiguousarray(frame0)).permute(2, 0, 1)[None]
    t1 = torch.from_numpy(np.ascontiguousarray(frame1)).permute(2, 0, 1)[None]
    x = torch.cat([t0, t1], 0).to(device=p.device, dtype=p.dtype) / 255.0
    ph, pw = (-h) % _PAD_MULTIPLE, (-w) % _PAD_MULTIPLE
    if ph or pw:
        x = F.pad(x, (0, pw, 0, ph), mode="replicate")
    with torch.no_grad():
        mid = model(x[0:1], x[1:2], timestep=timestep)
    mid = mid[0, :, :h, :w].float()
    if not torch.isfinite(mid).all():
        raise MediaError("RIFE produced non-finite output")
    return (mid * 255.0 + 0.5).clamp_(0, 255).byte().permute(1, 2, 0).cpu().numpy()
