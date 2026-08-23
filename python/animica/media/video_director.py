"""Video director: a prompt → a multi-shot video with scenes, flow and real movement.

11.1.0 replaces "one still with a pan/zoom" with a director pipeline:

1. **Shot plan** (`plan_shots`, deterministic): the brief becomes an ordered shot list —
   explicit scenes / sentences / "then" beats when the user wrote them, otherwise film
   coverage of a single idea (wide establishing → main action → detail close-up) — each
   with a duration, a camera move matched to the content, and a transition.
2. **Engine per shot**, best the box can run, honest in the result meta:
   * ``t2v``      — a real text→video diffusion model (Wan2.1-1.3B by default) renders each
                    shot in its native regime; subjects move, cameras travel.
   * ``keyframe`` — the image-fidelity pipeline renders the shot's keyframe (best-of-N,
                    CLIP-judged, so the composition is RIGHT) and an image→video model
                    (Stable Video Diffusion) animates it.
   * ``parallax`` — runs anywhere, even CPU: the judged keyframe + a monocular depth map
                    → layered 2.5D parallax camera moves (dolly, pan, orbit). Foreground and
                    background move differently, so it reads as real camera motion, not a
                    sliding photograph. Subjects do not animate (no model can do that on a
                    CPU in reasonable time); the meta says so.
3. **Conform**: every shot is brought to the EXACT requested fps / size / duration (retimed
   with motion interpolation, not frame duplication), then shots are joined with the
   planned transitions by ffmpeg. The output is always a real MP4 or an error.

The prompt compiler (`prompt_spec`) is applied to every shot, and each keyframe carries its
recipe, so a video is as reproducible as an image: same plan + seeds ⇒ same video.
"""

from __future__ import annotations

import io
import math
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, List, Optional, Sequence

from .base import MediaError, MediaBackendUnavailable, sha3_hex, validate_magic
from . import prompt_spec

ENGINES = ("t2v", "keyframe", "parallax")
CAMERA_MOVES = ("dolly_in", "dolly_out", "pan_left", "pan_right", "tilt_up", "tilt_down", "orbit_left", "orbit_right", "push_in", "static")

_CAMERA_PHRASE = {
    "dolly_in": "camera slowly dollies in",
    "dolly_out": "camera slowly pulls back",
    "pan_left": "slow cinematic pan to the left",
    "pan_right": "slow cinematic pan to the right",
    "tilt_up": "camera slowly tilts upward",
    "tilt_down": "camera slowly tilts downward",
    "orbit_left": "camera slowly orbits around the subject",
    "orbit_right": "camera slowly orbits around the subject",
    "push_in": "slow push-in toward the subject",
    "static": "steady locked-off camera",
}

_MOTION_VERBS = (
    "walk", "run", "fly", "drive", "swim", "dance", "jump", "fall", "flow", "spin", "rotate", "turn",
    "pour", "ripple", "wave", "blow", "drift", "float", "roll", "ride", "sail", "soar", "glide", "race",
    "explode", "burst", "grow", "bloom", "melt", "rain", "snow", "storm", "crash", "splash", "swirl",
    "orbit", "zoom", "pan", "tracking", "time-lapse", "timelapse", "slow motion", "slow-motion",
)
_SCENE_SPLIT = re.compile(r"\n+|\s*\|\s*|\s*;\s*|(?<=[.!?])\s+(?=[A-Z\"'])|\s+(?:and\s+)?then\s+|\s*,\s*then\s+", re.I)
_SETTING_SPLIT = re.compile(r"\s+(?:in|on|at|inside|through|across|over|under|above|below|against|near|beside|behind|among|within|along|during|beneath)\s+", re.I)


@dataclass
class Shot:
    index: int
    prompt: str            # what to render (compiled, no camera language)
    camera: str            # one of CAMERA_MOVES
    seconds: float
    transition: str = "fade"
    role: str = "main"     # wide | main | detail | user
    seed: Optional[int] = None
    # filled in after rendering
    engine: Optional[str] = None
    model: Optional[str] = None
    fidelity: Optional[float] = None
    candidates: Optional[int] = None
    notes: list[str] = field(default_factory=list)

    def t2v_prompt(self) -> str:
        return f"{self.prompt}. {_CAMERA_PHRASE.get(self.camera, '')}, smooth natural motion, cinematic, high detail".replace("..", ".")


def _has_motion(text: str) -> bool:
    t = text.lower()
    return any(v in t for v in _MOTION_VERBS)


def _subject_setting(prompt: str) -> tuple[str, str]:
    """'a cat walking through a neon city at night' → ('a cat walking', 'a neon city at night')."""
    first = re.split(r"[,;]", prompt, 1)[0].strip()
    parts = _SETTING_SPLIT.split(first, 1)
    subject = parts[0].strip()
    setting = parts[1].strip() if len(parts) > 1 else ""
    if len(subject.split()) > 9:  # long clause: keep the head
        subject = " ".join(subject.split()[:9])
    return subject, setting


def _style_tail(prompt: str) -> str:
    spec = prompt_spec.compile_image_prompt(prompt).spec
    return ", ".join(spec.style[:3])


def camera_for(role: str, index: int, text: str, seconds: float) -> str:
    """A camera move that suits the shot. Wide shots pan, main shots travel with the
    action, details push in; alternates direction so consecutive shots don't repeat."""
    t = text.lower()
    if any(w in t for w in ("static", "locked", "still camera", "no camera movement")):
        return "static"
    if "orbit" in t or "around" in t:
        return "orbit_left" if index % 2 == 0 else "orbit_right"
    if "tilt up" in t or "looking up" in t or "tower" in t or "skyscraper" in t:
        return "tilt_up"
    if "tilt down" in t or "looking down" in t or "from above" in t or "aerial" in t:
        return "tilt_down"
    if "pan left" in t:
        return "pan_left"
    if "pan right" in t:
        return "pan_right"
    if "zoom out" in t or "pull back" in t or "reveal" in t:
        return "dolly_out"
    if "zoom in" in t or "close" in t or "push in" in t:
        return "dolly_in"
    if role == "wide":
        return "pan_right" if index % 2 == 0 else "pan_left"
    if role == "detail":
        return "push_in"
    if seconds <= 2.0:
        return "dolly_in"
    return ("dolly_in", "pan_left", "orbit_left", "pan_right")[index % 4]


def plan_shots(prompt: str, seconds: float, *, scenes: Optional[Sequence[str]] = None,
               max_shots: int = 8, min_shot: float = 1.5, max_shot: float = 5.0,
               transition: str = "fade", seed: Optional[int] = None,
               camera_chooser: Optional[Callable[[str, int, str, float], str]] = None) -> List[Shot]:
    """Turn a brief + total duration into an ordered shot list. Deterministic."""
    seconds = max(1.0, min(float(seconds), 60.0))
    chooser = camera_chooser or camera_for
    base_seed = int(seed) if seed is not None else int.from_bytes(os.urandom(4), "big")

    beats: list[tuple[str, str]] = []  # (prompt, role)
    if scenes:
        beats = [(s.strip(), "user") for s in scenes if s and s.strip()][:max_shots]
    else:
        text = (prompt or "").strip()
        parts = [p.strip() for p in _SCENE_SPLIT.split(text) if p and p.strip()]
        parts = [p for p in parts if len(p.split()) >= 2] or [text]
        if len(parts) >= 2:
            beats = [(p, "user") for p in parts[:max_shots]]
        else:
            compiled = prompt_spec.compile_image_prompt(text).prompt or text
            subject, setting = _subject_setting(compiled)
            style = _style_tail(compiled)
            tail = f", {style}" if style else ""
            n_cover = 1 if seconds <= 3.5 else (2 if seconds < 6 else 3)
            if n_cover == 1:
                beats = [(compiled, "main")]
            elif n_cover == 2:
                beats = [(f"wide establishing shot of {setting or subject}{tail}", "wide"), (compiled, "main")]
            else:
                beats = [
                    (f"wide establishing shot of {setting or subject}{tail}", "wide"),
                    (compiled, "main"),
                    (f"close-up of {subject}{(', ' + setting) if setting else ''}{tail}", "detail"),
                ]
    if not beats:
        raise MediaError("empty prompt")

    # Durations: split evenly, then clamp each shot to [min_shot, max_shot]; if the brief is
    # longer than the shots can carry, repeat coverage (main shots get the extra time first).
    n = len(beats)
    per = seconds / n
    if per > max_shot and n < max_shots:
        extra = min(max_shots, int(math.ceil(seconds / max_shot))) - n
        for i in range(extra):
            src = beats[(i * 2 + 1) % n] if n > 1 else beats[0]
            beats.append((src[0], "main"))
        n = len(beats)
        per = seconds / n
    per = max(min_shot, min(per, max_shot))
    durations = [per] * n
    # keep the total honest: trim/extend the last shot within bounds
    total = sum(durations)
    durations[-1] = max(min_shot, min(max_shot, durations[-1] + (seconds - total)))

    shots: list[Shot] = []
    for i, (p, role) in enumerate(beats):
        shots.append(Shot(index=i, prompt=p, camera=chooser(role, i, p, durations[i]), seconds=round(durations[i], 2),
                          transition=transition, role=role, seed=(base_seed + i * 101) % (2 ** 32)))
    return shots


# ── Depth + parallax (CPU engine) ───────────────────────────────────────────

_DEPTH_CACHE: dict = {}
DEFAULT_DEPTH_MODEL = "depth-anything/Depth-Anything-V2-Small-hf"


def estimate_depth(img, *, model_id: Optional[str] = None):
    """(H,W) float32 in [0,1], 1 = nearest. Depth-Anything-V2-small (~100 MB, ~1 s on CPU)
    when available; otherwise a ground-plane prior (bottom = near) so parallax still works."""
    import numpy as np
    w, h = img.size
    mid = model_id or os.environ.get("ANIMICA_DEPTH_MODEL", "").strip() or DEFAULT_DEPTH_MODEL
    if os.environ.get("ANIMICA_DEPTH_MODEL", "").strip().lower() not in ("0", "off", "none"):
        try:
            pipe = _DEPTH_CACHE.get(mid)
            if pipe is None:
                from transformers import pipeline as hf_pipeline
                try:
                    import torch
                    dev = 0 if torch.cuda.is_available() else -1
                except Exception:
                    dev = -1
                pipe = hf_pipeline("depth-estimation", model=mid, device=dev)
                _DEPTH_CACHE[mid] = pipe
            out = pipe(img.convert("RGB"))
            d = out.get("predicted_depth")
            if d is None:
                raise RuntimeError("no predicted_depth")
            try:
                d = d.detach().float().cpu().numpy()
            except Exception:
                d = np.asarray(d, dtype="float32")
            d = np.squeeze(d).astype("float32")
            # Depth-Anything predicts relative INVERSE depth: larger = nearer. DPT-style
            # models do too. Normalize to [0,1] near=1.
            lo, hi = float(np.percentile(d, 1)), float(np.percentile(d, 99))
            d = (d - lo) / max(1e-6, hi - lo)
            d = np.clip(d, 0.0, 1.0)
            from PIL import Image
            dimg = Image.fromarray((d * 255).astype("uint8")).resize((w, h), Image.BILINEAR)
            return np.asarray(dimg).astype("float32") / 255.0, mid
        except Exception:
            pass
    # Ground-plane prior: the bottom of the frame is near, the top is far, softened.
    ys = np.linspace(0.0, 1.0, h, dtype="float32")[:, None]
    d = 0.15 + 0.85 * ys
    return np.repeat(d, w, axis=1), "ground-plane-prior"


def _ease(t: float) -> float:
    return t * t * (3 - 2 * t)  # smoothstep


def _camera_path(camera: str, t: float) -> tuple[float, float, float]:
    """(dx, dy, zoom) at eased time t∈[0,1]: dx/dy in fractions of frame width/height,
    zoom multiplier ≥ 1."""
    e = _ease(t)
    if camera == "dolly_in":
        return 0.0, 0.0, 1.0 + 0.16 * e
    if camera == "push_in":
        return 0.0, -0.01 * e, 1.0 + 0.22 * e
    if camera == "dolly_out":
        return 0.0, 0.0, 1.16 - 0.16 * e
    if camera == "pan_left":
        return -0.05 + 0.10 * (1 - e), 0.0, 1.06
    if camera == "pan_right":
        return -0.05 + 0.10 * e, 0.0, 1.06
    if camera == "tilt_up":
        return 0.0, 0.04 - 0.08 * e, 1.06
    if camera == "tilt_down":
        return 0.0, -0.04 + 0.08 * e, 1.06
    if camera == "orbit_left":
        return 0.06 * math.sin(math.pi * e), 0.015 * (1 - math.cos(math.pi * e)), 1.06 + 0.06 * e
    if camera == "orbit_right":
        return -0.06 * math.sin(math.pi * e), 0.015 * (1 - math.cos(math.pi * e)), 1.06 + 0.06 * e
    return 0.0, 0.0, 1.04  # static: tiny zoom so it is still "alive"


def _inpaint_background(base, d, near_from: float = 0.45):
    """Background plate with the foreground REMOVED: pixels nearer than `near_from` are
    replaced by a normalized-blur fill from the surrounding far pixels (growing radii so
    large holes close too). Without this, the plate behind a moving foreground shows a
    ghost copy of the object that just moved away."""
    import numpy as np
    from PIL import Image, ImageFilter
    rgb = np.asarray(base).astype("float32")
    far = (d < near_from).astype("float32")
    if far.mean() > 0.995 or far.mean() < 0.02:
        return base.filter(ImageFilter.GaussianBlur(2))
    fill = rgb * far[..., None]
    weight = far.copy()
    out = rgb.copy()
    W = base.width
    for radius in (max(2, W // 80), max(4, W // 30), max(8, W // 12), max(16, W // 5)):
        fimg = Image.fromarray(np.clip(fill, 0, 255).astype("uint8")).filter(ImageFilter.GaussianBlur(radius))
        wimg = Image.fromarray(np.clip(weight * 255, 0, 255).astype("uint8")).filter(ImageFilter.GaussianBlur(radius))
        fb = np.asarray(fimg).astype("float32")
        wb = np.asarray(wimg).astype("float32") / 255.0
        ok = wb > 0.02
        est = np.where(ok[..., None], fb / np.maximum(wb, 1e-3)[..., None], out)
        hole = (weight < 0.5) & ok
        out = np.where(hole[..., None], est, out)
        # freshly filled pixels become sources for the next (larger) radius
        weight = np.where(hole, 1.0, weight)
        fill = out * weight[..., None]
    return Image.fromarray(np.clip(out, 0, 255).astype("uint8")).filter(ImageFilter.GaussianBlur(1.5))


def parallax_frames(img, depth, *, n_frames: int, camera: str, width: int, height: int,
                    layers: int = 6, strength: float = 0.045, feather_frac: float = 0.012) -> list:
    """Synthesize `n_frames` (H,W,3) uint8 frames of a 2.5D camera move over `img` using
    `depth` (1 = near). Layers at different depths translate/scale by different amounts —
    foreground slides across background — which is what makes it read as a camera in a
    space rather than a photo on a slider. Holes behind the foreground are covered by a
    softened, slightly enlarged background plate."""
    import numpy as np
    from PIL import Image, ImageFilter

    # Work frame: cover-fit the image to the target aspect at the target size.
    from .video_gen import _fit_cover
    base = _fit_cover(img.convert("RGB"), width, height)
    dimg = Image.fromarray((np.clip(depth, 0, 1) * 255).astype("uint8")).resize(img.size, Image.BILINEAR)
    dimg = _fit_cover(dimg, width, height)
    d = np.asarray(dimg).astype("float32") / 255.0
    W, H = width, height
    cx, cy = W / 2.0, H / 2.0
    feather = max(2, int(W * feather_frac))

    # Precompute layer RGBA plates (feathered depth bands), far → near. Each plate's RGB
    # has everything NEARER than the layer replaced by the inpainted background, so the
    # feathered alpha edge never bleeds foreground pixels into the layer behind (halos).
    inpainted = _inpaint_background(base, d)
    inp_arr = np.asarray(inpainted)
    base_arr = np.asarray(base)
    plates = []
    edges = np.linspace(0.0, 1.0, layers + 1)
    for k in range(layers):
        lo, hi = edges[k], edges[k + 1]
        m = ((d >= lo) & (d < hi if k < layers - 1 else d <= hi)).astype("uint8") * 255
        mask = Image.fromarray(m).filter(ImageFilter.GaussianBlur(feather))
        if k < layers - 1:
            rgb = np.where((d < hi)[..., None], base_arr, inp_arr)
            plate = Image.fromarray(rgb.astype("uint8"))
        else:
            plate = base.copy()
        plate.putalpha(mask)
        depth_k = (lo + hi) / 2.0
        plates.append((plate, depth_k))
    backplate = inpainted.resize((int(W * 1.12) // 2 * 2, int(H * 1.12) // 2 * 2), Image.BICUBIC)

    def affine(im, scale: float, tx: float, ty: float, size=(W, H)):
        a = 1.0 / scale
        c = cx - (cx + tx) * a
        f = cy - (cy + ty) * a
        return im.transform(size, Image.AFFINE, (a, 0.0, c, 0.0, a, f), resample=Image.BICUBIC)

    frames = []
    for i in range(max(1, n_frames)):
        t = i / float(max(1, n_frames - 1))
        dx, dy, zoom = _camera_path(camera, t)
        # Background plate: moves like the farthest layer (opposite to the foreground).
        bg_shift_x = -dx * W * strength * 6.0 * 0.5
        bg_shift_y = -dy * H * strength * 6.0 * 0.5
        canvas = Image.new("RGB", (W, H))
        bw, bh = backplate.size
        canvas.paste(backplate, (int(round((W - bw) / 2 + bg_shift_x)), int(round((H - bh) / 2 + bg_shift_y))))
        for plate, depth_k in plates:
            par = (depth_k - 0.5) * 2.0                 # -1 (far) … +1 (near)
            tx = dx * W * strength * 6.0 * par
            ty = dy * H * strength * 6.0 * par
            s = zoom * (1.0 + 0.035 * par * (zoom - 1.0) * 10.0)
            s = max(0.85, s)
            moved = affine(plate, s, tx, ty)
            canvas.paste(moved, (0, 0), moved)
        # Global framing zoom hides any residual border from the largest shifts.
        crop = 1.0 / (1.0 + strength * 1.5)
        cw, ch = int(W * crop) // 2 * 2, int(H * crop) // 2 * 2
        canvas = canvas.crop(((W - cw) // 2, (H - ch) // 2, (W - cw) // 2 + cw, (H - ch) // 2 + ch)).resize((W, H), Image.LANCZOS)
        frames.append(np.asarray(canvas))
    return frames


# ── ffmpeg helpers ──────────────────────────────────────────────────────────

def _ffmpeg() -> str:
    from .base import resolve_ffmpeg
    exe = resolve_ffmpeg()
    if not exe:
        raise MediaBackendUnavailable("ffmpeg not installed — required for video assembly")
    return exe


def _run(cmd: Sequence[str], *, timeout: float, what: str) -> None:
    try:
        proc = subprocess.run(list(cmd), capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise MediaError(f"{what} timed out after {timeout:.0f}s") from e
    if proc.returncode != 0:
        raise MediaError(f"{what} failed: {proc.stderr.decode(errors='replace')[-400:]}")


def conform_clip(src_path: str, dst_path: str, *, width: int, height: int, fps: int, seconds: float,
                 src_fps: float, src_seconds: float, retime: Optional[str] = None, timeout: float = 420.0) -> dict:
    """Bring a rendered clip to the exact (width, height, fps, seconds). Shorter sources are
    slowed (setpts) up to 1.6× so motion stays continuous instead of freezing on the last
    frame; longer ones are trimmed. Frame-rate changes use motion-compensated interpolation
    (minterpolate) when the ratio is meaningful, else a plain fps resample."""
    exe = _ffmpeg()
    retime = (retime or os.environ.get("ANIMICA_VIDEO_RETIME", "minterpolate")).lower()
    stretch = 1.0
    if src_seconds > 0 and src_seconds < seconds:
        stretch = min(1.6, seconds / src_seconds)
    vf = [f"scale={width}:{height}:force_original_aspect_ratio=increase", f"crop={width}:{height}", "setsar=1"]
    if abs(stretch - 1.0) > 0.01:
        vf.append(f"setpts={stretch:.4f}*PTS")
    ratio = fps / max(1e-6, src_fps / stretch)
    if retime == "minterpolate" and ratio >= 1.4:
        vf.append(f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1")
    else:
        vf.append(f"fps={fps}")
    vf.append("format=yuv420p")
    cmd = [exe, "-y", "-hide_banner", "-loglevel", "error", "-i", src_path,
           "-vf", ",".join(vf), "-t", f"{seconds:.3f}", "-r", str(fps),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-an", dst_path]
    try:
        _run(cmd, timeout=timeout, what="clip conform")
    except MediaError:
        if "minterpolate" not in ",".join(vf):
            raise
        # minterpolate can be slow/brittle on exotic clips: fall back to a plain resample.
        vf = [v for v in vf if not v.startswith("minterpolate")] + [f"fps={fps}"]
        cmd[cmd.index("-vf") + 1] = ",".join(vf)
        _run(cmd, timeout=timeout, what="clip conform (fps)")
    return {"stretch": round(stretch, 3), "retime": "minterpolate" if ratio >= 1.4 and retime == "minterpolate" else "fps"}


def write_frames_mp4(frames, path: str, fps: int) -> None:
    from .video_gen import encode_mp4
    with open(path, "wb") as f:
        f.write(encode_mp4(frames, fps=fps))


def assemble_clips(paths: Sequence[str], out_path: str, *, fps: int, durations: Sequence[float],
                   transitions: Sequence[str], transition_secs: float = 0.5, timeout: float = 600.0) -> float:
    """Join conformed clips with xfade transitions. Returns the total duration."""
    exe = _ffmpeg()
    n = len(paths)
    if n == 1:
        import shutil
        shutil.copyfile(paths[0], out_path)
        return float(durations[0])
    from .scene_video import _TRANSITIONS
    T = max(0.0, min(float(transition_secs), min(durations) / 2.0 - 0.05))
    inputs: list[str] = []
    for p in paths:
        inputs += ["-i", p]
    chains = [f"[{i}:v]settb=1/{fps},fps={fps},format=yuv420p[v{i}]" for i in range(n)]
    xf = []
    acc = float(durations[0])
    prev = "v0"
    for i in range(1, n):
        tr = transitions[i] if i < len(transitions) and transitions[i] in _TRANSITIONS else "fade"
        if tr == "cut":
            tr, Ti = "fade", 0.04
        else:
            Ti = T
        out = f"x{i}"
        xf.append(f"[{prev}][v{i}]xfade=transition={tr}:duration={Ti:.3f}:offset={max(0.0, acc - Ti):.3f}[{out}]")
        acc = acc + float(durations[i]) - Ti
        prev = out
    cmd = [exe, "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", ";".join(chains + xf),
           "-map", f"[{prev}]", "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
    _run(cmd, timeout=timeout, what="shot assembly")
    return acc


# ── Engine selection ────────────────────────────────────────────────────────

def _cuda_vram_gb() -> float:
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_properties(0).total_memory / 2 ** 30
    except Exception:
        pass
    return 0.0


def select_engine(requested: Optional[str] = None, *, tier: Optional[str] = None) -> str:
    """Best engine this box can run, unless the job/operator pinned one."""
    env = os.environ.get("ANIMICA_VIDEO_ENGINE", "").strip().lower()
    for cand in (requested, env):
        if cand and cand in ENGINES:
            if cand == "parallax":
                return cand
            if _cuda_vram_gb() > 0:
                return cand
    vram = _cuda_vram_gb()
    if vram <= 0:
        return "parallax"
    try:
        from .video_gen import resolve_video_model, video_model_profile
        t2v_need = video_model_profile(resolve_video_model("video_t2v", tier))["vram_gb"]
        i2v_need = video_model_profile(resolve_video_model("video_i2v", tier))["vram_gb"]
    except Exception:
        t2v_need, i2v_need = 8.0, 12.0
    if vram >= t2v_need and os.environ.get("ANIMICA_MEDIA_VIDEO_ENABLED", "1") != "0":
        return "t2v"
    if vram >= i2v_need and os.environ.get("ANIMICA_MEDIA_I2V_MODEL_ENABLED", "1") != "0":
        return "keyframe"
    return "parallax"


# ── Orchestrator ────────────────────────────────────────────────────────────

def _keyframe(shot: Shot, *, width: int, height: int, tier: str, precision: str, negative: Optional[str], learner=None):
    from . import image_gen
    out = image_gen.generate_image(shot.prompt, tier=tier, width=width, height=height, seed=shot.seed,
                                   negative_prompt=negative, precision=precision)
    from PIL import Image
    img = Image.open(io.BytesIO(out["bytes"])).convert("RGB")
    shot.fidelity = out.get("fidelity")
    shot.candidates = out.get("candidates")
    shot.model = out.get("model")
    return img, out


def render_video(prompt: str, *, seconds: float = 4.0, fps: int = 24, width: int = 768, height: int = 432,
                 tier: str = "standard", precision: str = "balanced", seed: Optional[int] = None,
                 negative_prompt: Optional[str] = None, scenes: Optional[Sequence[str]] = None,
                 transition: str = "fade", engine: Optional[str] = None, progress: Optional[Callable] = None,
                 stills: Optional[Sequence] = None, learner=None) -> dict:
    """Prompt → MP4 bytes + meta. `stills` (PIL images) turns the director into image→video:
    one shot per still, animated by the best engine. Fail-closed."""
    t0 = time.monotonic()
    fps = max(6, min(int(fps), 60))
    width = max(64, min(int(width), 1920)) // 2 * 2
    height = max(64, min(int(height), 1080)) // 2 * 2
    seconds = max(1.0, min(float(seconds), 60.0))
    precision = precision if precision in ("fast", "balanced", "high") else "balanced"
    eng = select_engine(engine, tier=tier)
    chooser = getattr(learner, "choose_camera", None) if learner is not None else None

    if stills:
        shots = plan_shots(prompt or "uploaded image", seconds, scenes=[f"uploaded image {i + 1}" for i in range(len(stills))],
                           transition=transition, seed=seed, camera_chooser=chooser)
    else:
        shots = plan_shots(prompt, seconds, scenes=scenes, transition=transition, seed=seed, camera_chooser=chooser)

    def _p(pct: float, note: str):
        if progress:
            try:
                progress(pct, note)
            except Exception:
                pass

    models_used: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="anmdir_") as td:
        clip_paths: list[str] = []
        for i, shot in enumerate(shots):
            _p(5 + 80 * i / len(shots), f"shot {i + 1}/{len(shots)} · {shot.role} · {shot.camera} · {eng}")
            raw = os.path.join(td, f"raw{i:02d}.mp4")
            dst = os.path.join(td, f"shot{i:02d}.mp4")
            shot_engine = eng
            still = stills[i] if stills and i < len(stills) else None
            try:
                if shot_engine == "t2v" and still is None:
                    from . import video_gen
                    sp = prompt_spec.compile_image_prompt(shot.prompt, negative_prompt)
                    prof_neg = None  # model default negative (Wan's list) + user's
                    if sp.negative:
                        prof_neg = sp.negative + ", " + video_gen.video_model_profile(
                            video_gen.resolve_video_model("video_t2v", tier))["negative"]
                    out = video_gen.generate_text_to_video(shot.t2v_prompt(), tier=tier, seconds=shot.seconds,
                                                           seed=shot.seed, negative_prompt=prof_neg)
                    with open(raw, "wb") as f:
                        f.write(out["bytes"])
                    src_fps, src_secs = float(out["fps"]), out["frames"] / float(out["fps"])
                    shot.model = out["model"]
                    conform_clip(raw, dst, width=width, height=height, fps=fps, seconds=shot.seconds,
                                 src_fps=src_fps, src_seconds=src_secs)
                elif shot_engine in ("t2v", "keyframe"):
                    from . import video_gen
                    if still is None:
                        img, _ = _keyframe(shot, width=width, height=height, tier=tier, precision=precision,
                                           negative=negative_prompt, learner=learner)
                    else:
                        img = still.convert("RGB")
                    out = video_gen.generate_image_to_video(img, tier=tier, seed=shot.seed, prompt=shot.t2v_prompt(),
                                                            motion=None)
                    with open(raw, "wb") as f:
                        f.write(out["bytes"])
                    src_fps, src_secs = float(out["fps"]), out["frames"] / float(out["fps"])
                    shot.model = (shot.model + " + " if shot.model else "") + out["model"]
                    shot_engine = "keyframe"
                    conform_clip(raw, dst, width=width, height=height, fps=fps, seconds=shot.seconds,
                                 src_fps=src_fps, src_seconds=src_secs)
                else:
                    raise MediaError("parallax")
            except MediaBackendUnavailable:
                raise
            except Exception as e:
                if shot_engine == "parallax" and str(e) != "parallax":
                    raise
                # Generative engine failed (OOM, model) or parallax requested: 2.5D engine.
                if shot_engine != "parallax":
                    shot.notes.append(f"{shot_engine} unavailable ({str(e)[:80]}) → parallax")
                shot_engine = "parallax"
                if still is None:
                    img, _ = _keyframe(shot, width=width, height=height, tier=tier, precision=precision,
                                       negative=negative_prompt, learner=learner)
                else:
                    img = still.convert("RGB")
                depth, dmodel = estimate_depth(img)
                n_frames = max(2, int(round(shot.seconds * fps)))
                # A real depth map has object edges worth keeping crisp; the ground-plane
                # prior is a smooth gradient, so blend its bands wide to avoid shear seams.
                feather = 0.012 if dmodel != "ground-plane-prior" else 0.05
                frames = parallax_frames(img, depth, n_frames=n_frames, camera=shot.camera, width=width, height=height,
                                         feather_frac=feather)
                write_frames_mp4(frames, dst, fps)
                shot.model = (shot.model + " + " if shot.model else "") + f"depth:{dmodel}"
            shot.engine = shot_engine
            if shot.model:
                models_used.add(shot.model)
            clip_paths.append(dst)
        _p(88, "assembling shots")
        out_path = os.path.join(td, "out.mp4")
        total = assemble_clips(clip_paths, out_path, fps=fps, durations=[s.seconds for s in shots],
                               transitions=[s.transition for s in shots])
        with open(out_path, "rb") as f:
            data = f.read()
    if not validate_magic(data, "mp4"):
        raise MediaError("assembled output is not a valid MP4")
    engines = sorted({s.engine for s in shots if s.engine})
    meta = {
        "engine": engines[0] if len(engines) == 1 else "+".join(engines),
        "shots": [asdict(s) for s in shots],
        "model": " | ".join(sorted(models_used)) if models_used else None,
        "fps": fps, "width": width, "height": height, "duration_s": round(total, 2),
        "seconds_requested": seconds, "precision": precision,
        "subject_motion": "generative" if all(s.engine in ("t2v", "keyframe") for s in shots) else "camera-only (2.5D parallax)",
        "render_s": round(time.monotonic() - t0, 1),
        "version": "animica-video-director/1",
    }
    if learner is not None:
        try:
            learner.record_video(prompt, meta)
        except Exception:
            pass
    return {"bytes": data, "mime": "video/mp4", "sha3": sha3_hex(data), "meta": meta,
            "frames": int(round(total * fps)), "fps": fps, "duration_s": round(total, 2),
            "model": meta["model"] or "anm-video-director"}


# ── Distributed mode (11.1.0): one shot per miner, one assembler ───────────

def _shot_from_dict(d: dict) -> Shot:
    return Shot(index=int(d.get("index", 0)), prompt=str(d.get("prompt") or ""), camera=str(d.get("camera") or "dolly_in"),
                seconds=float(d.get("seconds") or 3.0), transition=str(d.get("transition") or "fade"),
                role=str(d.get("role") or "main"), seed=int(d["seed"]) if d.get("seed") is not None else None)


def render_shot(shot_dict: dict, out_dir: str, *, width: int, height: int, fps: int, tier: str = "standard",
                precision: str = "balanced", negative_prompt: Optional[str] = None, engine: Optional[str] = None,
                references: Optional[list] = None, progress: Optional[Callable] = None, learner=None) -> dict:
    """Render ONE planned shot (from the gateway's plan) to a conformed MP4 file. The shot
    is exactly what `render_video` would produce for that index, so shots rendered by
    different miners join seamlessly in `assemble_shots`."""
    shot = _shot_from_dict(shot_dict)
    fps = max(6, min(int(fps), 60))
    width = max(64, min(int(width), 1920)) // 2 * 2
    height = max(64, min(int(height), 1080)) // 2 * 2
    eng = select_engine(engine, tier=tier)
    os.makedirs(out_dir, exist_ok=True)
    raw = os.path.join(out_dir, "raw.mp4")
    dst = os.path.join(out_dir, f"shot{shot.index:02d}.mp4")

    def _p(pct, note):
        if progress:
            try:
                progress(pct, note)
            except Exception:
                pass

    _p(5, f"shot {shot.index + 1} · {shot.role} · {shot.camera} · {eng}")
    shot_engine = eng
    try:
        if shot_engine == "t2v":
            from . import video_gen
            sp = prompt_spec.compile_image_prompt(shot.prompt, negative_prompt)
            prof_neg = None
            if sp.negative:
                prof_neg = sp.negative + ", " + video_gen.video_model_profile(video_gen.resolve_video_model("video_t2v", tier))["negative"]
            out = video_gen.generate_text_to_video(shot.t2v_prompt(), tier=tier, seconds=shot.seconds, seed=shot.seed, negative_prompt=prof_neg)
            with open(raw, "wb") as f:
                f.write(out["bytes"])
            shot.model = out["model"]
            conform_clip(raw, dst, width=width, height=height, fps=fps, seconds=shot.seconds,
                         src_fps=float(out["fps"]), src_seconds=out["frames"] / float(out["fps"]))
        elif shot_engine == "keyframe":
            from . import video_gen
            img, _ = _keyframe(shot, width=width, height=height, tier=tier, precision=precision, negative=negative_prompt, learner=learner)
            out = video_gen.generate_image_to_video(img, tier=tier, seed=shot.seed, prompt=shot.t2v_prompt())
            with open(raw, "wb") as f:
                f.write(out["bytes"])
            shot.model = (shot.model + " + " if shot.model else "") + out["model"]
            conform_clip(raw, dst, width=width, height=height, fps=fps, seconds=shot.seconds,
                         src_fps=float(out["fps"]), src_seconds=out["frames"] / float(out["fps"]))
        else:
            raise MediaError("parallax")
    except MediaBackendUnavailable:
        raise
    except Exception as e:
        if shot_engine == "parallax" and str(e) != "parallax":
            raise
        if shot_engine != "parallax":
            shot.notes.append(f"{shot_engine} unavailable ({str(e)[:80]}) → parallax")
        shot_engine = "parallax"
        from . import image_gen
        out = image_gen.generate_image(shot.prompt, tier=tier, width=width, height=height, seed=shot.seed,
                                       negative_prompt=negative_prompt, precision=precision, references=references, learner=learner)
        from PIL import Image
        img = Image.open(io.BytesIO(out["bytes"])).convert("RGB")
        shot.fidelity, shot.candidates, shot.model = out.get("fidelity"), out.get("candidates"), out.get("model")
        depth, dmodel = estimate_depth(img)
        _p(60, "depth + parallax camera")
        feather = 0.012 if dmodel != "ground-plane-prior" else 0.05
        frames = parallax_frames(img, depth, n_frames=max(2, int(round(shot.seconds * fps))), camera=shot.camera,
                                 width=width, height=height, feather_frac=feather)
        write_frames_mp4(frames, dst, fps)
        shot.model = (shot.model + " + " if shot.model else "") + f"depth:{dmodel}"
    shot.engine = shot_engine
    try:
        os.remove(raw)
    except OSError:
        pass
    meta = {**asdict(shot), "fps": fps, "width": width, "height": height, "version": "animica-video-director/1"}
    if learner is not None:
        try:
            learner.record_video(shot.prompt, {"shots": [meta]})
        except Exception:
            pass
    from .video_studio import _finalize
    return _finalize(dst, "mp4", "video/mp4", meta)


def assemble_shots(shot_paths: Sequence[str], shots: Sequence[dict], out_dir: str, *, fps: int,
                   transition: str = "fade", shot_metas: Optional[Sequence[dict]] = None,
                   progress: Optional[Callable] = None) -> dict:
    """Join shot clips rendered by OTHER miners (ordered by shot index) into the final MP4.
    Clips are validated before use (real MP4 magic) — they are untrusted inputs."""
    paths = [p for p in (shot_paths or []) if p]
    if not paths or len(paths) != len(shots):
        raise MediaError(f"assemble needs one clip per shot ({len(paths)} clips, {len(shots)} shots)")
    for p in paths:
        with open(p, "rb") as f:
            head = f.read(4096)
        if not validate_magic(head, "mp4"):
            raise MediaError(f"shot clip {os.path.basename(p)} is not a valid MP4")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "final.mp4")
    if progress:
        try:
            progress(20, f"assembling {len(paths)} shots")
        except Exception:
            pass
    durations = [float(s.get("seconds") or 3.0) for s in shots]
    transitions = [str(s.get("transition") or transition) for s in shots]
    total = assemble_clips(paths, out_path, fps=int(fps), durations=durations, transitions=transitions)
    metas = list(shot_metas or [])
    engines = sorted({str(m.get("engine")) for m in metas if m.get("engine")})
    models = sorted({str(m.get("model")) for m in metas if m.get("model")})
    meta = {
        "engine": (engines[0] if len(engines) == 1 else "+".join(engines)) if engines else "distributed",
        "shots": metas or list(shots),
        "model": " | ".join(models) if models else None,
        "fps": int(fps), "duration_s": round(total, 2),
        "subject_motion": "generative" if engines and all(e in ("t2v", "keyframe") for e in engines) else "camera-only (2.5D parallax)",
        "distributed": True, "workers": len(paths),
        "version": "animica-video-director/1",
    }
    from .video_studio import _finalize
    return _finalize(out_path, "mp4", "video/mp4", meta)
