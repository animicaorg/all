"""Composites one full 720p (or configured) frame: branded background, top HUD,
live-chat panel, subtitle box, now-playing bar and the cat. Fully self-contained
(programmatic drawing + a bundled-font fallback) so it renders on any box.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFont

from .actor import make_actor
from .contract import AnimalState, Character, SceneContext, StreamConfig, clamp

_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans{b}.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans{b2}.ttf",
]
_MONO_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
_font_cache: dict = {}


def _font(size: int, bold: bool = False, mono: bool = False):
    key = (size, bold, mono)
    if key in _font_cache:
        return _font_cache[key]
    f = None
    cands = [_MONO_PATH] if mono else [p.format(b="-Bold" if bold else "", b2="-Bold" if bold else "-Regular")
                                       for p in _FONT_PATHS]
    for p in cands:
        try:
            f = ImageFont.truetype(p, size)
            break
        except Exception:
            continue
    if f is None:
        try:
            f = ImageFont.load_default(size)      # Pillow >= 10: scalable bundled DejaVu
        except TypeError:
            f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _rrect(d, box, r, fill=None, outline=None, w=1):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=w)


def _text(d, xy, s, font, fill, shadow=(0, 0, 0), anchor=None, sw=1):
    if sw:
        d.text((xy[0] + sw, xy[1] + sw), s, font=font, fill=shadow, anchor=anchor)
    d.text(xy, s, font=font, fill=fill, anchor=anchor)


def _eye(d, cx, cy, s, color):
    """A small 'viewers' eye icon centered at (cx, cy)."""
    d.ellipse([cx - s, cy - s * 0.62, cx + s, cy + s * 0.62], outline=color, width=max(1, int(s * 0.16)))
    d.ellipse([cx - s * 0.34, cy - s * 0.34, cx + s * 0.34, cy + s * 0.34], fill=color)


def _paw(d, cx, cy, s, color):
    """A tiny paw glyph centered at (cx, cy) (main pad + four toe beans)."""
    d.ellipse([cx - s * 0.5, cy - s * 0.1, cx + s * 0.5, cy + s * 0.7], fill=color)
    for i, dx in enumerate((-0.55, -0.2, 0.2, 0.55)):
        dy = -0.55 if i in (1, 2) else -0.35
        r = s * 0.24
        d.ellipse([cx + dx * s - r, cy + dy * s - r, cx + dx * s + r, cy + dy * s + r], fill=color)


def _wrap(d, s, font, max_w):
    out, line = [], ""
    for word in s.split():
        trial = (line + " " + word).strip()
        if d.textlength(trial, font=font) <= max_w:
            line = trial
        else:
            if line:
                out.append(line)
            line = word
    if line:
        out.append(line)
    return out


class Scene:
    """Holds the cached background + fonts and renders one frame per call."""

    def __init__(self, cfg: StreamConfig, char: Character):
        self.cfg = cfg
        self.char = char
        W, H = cfg.width, cfg.height
        self.W, self.H = W, H
        self.floor_y = int(H * 0.82)
        self.base = int(H * 0.44)
        # chat panel geometry
        self.panel = (int(W * 0.695), 70, W - int(W * 0.018), int(H * 0.80))
        self.actor = make_actor(cfg, char)
        self.bg = self._background()
        # fonts
        s = H / 720.0
        self.f_title = _font(int(24 * s), bold=True)
        self.f_h = _font(int(17 * s), bold=True)
        self.f_b = _font(int(16 * s))
        self.f_s = _font(int(14 * s))
        self.f_mono = _font(int(14 * s), mono=True)
        self.f_sub = _font(int(23 * s), bold=True)
        self.f_big = _font(int(30 * s), bold=True)

    # stage x-range (normalized) the cat may wander in without hiding behind the chat panel
    def stage_bounds(self):
        return (0.055, (self.panel[0] - self.base * 0.35) / self.W)

    def _background(self) -> Image.Image:
        W, H = self.W, self.H
        acc = self.char.accent
        top = (9, 12, 26)
        horizon = _lerp((17, 22, 46), acc, 0.18)
        img = Image.new("RGB", (W, H), top)
        px = img.load()
        fh = self.floor_y
        for y in range(H):
            if y < fh:
                t = y / max(1, fh)
                col = _lerp(top, horizon, t * t)
            else:
                t = (y - fh) / max(1, H - fh)
                col = _lerp((14, 18, 38), (7, 9, 20), t)
            for x in range(W):
                px[x, y] = col
        d = ImageDraw.Draw(img, "RGBA")
        # accent glow near the top-center
        gy = int(H * 0.02)
        for i, rr in enumerate(range(int(W * 0.42), 0, -max(6, int(W * 0.02)))):
            a = int(46 * (1 - i / 22.0))
            if a <= 0:
                continue
            d.ellipse([W // 2 - rr, gy - rr // 2, W // 2 + rr, gy + rr], fill=acc + (max(0, a),))
        # floor line + soft stage light
        d.line([(0, fh), (W, fh)], fill=(255, 255, 255, 26), width=2)
        sl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sl).ellipse([int(W * 0.16), fh - int(H * 0.16), int(W * 0.60), fh + int(H * 0.10)],
                                   fill=acc + (24,))
        img = Image.alpha_composite(img.convert("RGBA"), sl).convert("RGB")
        # decorative stars/dots
        d = ImageDraw.Draw(img, "RGBA")
        rnd = _PRNG(self.cfg.seed)
        for _ in range(70):
            x = int(rnd.rand() * W)
            y = int(rnd.rand() * fh * 0.9)
            r = rnd.rand() * 1.8 + 0.4
            a = int(60 + rnd.rand() * 120)
            d.ellipse([x - r, y - r, x + r, y + r], fill=(200, 220, 255, a))
        return img

    def render(self, state: AnimalState, ctx: SceneContext, t: float) -> Image.Image:
        img = self.bg.copy()
        d = ImageDraw.Draw(img, "RGBA")
        W, H = self.W, self.H
        acc = self.char.accent

        # floating sparkle particles (ambient life)
        for i in range(10):
            ph = (t * 0.15 + i * 0.37) % 1.0
            x = (i * 131 % W)
            y = int(self.floor_y * (1.0 - ph))
            a = int(120 * math.sin(ph * math.pi))
            if a > 0:
                d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=acc + (a,))

        # the character (cat by default, or an uploaded PNG puppet)
        self.actor.draw(img, state, self.floor_y, self.base)
        d = ImageDraw.Draw(img, "RGBA")

        # quirky-action thought bubble near the cat
        if state.thought:
            self._thought(d, state, ctx)

        self._chat_panel(d, ctx)
        self._top_bar(d, ctx)
        self._net_strip(d, ctx)
        if ctx.subtitle:
            self._subtitle(d, ctx)
        self._now_playing(d, ctx, t)
        return img

    def _top_bar(self, d, ctx):
        W = self.W
        bh = int(self.H * 0.077)
        d.rectangle([0, 0, W, bh], fill=(9, 12, 26, 215))
        d.line([(0, bh), (W, bh)], fill=self.char.accent + (160,), width=2)
        # brand mark
        m = int(bh * 0.5)
        mx, my = int(W * 0.016), bh // 2
        d.ellipse([mx, my - m // 2, mx + m, my + m // 2], fill=self.char.accent)
        _text(d, (mx + m // 2, my), "A", self.f_h, (255, 255, 255), anchor="mm", sw=0)
        _text(d, (mx + m + 10, my), f"{self.char.channel_name if hasattr(self.char,'channel_name') else self.cfg.channel_name}",
              self.f_title, (240, 244, 255), anchor="lm")
        # LIVE pill + viewers + uptime (right → left)
        rx = W - int(W * 0.016)
        up = _fmt_dur(ctx.uptime_s)
        _text(d, (rx, my), up, self.f_b, (150, 165, 200), anchor="rm")
        cur = rx - self.f_b.getlength(up) - 22
        vcount = _fmt_num(ctx.viewers)
        _text(d, (cur, my), vcount, self.f_b, (214, 224, 244), anchor="rm")
        numw = self.f_b.getlength(vcount)
        _eye(d, cur - numw - 12, my, 8, (198, 210, 236))
        cur = cur - numw - 12 - 16 - 12
        if ctx.live:
            pill_w = 66
            _rrect(d, [cur - pill_w, my - 13, cur, my + 13], 13, fill=(230, 60, 70))
            d.ellipse([cur - pill_w + 13, my - 4, cur - pill_w + 21, my + 4], fill=(255, 255, 255))
            _text(d, (cur - pill_w + 27, my), "LIVE", self.f_s, (255, 255, 255), anchor="lm", sw=0)

    def _net_strip(self, d, ctx):
        ns = ctx.net_stats or {}
        chips = []
        if ns.get("height") is not None:
            chips.append(("BLOCK", f"#{_fmt_num(ns['height'])}"))
        if ns.get("price") is not None:
            chips.append(("ANM", f"${ns['price']}"))
        if ns.get("peers") is not None:
            chips.append(("PEERS", f"{ns['peers']}"))
        if ns.get("hashrate"):
            chips.append(("HASH", str(ns["hashrate"])))
        x = int(self.W * 0.016)
        y = int(self.H * 0.077) + 12
        for tag, val in chips:
            label = f"{tag} {val}"
            w = self.f_s.getlength(tag) + self.f_s.getlength(" " + val) + 20
            _rrect(d, [x, y, x + w, y + 26], 13, fill=(20, 26, 44, 200), outline=(40, 52, 82, 255), w=1)
            _text(d, (x + 10, y + 13), tag, self.f_s, (120, 134, 168), anchor="lm", sw=0)
            _text(d, (x + 10 + self.f_s.getlength(tag + " "), y + 13), val, self.f_s, (214, 224, 246), anchor="lm", sw=0)
            x += w + 8

    def _chat_panel(self, d, ctx):
        x0, y0, x1, y1 = self.panel
        _rrect(d, [x0, y0, x1, y1], 16, fill=(13, 18, 34, 210), outline=(38, 50, 82, 255), w=1)
        _text(d, (x0 + 16, y0 + 20), "LIVE CHAT", self.f_h, self.char.accent, anchor="lm", sw=0)
        d.line([(x0 + 14, y0 + 40), (x1 - 14, y0 + 40)], fill=(40, 52, 82, 255), width=1)
        # newest at bottom; wrap + clip to panel
        pad = 14
        maxw = x1 - x0 - pad * 2
        lines = []
        for m in ctx.chat[-30:]:
            author = str(m.get("author", "?"))[:18]
            reply = m.get("reply")
            body = str(m.get("text", ""))
            wrapped = _wrap(d, body, self.f_b, maxw - 10)
            lines.append(("head", author, reply))
            for wl in wrapped:
                lines.append(("body", wl, reply))
            lines.append(("gap", "", reply))
        lh = int(self.H * 0.028)
        avail = (y1 - (y0 + 50)) // lh
        lines = lines[-avail:] if avail > 0 else []
        y = y0 + 50
        for kind, s, reply in lines:
            if kind == "gap":
                y += lh // 2
                continue
            if kind == "head":
                col = (255, 200, 120) if reply else self.char.accent
                if reply:
                    _paw(d, x0 + pad + 5, y, 6, col)
                    _text(d, (x0 + pad + 16, y), self.char.name, self.f_s, col, anchor="lm", sw=0)
                else:
                    _text(d, (x0 + pad, y), s, self.f_s, col, anchor="lm", sw=0)
            else:
                col = (230, 236, 250) if reply else (198, 208, 230)
                _text(d, (x0 + pad + (10 if reply else 0), y), s, self.f_b, col, anchor="lm", sw=0)
            y += lh

    def _subtitle(self, d, ctx):
        maxw = int(self.panel[0] * 0.82)
        lines = _wrap(d, ctx.subtitle, self.f_sub, maxw)[-3:]
        lh = int(self.H * 0.045)
        box_h = lh * len(lines) + 22
        cx = self.panel[0] // 2
        y1 = int(self.H * 0.735)
        y0 = y1 - box_h
        bw = maxw + 40
        _rrect(d, [cx - bw // 2, y0, cx + bw // 2, y1], 16, fill=(10, 12, 24, 225),
               outline=self.char.accent + (220,), w=2)
        y = y0 + 14
        for ln in lines:
            _text(d, (cx, y + lh // 2), ln, self.f_sub, (245, 248, 255), anchor="mm")
            y += lh

    def _thought(self, d, state, ctx):
        x = int(state.x * self.W)
        y = int(self.floor_y - self.base * 1.02)
        s = state.thought[:40]
        w = self.f_s.getlength(s) + 26
        bx = min(max(x, w // 2 + 8), self.panel[0] - w // 2 - 8)
        _rrect(d, [bx - w // 2, y - 30, bx + w // 2, y - 4], 13, fill=(255, 255, 255, 235))
        _text(d, (bx, y - 17), s, self.f_s, (40, 40, 60), anchor="mm", sw=0)
        d.ellipse([bx - 6, y - 2, bx + 6, y + 8], fill=(255, 255, 255, 235))

    def _now_playing(self, d, ctx, t):
        if not ctx.now_playing and self.cfg.music == "off":
            return
        H, W = self.H, self.W
        by = int(H * 0.955)
        d.rectangle([0, by, W, H], fill=(9, 12, 26, 220))
        d.line([(0, by), (W, by)], fill=self.char.accent + (140,), width=1)
        my = (by + H) // 2
        label = f"♪  {ctx.now_playing or 'ambient lofi'}"
        _text(d, (int(W * 0.016), my), label, self.f_b, (210, 220, 240), anchor="lm", sw=0)
        # equalizer
        ex = int(W * 0.016) + self.f_b.getlength(label) + 20
        for i in range(16):
            bh = int((0.3 + 0.7 * abs(math.sin(t * 3 + i * 0.6))) * (H - by) * 0.55)
            d.rectangle([ex + i * 7, my + (H - by) // 4 - bh // 2, ex + i * 7 + 4, my + (H - by) // 4 + bh // 2],
                        fill=self.char.accent + (220,))


class _PRNG:
    """Tiny deterministic PRNG (no Math.random ban issues; reproducible previews)."""
    def __init__(self, seed: int):
        self.s = (seed or 1) & 0xFFFFFFFF

    def rand(self) -> float:
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return self.s / 0x7FFFFFFF


def _fmt_num(n) -> str:
    try:
        n = int(n)
    except Exception:
        return str(n)
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def _fmt_dur(s: float) -> str:
    s = int(s)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h:d}:{m:02d}:{sec:02d}"
