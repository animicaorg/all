"""Character-actor abstraction: the scene draws whatever actor `make_actor` returns.

- CatActor  → the programmatic Animica mascot (`cat.py`), the default.
- SpriteActor → an end-user character built from an uploaded PNG (`sprite.py`).

Both expose the same `.draw(frame, state, floor_y, base)` seam, so behavior, brain,
audio and the scene are identical regardless of which character is on stage.
"""
from __future__ import annotations

from . import cat as _cat
from .contract import Character, StreamConfig
from .sprite import SpriteActor


class CatActor:
    def __init__(self, cfg: StreamConfig, char: Character):
        self.char = char

    def draw(self, frame, state, floor_y: int, base: int) -> None:
        _cat.draw_cat(frame, state, floor_y, base, self.char)


def make_actor(cfg: StreamConfig, char: Character):
    """Return the right actor for this character, falling back to the cat if an
    uploaded sprite can't be loaded (so a broken PNG never blanks the stream)."""
    if getattr(char, "kind", "cat") == "sprite":
        sa = SpriteActor(cfg, char)
        if sa.ok:
            return sa
    return CatActor(cfg, char)
