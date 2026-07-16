"""The Animica mascot cat, drawn entirely with PIL primitives so every part is
animatable — mouth openness (lip-sync), eye blink, per-emotion expressions, tail
wag, ear twitch, body lean, and transient emotes (heart / music note / zzz / !).

Nothing is loaded from disk: the cat is pure geometry, so the stream works straight
from `pip install animica` with no art assets. `draw_cat` composites the cat onto a
frame at a normalized stage position; it is the only public entry point.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

from .contract import AnimalState, clamp

# Ginger-cream cat with a teal iris to nod at the Animica brand.
FUR = (245, 176, 106)
FUR_DK = (223, 141, 76)
BELLY = (255, 241, 224)
EAR_IN = (255, 196, 189)
EYE_WHITE = (252, 252, 255)
PUPIL = (34, 42, 66)
IRIS = (60, 206, 200)
NOSE = (240, 132, 142)
MOUTH_IN = (156, 62, 74)
TONGUE = (240, 120, 130)
BLUSH = (255, 150, 138)
OUTLINE = (58, 40, 32)
SHADOW = (0, 0, 0, 70)

SS = 2  # supersample factor for smooth edges


def _mirror_x(pts, cx):
    return [(2 * cx - x, y) for (x, y) in pts]


def _poly(d, pts, fill, outline=OUTLINE, w=0):
    d.polygon(pts, fill=fill, outline=outline if w else None, width=w)


def _emotion_params(emotion: str):
    """Return per-emotion drawing knobs: eye squash, brow, mouth curve, blush, sparkle."""
    e = emotion if emotion in (
        "neutral", "happy", "curious", "surprised", "sleepy", "excited", "sassy", "love") else "neutral"
    table = {
        # (eye_open, pupil_dy, brow, mouth_curve, blush, iris_big)
        "neutral":  (1.00, 0.0, 0.0, 0.15, 0.0, 1.0),
        "happy":    (0.85, 0.0, 0.1, 0.55, 0.5, 1.0),
        "curious":  (1.05, -0.1, 0.25, 0.2, 0.1, 1.1),
        "surprised":(1.20, 0.0, 0.4, 0.0, 0.2, 1.2),
        "sleepy":   (0.45, 0.15, -0.2, 0.05, 0.1, 0.9),
        "excited":  (1.10, -0.05, 0.2, 0.6, 0.6, 1.15),
        "sassy":    (0.80, 0.1, -0.15, -0.25, 0.2, 1.0),
        "love":     (0.75, 0.0, 0.15, 0.5, 0.8, 1.0),
    }
    return table[e]


def draw_cat(frame: Image.Image, state: AnimalState, floor_y: int, base: int, char=None) -> None:
    """Composite the cat onto `frame`. `base` is the cat's nominal height in px;
    `floor_y` is the y of the stage floor line. Position comes from state.x/scale/bob.
    `char` (a Character) supplies the live-editable palette; None uses defaults."""
    # Shadow the module palette with the character's colors when supplied.
    FUR = char.fur if char is not None else globals()["FUR"]
    FUR_DK = char.fur_dk if char is not None else globals()["FUR_DK"]
    BELLY = char.belly if char is not None else globals()["BELLY"]
    IRIS = char.iris if char is not None else globals()["IRIS"]
    W, H = frame.size
    scale = max(0.35, state.scale)
    T = int(base * 1.7 * scale) * SS         # supersampled tile edge
    if T < 8:
        return
    tile = Image.new("RGBA", (T, T), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    cx = T / 2
    lw = max(2, int(T * 0.012))              # outline weight

    facing = -1 if state.facing < 0 else 1
    lean = clamp(state.lean, -1, 1) * T * 0.05
    (eye_open, pupil_dy, brow, mcurve, blush, iris_big) = _emotion_params(state.emotion)

    # ── tail (drawn first, behind body) ── wag with state.tail, sweeps behind-right
    tail_ph = math.sin(state.tail * math.pi)
    tail_base = (cx + T * 0.20, T * 0.78)
    tw = T * 0.075
    seg = []
    for i in range(9):
        f = i / 8.0
        ang = (0.5 + 0.9 * f) * math.pi + tail_ph * 0.5 * f
        r = T * 0.30 * f
        seg.append((tail_base[0] + math.cos(ang) * r * 0.9 + T * 0.12 * f,
                    tail_base[1] - math.sin(ang) * r - T * 0.02 * f))
    for i in range(len(seg) - 1):
        rr = tw * (1 - i / 10.0)
        x0, y0 = seg[i]
        d.line([seg[i], seg[i + 1]], fill=FUR_DK, width=int(rr * 2))
        d.ellipse([x0 - rr, y0 - rr, x0 + rr, y0 + rr], fill=FUR_DK)
    tipx, tipy = seg[-1]
    d.ellipse([tipx - tw * 0.8, tipy - tw * 0.8, tipx + tw * 0.8, tipy + tw * 0.8], fill=BELLY)

    # ── body ── rounded blob, leaning with movement
    bcx, bcy = cx + lean, T * 0.66
    bw, bh = T * 0.29, T * 0.26
    d.ellipse([bcx - bw, bcy - bh, bcx + bw, bcy + bh], fill=FUR, outline=OUTLINE, width=lw)
    # cream belly patch
    d.ellipse([bcx - bw * 0.55, bcy - bh * 0.2, bcx + bw * 0.55, bcy + bh * 0.95], fill=BELLY)
    # hind + front paws
    for sgn in (-1, 1):
        px = bcx + sgn * bw * 0.55
        d.ellipse([px - T * 0.06, T * 0.86, px + T * 0.06, T * 0.94], fill=BELLY, outline=OUTLINE, width=lw)

    # ── head ──
    hcx, hcy = cx + lean * 1.3, T * 0.36
    hr = T * 0.235
    # ears
    ear_tw = clamp(state.ear, -1, 1)
    for sgn in (-1, 1):
        ex = hcx + sgn * hr * 0.72
        ey = hcy - hr * 0.72
        tip = (ex + sgn * hr * 0.22, ey - hr * 0.62 - (ear_tw * hr * 0.12 if sgn > 0 else 0))
        outer = [(ex - hr * 0.34, ey + hr * 0.18), tip, (ex + hr * 0.34, ey + hr * 0.05)]
        _poly(d, outer, FUR, OUTLINE, lw)
        inner = [(ex - hr * 0.16, ey + hr * 0.06), (tip[0], tip[1] + hr * 0.16), (ex + hr * 0.16, ey - hr * 0.01)]
        _poly(d, inner, EAR_IN)
    # face
    d.ellipse([hcx - hr, hcy - hr, hcx + hr, hcy + hr], fill=FUR, outline=OUTLINE, width=lw)
    # cheeks / muzzle
    d.ellipse([hcx - hr * 0.62, hcy + hr * 0.02, hcx + hr * 0.62, hcy + hr * 0.72], fill=BELLY)

    # ── eyes ──
    blink = clamp(state.eye)                       # 1 open .. 0 closed
    e_open = eye_open * blink
    ew, eh = hr * 0.26, hr * 0.34 * e_open
    for sgn in (-1, 1):
        ex = hcx + sgn * hr * 0.42
        ey = hcy - hr * 0.06
        if eh < hr * 0.05:                          # closed → happy arc
            d.arc([ex - ew, ey - ew * 0.6, ex + ew, ey + ew * 0.6], 200, 340, fill=OUTLINE, width=lw)
            continue
        d.ellipse([ex - ew, ey - eh, ex + ew, ey + eh], fill=EYE_WHITE, outline=OUTLINE, width=lw)
        ir = ew * 0.82 * iris_big
        iy = ey + pupil_dy * eh + eh * 0.05
        # look slightly toward facing direction
        ix = ex + facing * ew * 0.16
        d.ellipse([ix - ir, iy - ir, ix + ir, iy + ir], fill=IRIS)
        pr = ir * 0.55
        d.ellipse([ix - pr, iy - pr, ix + pr, iy + pr], fill=PUPIL)
        d.ellipse([ix - pr * 0.4 + ir * 0.3, iy - pr * 0.4 - ir * 0.1,
                   ix + pr * 0.2 + ir * 0.3, iy + pr * 0.1 - ir * 0.1], fill=(255, 255, 255))
        if brow:                                    # eyebrow accent
            by = ey - eh - hr * 0.06 * brow
            d.line([(ex - ew * 0.8, by + hr * 0.03 * brow), (ex + ew * 0.8, by)], fill=OUTLINE, width=lw)

    # ── blush ──
    if blush > 0.05:
        bl = int(120 * blush)
        for sgn in (-1, 1):
            bx = hcx + sgn * hr * 0.66
            ov = Image.new("RGBA", tile.size, (0, 0, 0, 0))
            ImageDraw.Draw(ov).ellipse([bx - hr * 0.2, hcy + hr * 0.18, bx + hr * 0.2, hcy + hr * 0.36],
                                       fill=BLUSH + (bl,))
            tile.alpha_composite(ov)
            d = ImageDraw.Draw(tile)

    # ── nose + mouth (mouth opens with state.mouth for lip-sync) ──
    nx, ny = hcx, hcy + hr * 0.24
    d.polygon([(nx - hr * 0.09, ny), (nx + hr * 0.09, ny), (nx, ny + hr * 0.10)], fill=NOSE, outline=OUTLINE)
    mopen = clamp(state.mouth)
    my = ny + hr * 0.13
    if mopen > 0.06:
        mw = hr * (0.16 + 0.10 * mopen)
        mh = hr * (0.06 + 0.30 * mopen)
        d.ellipse([nx - mw, my, nx + mw, my + mh * 2], fill=MOUTH_IN, outline=OUTLINE, width=lw)
        d.ellipse([nx - mw * 0.7, my + mh * 0.7, nx + mw * 0.7, my + mh * 1.9], fill=TONGUE)
    else:
        # gentle closed smile whose curl follows the emotion
        c = mcurve
        d.arc([nx - hr * 0.20, my - hr * 0.10 - c * hr * 0.1, nx + hr * 0.20, my + hr * 0.14],
              20 if c >= 0 else 200, 160 if c >= 0 else 340, fill=OUTLINE, width=lw)

    # whiskers
    for sgn in (-1, 1):
        for k in range(3):
            wy = my - hr * 0.02 + k * hr * 0.10
            d.line([(nx + sgn * hr * 0.18, my + hr * 0.02),
                    (nx + sgn * hr * 0.72, wy - hr * 0.06)], fill=(255, 255, 255, 200), width=max(1, lw // 2))

    # ── transient emote prop near the head ──
    _draw_prop(tile, state.prop, hcx + hr * 0.9, hcy - hr * 0.9, hr)

    # facing flip
    if facing < 0:
        tile = tile.transpose(Image.FLIP_LEFT_RIGHT)

    # downscale (anti-alias) and composite with a floor shadow
    out = tile.resize((max(1, T // SS), max(1, T // SS)), Image.LANCZOS)
    ow, oh = out.size
    px = int(state.x * W - ow / 2)
    feet = int(floor_y - state.bob * base * 0.05)
    py = int(feet - oh * 0.92)
    # soft shadow
    sh = Image.new("RGBA", (ow, int(oh * 0.16)), (0, 0, 0, 0))
    sw = int(ow * (0.5 - 0.12 * clamp(state.bob, 0, 1)))
    ImageDraw.Draw(sh).ellipse([ow // 2 - sw, 0, ow // 2 + sw, int(oh * 0.14)], fill=SHADOW)
    frame.paste(sh, (px, int(feet - oh * 0.05)), sh)
    frame.paste(out, (px, py), out)


def _draw_prop(tile: Image.Image, prop: str, x: float, y: float, hr: float) -> None:
    if not prop:
        return
    d = ImageDraw.Draw(tile)
    s = hr * 0.5
    if prop == "heart":
        d.ellipse([x - s, y - s * 0.6, x, y + s * 0.1], fill=(255, 90, 120))
        d.ellipse([x, y - s * 0.6, x + s, y + s * 0.1], fill=(255, 90, 120))
        d.polygon([(x - s, y - s * 0.05), (x + s, y - s * 0.05), (x, y + s * 0.7)], fill=(255, 90, 120))
    elif prop == "note":
        d.ellipse([x - s * 0.5, y + s * 0.3, x + s * 0.1, y + s * 0.8], fill=(70, 120, 240))
        d.rectangle([x + s * 0.02, y - s * 0.6, x + s * 0.12, y + s * 0.55], fill=(70, 120, 240))
        d.polygon([(x + s * 0.12, y - s * 0.6), (x + s * 0.5, y - s * 0.45),
                   (x + s * 0.12, y - s * 0.25)], fill=(70, 120, 240))
    elif prop == "zzz":
        for i, ch in enumerate("zZ"):
            d.text((x + i * s * 0.5, y - i * s * 0.4), ch, fill=(150, 170, 210))
    elif prop == "sparkle":
        for a in range(0, 360, 45):
            r = s * 0.7
            d.line([(x, y), (x + math.cos(math.radians(a)) * r, y + math.sin(math.radians(a)) * r)],
                   fill=(255, 224, 120), width=int(hr * 0.05) or 1)
    elif prop in ("!", "?"):
        d.text((x, y - s), prop, fill=(255, 210, 90))
