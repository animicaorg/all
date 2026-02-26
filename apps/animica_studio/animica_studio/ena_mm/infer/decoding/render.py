from __future__ import annotations

from pathlib import Path
from PIL.Image import Image


def save_png(image: Image, path: str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    image.save(p)
    return str(p)


def save_mp4_placeholder(frames: list[Image], path: str) -> str:
    # Keep local-first + dependency-light: store a GIF bytes under mp4 filename as tiny placeholder artifact.
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if frames:
        frames[0].save(p, save_all=True, append_images=frames[1:], duration=80, loop=0)
    return str(p)
