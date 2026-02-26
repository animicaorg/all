from __future__ import annotations

from PIL import Image, ImageDraw


def generate_video_frames(prompt: str, width: int, height: int, frames: int, seed: int) -> list[Image.Image]:
    out: list[Image.Image] = []
    for i in range(frames):
        img = Image.new("RGB", (width, height), color=((seed + i * 11) % 255, 32, 64))
        d = ImageDraw.Draw(img)
        d.text((4, 4), f"ENA-MM {i}", fill=(255, 255, 255))
        d.text((4, 20), prompt[:30], fill=(220, 220, 220))
        out.append(img)
    return out
