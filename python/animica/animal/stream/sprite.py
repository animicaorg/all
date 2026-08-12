"""Puppet-animate an end-user's uploaded PNG as the stream character (PNGtuber style).

A single PNG is bobbed, swayed and "talk-bounced" (vertical squash synced to the voice
RMS); if a second mouth-open PNG is supplied it is swapped in while speaking. This lets
any uploaded artwork become an animated character without per-part rigging. The
programmatic cat (`cat.py`) stays the Animica default; this is for end users.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from . import cat as _cat
from .contract import Character, StreamConfig, clamp


def _load_rgba(path: str):
    if not path:
        return None
    try:
        im = Image.open(path).convert("RGBA")
    except Exception:
        return None
    # clamp absurd sizes so per-frame resize stays cheap
    if max(im.size) > 1024:
        im.thumbnail((1024, 1024), Image.LANCZOS)
    return im


class SpriteActor:
    def __init__(self, cfg: StreamConfig, char: Character):
        self.cfg = cfg
        self.char = char
        self.closed = _load_rgba(char.sprite_path)
        self.open = _load_rgba(char.sprite_open_path)
        self.ok = self.closed is not None

    def draw(self, frame: Image.Image, state, floor_y: int, base: int) -> None:
        if not self.ok:
            return
        W, H = frame.size
        talking = clamp(state.mouth)
        src = self.open if (self.open is not None and talking > 0.25) else self.closed

        th = base * 1.5 * max(0.4, state.scale)
        squash, hop = 1.0, 0.0
        if self.open is None:                      # no open-mouth art → imply speech with a bounce
            squash = 1.0 + 0.05 * talking
            hop = talking * base * 0.03
        w0, h0 = src.size
        scale = th / h0
        img = src.resize((max(1, int(w0 * scale)), max(1, int(h0 * scale * squash))), Image.LANCZOS)
        if state.facing < 0:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        ang = state.tail * 2.5 + state.lean * 3.0   # gentle idle sway / lean
        if abs(ang) > 0.1:
            img = img.rotate(-ang, resample=Image.BICUBIC, expand=True)

        iw, ih = img.size
        cx = int(state.x * W)
        feet = int(floor_y - state.bob * base * 0.05 - hop)
        px, py = cx - iw // 2, feet - ih

        # soft floor shadow
        sh = Image.new("RGBA", (iw, max(2, int(ih * 0.16))), (0, 0, 0, 0))
        sw = int(iw * 0.4)
        ImageDraw.Draw(sh).ellipse([iw // 2 - sw, 0, iw // 2 + sw, int(ih * 0.12)], fill=(0, 0, 0, 70))
        frame.paste(sh, (px, feet - int(ih * 0.05)), sh)
        frame.paste(img, (px, py), img)

        # transient emote near the head (reuse the cat's prop glyphs)
        if state.prop:
            _cat._draw_prop(frame, state.prop, cx + iw * 0.32, py + ih * 0.06, base * 0.28)
