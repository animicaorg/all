"""Advanced multi-scene video assembly for AICF media jobs.

A *scene video* is a single MP4 stitched from N generated scenes. Each scene is either a real
text-to-video clip (on a GPU worker with ANIMICA_MEDIA_VIDEO_ENABLED=1) or a generated still
given cinematic motion (a Ken Burns pan/zoom) — so the feature works on a CPU image worker and
upgrades in quality on a GPU rig. Scenes are joined with crossfade transitions and an optional
generated audio bed.

The compositor uses ffmpeg (no heavy Python deps) and is fail-closed: it returns a valid MP4 or
raises MediaError — never a placeholder. It is CPU-verifiable independently of any model, so the
plumbing can be tested without a GPU.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import List, Optional, Sequence

from .base import MediaError, MediaBackendUnavailable, sha3_hex, validate_magic

_TRANSITIONS = {
    "fade", "fadeblack", "fadewhite", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "smoothleft", "smoothright", "circleopen", "dissolve", "pixelize",
}


def _ffmpeg() -> str:
    from .base import resolve_ffmpeg
    exe = resolve_ffmpeg()
    if not exe:
        raise MediaBackendUnavailable("ffmpeg not installed — required for scene-video assembly")
    return exe


def assemble_scene_video(
    scene_images: Sequence[bytes],
    *,
    width: int = 768,
    height: int = 432,
    fps: int = 24,
    seconds_per_scene: float = 2.5,
    durations: Optional[Sequence[float]] = None,
    transition: str = "fade",
    transition_secs: float = 0.6,
    ken_burns: bool = True,
    audio: Optional[bytes] = None,
    audio_ext: str = "wav",
) -> dict:
    """Stitch a list of scene image bytes (PNG/JPEG) into one MP4.

    durations: optional per-scene seconds; falls back to seconds_per_scene.
    Returns {"bytes": mp4, "mime": "video/mp4", "sha3", "scenes", "fps", "duration_s"}.
    """
    imgs = [b for b in scene_images if b]
    if not imgs:
        raise MediaError("no scenes to assemble")
    exe = _ffmpeg()

    # clamp to sane bounds (this path is also exposed to a free preview endpoint)
    width = max(64, min(int(width), 1920)) // 2 * 2
    height = max(64, min(int(height), 1080)) // 2 * 2
    fps = max(6, min(int(fps), 60))
    n = len(imgs)
    durs = [float(durations[i]) if durations and i < len(durations) else float(seconds_per_scene) for i in range(n)]
    durs = [max(0.6, min(d, 15.0)) for d in durs]
    T = 0.0 if n == 1 else max(0.0, min(float(transition_secs), min(durs) - 0.2))
    trans = transition if transition in _TRANSITIONS else "fade"

    with tempfile.TemporaryDirectory(prefix="anmscene_") as td:
        paths = []
        for i, b in enumerate(imgs):
            p = os.path.join(td, f"s{i:03d}.png")
            with open(p, "wb") as f:
                f.write(b)
            paths.append(p)

        inputs: List[str] = []
        for p in paths:
            # A still is a single-frame input; zoompan (d=N) then emits exactly N
            # frames from it. Do NOT use `-loop 1` — that feeds zoompan an INFINITE
            # stream, so it emits N frames *per input frame* forever: ffmpeg 6.x
            # grinds for minutes and ffmpeg 7.x (incl. the bundled imageio-ffmpeg
            # 7.x build) fails the encoder outright ("received no packets").
            inputs += ["-i", p]

        audio_path = None
        if audio:
            audio_path = os.path.join(td, f"bed.{audio_ext}")
            with open(audio_path, "wb") as f:
                f.write(audio)
            inputs += ["-i", audio_path]

        # Per-scene filter: cover-crop to frame, then Ken Burns zoom (or a gentle static hold).
        chains = []
        for i in range(n):
            d_frames = max(1, round(durs[i] * fps))
            if ken_burns:
                D = max(2, d_frames)
                # SUPERSAMPLE the working frame (SS×) BEFORE zoompan. zoompan crops
                # its pan/zoom window in INTEGER input pixels; at display resolution
                # the sub-pixel per-frame motion rounds to whole-pixel jumps, so the
                # image "wiggles"/jitters instead of gliding. Panning a larger
                # frame makes each step a sub-pixel in the output → smooth. Cap the
                # work-frame long edge (~2560px) so a 1080p output doesn't balloon
                # into an 8K intermediate and OOM/stall the render.
                ss = max(1, min(4, 2560 // max(width, height)))
                cover = (f"[{i}:v]scale={width * ss}:{height * ss}:"
                         f"force_original_aspect_ratio=increase,"
                         f"crop={width * ss}:{height * ss},setsar=1")
                prog = f"(on/{D - 1})"                      # 0 → 1 across the scene
                zexpr = f"1+0.14*{prog}"                    # gentle linear zoom-in
                xc = "iw/2-(iw/zoom/2)"                     # centered X
                yc = "ih/2-(ih/zoom/2)"                     # centered Y
                # A real DIRECTIONAL pan (rotated per scene) so the image visibly
                # MOVES — a true Ken Burns glide, not just a centered zoom.
                v = i % 4
                if v == 0:      xe, ye = f"(iw-iw/zoom)*{prog}", yc          # pan →
                elif v == 1:    xe, ye = f"(iw-iw/zoom)*(1-{prog})", yc      # pan ←
                elif v == 2:    xe, ye = xc, f"(ih-ih/zoom)*{prog}"          # pan ↓
                else:           xe, ye = xc, f"(ih-ih/zoom)*(1-{prog})"      # pan ↑
                motion = (f"zoompan=z='{zexpr}':x='{xe}':y='{ye}':"
                          f"d={D}:s={width}x{height}:fps={fps}")
            else:
                cover = (f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                         f"crop={width}:{height},setsar=1")
                motion = f"zoompan=z=1:d={d_frames}:s={width}x{height}:fps={fps}"
            # settb + fps pin a CONSTANT frame rate on each scene stream. ffmpeg 7's
            # xfade rejects the still-image's undefined input rate ("current rate of
            # 1/0 is invalid") without this; ffmpeg 6 tolerated it. Harmless on the
            # single-scene path.
            chains.append(f"{cover},{motion},format=yuv420p,settb=1/{fps},fps={fps}[v{i}]")

        # Crossfade the scene streams together.
        if n == 1:
            vlabel = "[v0]"
            xfades = []
        else:
            xfades = []
            acc_len = durs[0]
            prev = "v0"
            for i in range(1, n):
                out = f"x{i}"
                offset = max(0.0, acc_len - T)
                xfades.append(f"[{prev}][v{i}]xfade=transition={trans}:duration={T:.3f}:offset={offset:.3f}[{out}]")
                acc_len = acc_len + durs[i] - T
                prev = out
            vlabel = f"[{prev}]"

        filtergraph = ";".join(chains + xfades)

        cmd = [exe, "-y", "-hide_banner", "-loglevel", "error", *inputs,
               "-filter_complex", filtergraph, "-map", vlabel]
        out_path = os.path.join(td, "out.mp4")
        if audio_path:
            cmd += ["-map", f"{n}:a", "-c:a", "aac", "-b:a", "128k", "-shortest"]
        cmd += ["-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]

        proc = subprocess.run(cmd, capture_output=True, timeout=600)
        if proc.returncode != 0:
            raise MediaError(f"ffmpeg scene assembly failed: {proc.stderr.decode(errors='replace')[-400:]}")
        with open(out_path, "rb") as f:
            data = f.read()

    if not validate_magic(data, "mp4"):
        raise MediaError("assembled output is not a valid MP4")
    total = sum(durs) - T * (n - 1)
    return {"bytes": data, "mime": "video/mp4", "sha3": sha3_hex(data),
            "scenes": n, "fps": fps, "duration_s": round(total, 2)}


def plan_scenes(prompt: str, *, max_scenes: int = 4) -> List[str]:
    """Turn a single free-text brief into an ordered list of scene prompts.

    Deterministic + dependency-free: split on explicit scene separators (newlines, ';', ' | ',
    or 'Scene N:' markers). A single sentence becomes one scene. The caller may instead pass an
    explicit scene list, in which case this is not used.
    """
    text = (prompt or "").strip()
    if not text:
        return []
    import re
    # explicit "Scene 1: ... Scene 2: ..." style
    parts = re.split(r"(?:^|\n)\s*scene\s*\d+\s*[:.)-]\s*", text, flags=re.IGNORECASE)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) <= 1:
        parts = [p.strip() for p in re.split(r"\n+|\s*\|\s*|\s*;\s*", text) if p.strip()]
    if len(parts) <= 1:
        # one brief, no separators: keep it a single scene (the model gets the whole intent)
        parts = [text]
    return parts[:max_scenes]
