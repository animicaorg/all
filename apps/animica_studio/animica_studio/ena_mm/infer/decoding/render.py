from __future__ import annotations

from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None


def _require_pillow() -> None:
    if Image is None:
        raise RuntimeError("Pillow is required for image/video rendering. Install: pip install pillow")


def save_png(image, path: str) -> str:
    _require_pillow()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    image.save(p)
    return str(p)


def save_mp4_placeholder(frames: list, path: str) -> str:
    _require_pillow()
    # Keep local-first + dependency-light: store a GIF bytes under mp4 filename as tiny placeholder artifact.
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if frames:
        frames[0].save(p, save_all=True, append_images=frames[1:], duration=80, loop=0)
    return str(p)
