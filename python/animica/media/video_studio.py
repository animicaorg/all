"""Video Studio — miner-side transforms for the 9.0.0 GPU-Studio video kinds.

Implements the five uploaded-video jobs from docs/gpu-studios-9.0.0.md:

  * upscale_video      (video_upscale)     — Real-ESRGAN 2x/4x (sr_models, vendored)
  * interpolate_video  (video_interpolate) — RIFE mid-frame synthesis (rife_arch,
                                             vendored) with ffmpeg-minterpolate fallback
  * subtitle_video     (video_subtitles)   — Whisper → SRT; burned or soft track; ZIP
  * remove_background  (video_bgremove)    — DeepLabV3 person matte → green / alpha
  * make_shorts        (video_shorts)      — scene cuts → scored vertical clips ZIP

Every public transform returns {"path": <file inside out_dir>, "mime", "sha3",
"meta": {model, device, timings, …}} and raises MediaError on ANY failure — never a
placeholder (fail-closed media contract). ``progress`` is an optional
``callable(pct: float, note: str = "")``; miner.py wires it to GatewayClient
(lease-extending heartbeats) — this module never talks to the network gateway
itself (model-weight downloads in sr_models/rife_arch are the only fetches).

House rules honored here: ffmpeg via base.resolve_ffmpeg() only (never ffprobe —
imageio-ffmpeg bundles no ffprobe; probe_video parses ``ffmpeg -i`` stderr), never
``-loop 1``, all heavy imports (torch/torchvision/transformers/sr_models/rife_arch)
inside functions, subprocess timeouts everywhere, scratch in TemporaryDirectory,
validate_magic on every output, CUDA OOM → empty_cache + halve tile/batch, retry
once, then MediaError.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
import zipfile
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

from .base import MediaBackendUnavailable, MediaError, resolve_ffmpeg, validate_magic

ProgressFn = Optional[Callable[..., None]]

# ── contract caps (docs/gpu-studios-9.0.0.md; the gateway clamps too — mirror them) ──
MAX_IN_W, MAX_IN_H = 4096, 2304       # input ceiling, either orientation — a crafted
                                      # 8K upload must never OOM-kill the miner
MAX_OUT_W, MAX_OUT_H = 3840, 2160     # video_upscale output ceiling (either orientation)
MAX_OUT_FPS = 120.0                   # video_interpolate output ceiling
MAX_UPSCALE_S = 600.0                 # ≤10 min
MAX_INTERP_S = 600.0                  # ≤10 min
MAX_SUBS_S = 1800.0                   # ≤30 min
MAX_BGREMOVE_S = 300.0                # ≤5 min
MAX_SHORTS_S = 3600.0                 # ≤60 min

GREEN_RGB = (0, 177, 64)              # #00B140 chroma green
_ASPECTS = ("9:16", "1:1", "16:9")
_WHISPER_MODEL = "openai/whisper-small"
_SCENE_THRESHOLD = 0.3


# ── small shared helpers ─────────────────────────────────────────────────────
def _ffmpeg() -> str:
    exe = resolve_ffmpeg()
    if not exe:
        raise MediaBackendUnavailable("ffmpeg not installed — required for the Video Studio")
    return exe


def _p(progress: ProgressFn, pct: float, note: str = "") -> None:
    """Report progress; a broken callback must never fail a render."""
    if progress is None:
        return
    try:
        progress(float(max(0.0, min(100.0, pct))), note)
    except Exception:
        pass


def _run(cmd: Sequence[str], *, timeout: float, what: str) -> subprocess.CompletedProcess:
    """Run a bounded subprocess; nonzero exit / timeout → MediaError with stderr tail."""
    try:
        proc = subprocess.run(list(cmd), capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise MediaError(f"{what} timed out after {timeout:.0f}s") from e
    except Exception as e:
        raise MediaError(f"{what} failed to start: {e}") from e
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode(errors="replace")[-400:]
        raise MediaError(f"{what} failed: {tail}")
    return proc


def _is_oom(e: Exception) -> bool:
    return type(e).__name__ == "OutOfMemoryError" or "out of memory" in str(e).lower()


def _cuda_empty_cache() -> None:
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:
        pass


def _device() -> str:
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


_CAP_CACHE: dict = {}


def _ffmpeg_lists(exe: str, kind: str, token: str) -> bool:
    """True iff `ffmpeg -filters` / `-encoders` lists `token` (word-exact)."""
    key = (exe, kind)
    if key not in _CAP_CACHE:
        try:
            proc = subprocess.run([exe, "-hide_banner", f"-{kind}"], capture_output=True, timeout=30)
            _CAP_CACHE[key] = (proc.stdout or b"").decode(errors="replace")
        except Exception:
            _CAP_CACHE[key] = ""
    return re.search(rf"\s{re.escape(token)}\s", _CAP_CACHE[key]) is not None


def _finalize(path: str, magic: str, mime: str, meta: dict) -> dict:
    """Fail-closed output check + streamed sha3 (== base.sha3_hex of the file bytes)."""
    import hashlib

    try:
        size = os.path.getsize(path)
    except OSError as e:
        raise MediaError(f"output missing: {e}") from e
    if size <= 0:
        raise MediaError("output is empty")
    h = hashlib.sha3_256()
    head = b""
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            if not head:
                head = chunk[:4096]
            h.update(chunk)
    if not validate_magic(head, magic):
        raise MediaError(f"output failed {magic} validation")
    return {"path": path, "mime": mime, "sha3": h.hexdigest(), "meta": meta}


def _read_exact(stream, n: int) -> Optional[bytes]:
    """Read exactly n bytes from a pipe, or None at clean EOF (partial tail → None)."""
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


# ── probe (ffmpeg ONLY — never ffprobe) ──────────────────────────────────────
_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_DIM_RE = re.compile(r"[,\s](\d{2,5})x(\d{2,5})(?:[\s,\[]|$)")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_TBR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*tbr")


def parse_ffmpeg_probe(stderr_text: str) -> dict:
    """Pure parser for `ffmpeg -i` stderr → {width, height, fps, duration_s, has_audio}.

    imageio-ffmpeg bundles no ffprobe, so this is the only portable probe. Raises
    MediaError when there is no video stream / no duration (fail closed)."""
    width = height = None
    fps = None
    has_audio = False
    for line in stderr_text.splitlines():
        if "Stream" in line and "Video:" in line and width is None:
            m = _DIM_RE.search(line)
            if m:
                width, height = int(m.group(1)), int(m.group(2))
            mf = _FPS_RE.search(line) or _TBR_RE.search(line)
            if mf:
                fps = float(mf.group(1))
        elif "Stream" in line and "Audio:" in line:
            has_audio = True
    md = _DUR_RE.search(stderr_text)
    if width is None or height is None:
        raise MediaError("input has no decodable video stream")
    if not md:
        raise MediaError("could not determine input duration")
    duration = int(md.group(1)) * 3600 + int(md.group(2)) * 60 + float(md.group(3))
    if fps is None or fps <= 0:
        raise MediaError("could not determine input frame rate")
    fps = min(fps, 240.0)
    return {"width": width, "height": height, "fps": fps,
            "duration_s": round(duration, 3), "has_audio": has_audio}


def check_input_resolution(width: int, height: int) -> None:
    """Shared input guard (either orientation): every studio kind decodes raw
    frames into RAM/VRAM, so an absurd-resolution upload is rejected up front
    instead of OOM-killing the miner mid-render."""
    w, h = int(width), int(height)
    if not ((w <= MAX_IN_W and h <= MAX_IN_H) or (w <= MAX_IN_H and h <= MAX_IN_W)):
        raise MediaError(
            f"{w}x{h} input exceeds the {MAX_IN_W}x{MAX_IN_H} resolution limit")


def probe_video(path: str) -> dict:
    """Probe a video file via `ffmpeg -i` stderr (exit code 1 there is expected)."""
    if not os.path.isfile(path):
        raise MediaError(f"input not found: {path}")
    exe = _ffmpeg()
    try:
        proc = subprocess.run([exe, "-hide_banner", "-i", os.path.abspath(path)],
                              capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as e:
        raise MediaError("probe timed out") from e
    return parse_ffmpeg_probe((proc.stderr or b"").decode("utf-8", errors="replace"))


# ── ffmpeg arg builders (pure — unit tested) ─────────────────────────────────
def build_decode_cmd(exe: str, input_path: str, *, fps: float, pix_fmt: str = "rgb24") -> List[str]:
    """Rawvideo frame pipe: CFR at `fps` so VFR inputs stay A/V-synced on re-encode."""
    return [exe, "-hide_banner", "-loglevel", "error", "-i", input_path,
            "-map", "0:v:0", "-f", "rawvideo", "-pix_fmt", pix_fmt,
            "-r", f"{fps:g}", "pipe:1"]


def build_encode_cmd(exe: str, out_path: str, *, width: int, height: int, fps: float,
                     audio_from: Optional[str] = None, in_pix_fmt: str = "rgb24",
                     vcodec: str = "libx264", out_pix_fmt: str = "yuv420p",
                     crf: int = 18, preset: str = "veryfast",
                     extra: Optional[Sequence[str]] = None,
                     audio_codec: str = "aac") -> List[str]:
    """Rawvideo-stdin encoder. Audio (if any) is carried over from `audio_from` as a
    second input via `-map 1:a?` — absent audio is fine, never an error."""
    cmd = [exe, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", in_pix_fmt, "-s", f"{width}x{height}",
           "-framerate", f"{fps:g}", "-i", "pipe:0"]
    if audio_from:
        cmd += ["-i", audio_from]
    cmd += ["-map", "0:v:0"]
    if audio_from:
        cmd += ["-map", "1:a?", "-c:a", audio_codec, "-b:a", "160k"]
    cmd += ["-c:v", vcodec]
    if vcodec == "libx264":
        cmd += ["-preset", preset, "-crf", str(crf)]
    cmd += ["-pix_fmt", out_pix_fmt]
    if list(extra or ()):
        cmd += list(extra)
    if out_path.endswith(".mp4"):
        cmd += ["-movflags", "+faststart"]
    if audio_from:
        cmd += ["-shortest"]
    cmd += [out_path]
    return cmd


def build_crop_filter(aspect: str) -> str:
    """Center-crop filter for a target aspect, even-dimension safe (pure)."""
    if aspect not in _ASPECTS:
        raise MediaError(f"aspect must be one of {_ASPECTS}, not {aspect!r}")
    a, b = (int(x) for x in aspect.split(":"))
    return (f"crop=w='trunc(min(iw,ih*{a}/{b})/2)*2'"
            f":h='trunc(min(ih,iw*{b}/{a})/2)*2'")


# ── streamed decode→transform→encode plumbing ────────────────────────────────
class _Pipeline:
    """Rawvideo decoder + encoder subprocess pair with bounded shutdown."""

    def __init__(self, dec_cmd: Sequence[str], enc_cmd: Sequence[str], td: str):
        self._enc_err = os.path.join(td, "enc.err")
        self._dec_err = os.path.join(td, "dec.err")
        self.dec = subprocess.Popen(list(dec_cmd), stdout=subprocess.PIPE,
                                    stderr=open(self._dec_err, "wb"))
        self.enc = subprocess.Popen(list(enc_cmd), stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL,
                                    stderr=open(self._enc_err, "wb"))

    def read_frame(self, nbytes: int) -> Optional[bytes]:
        return _read_exact(self.dec.stdout, nbytes)

    def write(self, data: bytes) -> None:
        try:
            self.enc.stdin.write(data)
        except BrokenPipeError as e:
            raise MediaError(f"encoder died: {self._err_tail(self._enc_err)}") from e

    def _err_tail(self, path: str) -> str:
        try:
            with open(path, "rb") as f:
                return f.read().decode(errors="replace")[-400:]
        except OSError:
            return "?"

    def finish(self, *, timeout: float = 600.0) -> None:
        try:
            self.enc.stdin.close()
        except Exception:
            pass
        try:
            self.dec.stdout.close()
        except Exception:
            pass
        for proc, err, what in ((self.dec, self._dec_err, "decoder"),
                                (self.enc, self._enc_err, "encoder")):
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired as e:
                proc.kill()
                raise MediaError(f"ffmpeg {what} hung") from e
            # rc 0 required for the encoder; the decoder may exit 1 on a broken
            # pipe if we stopped reading early — only fail it on real error text.
            if rc != 0 and what == "encoder":
                raise MediaError(f"ffmpeg encoder failed: {self._err_tail(err)}")

    def kill(self) -> None:
        for proc in (self.dec, self.enc):
            try:
                proc.kill()
            except Exception:
                pass


# ── upscale ──────────────────────────────────────────────────────────────────
_UPSCALE_WEIGHTS = {
    ("fast", 2): ("realesr-general-x4v3", 4),   # x4 net, torch-downscaled to 2x
    ("fast", 4): ("realesr-general-x4v3", 4),
    ("quality", 2): ("realesrgan-x2plus", 2),
    ("quality", 4): ("realesrgan-x4plus", 4),
}


def upscale_video(input_path: str, out_dir: str, *, scale: int = 2,
                  model: str = "fast", progress: ProgressFn = None) -> dict:
    """Super-resolve a video 2x/4x. fast=SRVGGNetCompact, quality=RRDBNet."""
    t0 = time.time()
    scale = int(scale)
    if scale not in (2, 4):
        raise MediaError(f"scale must be 2 or 4, not {scale}")
    if model not in ("fast", "quality"):
        raise MediaError(f"model must be fast|quality, not {model!r}")
    input_path = os.path.abspath(input_path)
    exe = _ffmpeg()
    info = probe_video(input_path)
    check_input_resolution(info["width"], info["height"])
    if info["duration_s"] > MAX_UPSCALE_S:
        raise MediaError(f"input is {info['duration_s']:.0f}s — video_upscale caps at {MAX_UPSCALE_S:.0f}s")
    w, h, fps = info["width"], info["height"], info["fps"]
    ow, oh = w * scale, h * scale
    if not ((ow <= MAX_OUT_W and oh <= MAX_OUT_H) or (ow <= MAX_OUT_H and oh <= MAX_OUT_W)):
        raise MediaError(f"{ow}x{oh} output exceeds the {MAX_OUT_W}x{MAX_OUT_H} contract cap")

    _p(progress, 1, "loading model")
    import torch
    from . import sr_models

    device = _device()
    weight_name, net_scale = _UPSCALE_WEIGHTS[(model, scale)]
    try:
        net = sr_models.load_upscaler(weight_name, device=device)
    except MediaError:
        raise
    except Exception as e:
        if _is_oom(e) and device == "cuda":
            _cuda_empty_cache()
            device = "cpu"
            net = sr_models.load_upscaler(weight_name, device=device)
        else:
            raise MediaError(f"failed to load upscaler {weight_name}: {e}") from e

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"upscaled_{scale}x.mp4")
    expected = max(1, int(round(info["duration_s"] * fps)))
    frame_cap = expected + int(fps * 5) + 8
    tile = 512
    frames = 0

    with tempfile.TemporaryDirectory(prefix="anmups_") as td:
        pipe = _Pipeline(
            build_decode_cmd(exe, input_path, fps=fps),
            build_encode_cmd(exe, out_path, width=ow, height=oh, fps=fps, audio_from=input_path),
            td)
        try:
            nbytes = w * h * 3
            while frames < frame_cap:
                raw = pipe.read_frame(nbytes)
                if raw is None:
                    break
                x = torch.from_numpy(
                    np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3).copy()
                ).permute(2, 0, 1)[None].float() / 255.0
                for attempt in (0, 1):
                    try:
                        out = sr_models.upscale_tiled(net, x, net_scale, tile=tile)
                        break
                    except Exception as e:
                        if _is_oom(e) and attempt == 0:
                            _cuda_empty_cache()
                            tile = max(64, tile // 2)   # halve the tile, retry once
                            continue
                        raise MediaError(f"upscale failed on frame {frames}: {e}") from e
                if out.shape[-2:] != (oh, ow):  # fast model is x4; downscale for scale=2
                    out = torch.nn.functional.interpolate(
                        out, size=(oh, ow), mode="bicubic", antialias=True).clamp_(0, 1)
                pipe.write((out[0].permute(1, 2, 0) * 255.0 + 0.5).byte().numpy().tobytes())
                frames += 1
                if frames % 10 == 0 or frames == expected:
                    _p(progress, 2 + 95.0 * min(frames / expected, 1.0), f"frame {frames}/{expected}")
            if frames == 0:
                raise MediaError("input produced no frames")
            pipe.finish(timeout=600)
        except MediaError:
            pipe.kill()
            raise
        except Exception as e:
            pipe.kill()
            raise MediaError(f"upscale pipeline failed: {e}") from e

    _p(progress, 99, "finalizing")
    meta = {"model": weight_name, "device": device, "scale": scale, "quality": model,
            "in": f"{w}x{h}", "out": f"{ow}x{oh}", "fps": fps, "frames": frames,
            "tile": tile, "timings": {"total_s": round(time.time() - t0, 2)}}
    return _finalize(out_path, "mp4", "video/mp4", meta)


# ── interpolate ──────────────────────────────────────────────────────────────
def interpolate_video(input_path: str, out_dir: str, *, factor: int = 2,
                      progress: ProgressFn = None) -> dict:
    """Raise the frame rate 2x/4x with RIFE mid-frame synthesis (recursive for 4x);
    falls back to ffmpeg minterpolate when the RIFE arch/weights are unavailable."""
    t0 = time.time()
    factor = int(factor)
    if factor not in (2, 4):
        raise MediaError(f"factor must be 2 or 4, not {factor}")
    input_path = os.path.abspath(input_path)
    exe = _ffmpeg()
    info = probe_video(input_path)
    check_input_resolution(info["width"], info["height"])
    if info["duration_s"] > MAX_INTERP_S:
        raise MediaError(f"input is {info['duration_s']:.0f}s — video_interpolate caps at {MAX_INTERP_S:.0f}s")
    w, h, fps = info["width"], info["height"], info["fps"]
    if fps * 2 > MAX_OUT_FPS:
        raise MediaError(f"input is already {fps:g} fps — 2x would exceed the {MAX_OUT_FPS:g} fps cap")
    if fps * factor > MAX_OUT_FPS:
        factor = 2  # clamp 4x → 2x rather than fail (cap is on the OUTPUT)
    out_fps = fps * factor

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"interpolated_{factor}x.mp4")

    # RIFE is "unavailable" when the vendored arch/weights can't load — CPU is
    # allowed (slow is fine). Any load failure → ffmpeg minterpolate fallback.
    net = None
    device = _device()
    try:
        from . import rife_arch
        _p(progress, 1, "loading RIFE")
        net = rife_arch.load_rife(device=device)
    except Exception:
        net = None

    if net is None:
        _p(progress, 2, "RIFE unavailable — ffmpeg minterpolate")
        cmd = [exe, "-y", "-hide_banner", "-loglevel", "error", "-i", input_path,
               "-vf", f"minterpolate=fps={out_fps:g}",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart",
               "-c:a", "aac", "-b:a", "160k", out_path]
        _run(cmd, timeout=max(900.0, info["duration_s"] * 30), what="minterpolate")
        meta = {"model": "ffmpeg-minterpolate", "device": "cpu", "factor": factor,
                "fps_in": fps, "fps_out": out_fps,
                "timings": {"total_s": round(time.time() - t0, 2)}}
        return _finalize(out_path, "mp4", "video/mp4", meta)

    from . import rife_arch  # noqa: F811 — loaded above

    def _mid(a, b, note: str):
        for attempt in (0, 1):
            try:
                return rife_arch.interpolate_mid(net, a, b)
            except Exception as e:
                if _is_oom(e) and attempt == 0:
                    _cuda_empty_cache()
                    continue
                raise MediaError(f"RIFE failed ({note}): {e}") from e

    expected = max(1, int(round(info["duration_s"] * fps)))
    frame_cap = expected + int(fps * 5) + 8
    frames_in = frames_out = 0
    with tempfile.TemporaryDirectory(prefix="anmitp_") as td:
        pipe = _Pipeline(
            build_decode_cmd(exe, input_path, fps=fps),
            build_encode_cmd(exe, out_path, width=w, height=h, fps=out_fps, audio_from=input_path),
            td)
        try:
            nbytes = w * h * 3
            prev = None
            while frames_in < frame_cap:
                raw = pipe.read_frame(nbytes)
                if raw is None:
                    break
                cur = np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3).copy()
                if prev is not None:
                    m = _mid(prev, cur, f"pair {frames_in}")
                    mids = [m] if factor == 2 else \
                        [_mid(prev, m, "q1"), m, _mid(m, cur, "q3")]  # recursive 4x
                    pipe.write(prev.tobytes())
                    for f in mids:
                        pipe.write(f.tobytes())
                    frames_out += factor
                prev = cur
                frames_in += 1
                if frames_in % 5 == 0 or frames_in == expected:
                    _p(progress, 2 + 95.0 * min(frames_in / expected, 1.0),
                       f"frame {frames_in}/{expected}")
            if prev is None:
                raise MediaError("input produced no frames")
            for _ in range(factor):  # hold the tail so duration stays n/fps exactly
                pipe.write(prev.tobytes())
                frames_out += 1
            pipe.finish(timeout=600)
        except MediaError:
            pipe.kill()
            raise
        except Exception as e:
            pipe.kill()
            raise MediaError(f"interpolate pipeline failed: {e}") from e

    _p(progress, 99, "finalizing")
    meta = {"model": f"rife-flownet-4.13.2 ({rife_arch.RIFE_REPO})", "device": device,
            "factor": factor, "fps_in": fps, "fps_out": out_fps,
            "frames_in": frames_in, "frames_out": frames_out,
            "timings": {"total_s": round(time.time() - t0, 2)}}
    return _finalize(out_path, "mp4", "video/mp4", meta)


# ── subtitles ────────────────────────────────────────────────────────────────
def format_srt_time(seconds: float) -> str:
    """SRT timestamp HH:MM:SS,mmm (pure)."""
    ms = max(0, int(round(float(seconds) * 1000)))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def srt_from_segments(segments: Sequence[Tuple[Optional[float], Optional[float], str]]) -> str:
    """Pure SRT writer. segments: (start_s, end_s|None, text); ordered by start,
    empty texts skipped, open ends closed at +2.5s, zero-length stretched 0.3s."""
    entries = []
    for start, end, text in segments:
        text = (text or "").strip()
        if not text or start is None:
            continue
        start = max(0.0, float(start))
        end = float(end) if end is not None else start + 2.5
        if end <= start:
            end = start + 0.3
        entries.append((start, end, text))
    entries.sort(key=lambda e: e[0])
    blocks = [f"{i}\n{format_srt_time(s)} --> {format_srt_time(e)}\n{t}\n"
              for i, (s, e, t) in enumerate(entries, start=1)]
    return "\n".join(blocks) + ("\n" if blocks else "")


def _extract_audio_f32(exe: str, input_path: str, timeout: float) -> np.ndarray:
    """Decode the audio track to 16 kHz mono float32 via an ffmpeg pipe."""
    proc = _run([exe, "-hide_banner", "-loglevel", "error", "-i", input_path,
                 "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-acodec", "pcm_f32le",
                 "pipe:1"], timeout=timeout, what="audio extraction")
    audio = np.frombuffer(proc.stdout, dtype=np.float32)
    if audio.size < 1600:  # <0.1s of audio is not transcribable
        raise MediaError("input has no usable audio")
    return audio.copy()


def _load_asr():
    try:
        import torch
        from transformers import pipeline
    except Exception as e:  # pragma: no cover - env dependent
        raise MediaBackendUnavailable(f"transformers/torch not installed: {e}") from e
    cuda = torch.cuda.is_available()
    kwargs = {"torch_dtype": torch.float16} if cuda else {}
    try:
        return pipeline("automatic-speech-recognition", model=_WHISPER_MODEL,
                        chunk_length_s=30, return_timestamps=True,
                        device=0 if cuda else -1, **kwargs)
    except Exception as e:
        raise MediaError(f"failed to load {_WHISPER_MODEL}: {e}") from e


def _transcribe(asr, audio: np.ndarray, language: str):
    """→ (segments [(start, end, text)], full_text). OOM → CPU retry once."""
    call_kwargs: dict = {}
    lang = (language or "auto").strip().lower()
    if lang and lang != "auto":
        call_kwargs["generate_kwargs"] = {"language": lang, "task": "transcribe"}
    try:
        result = asr({"array": audio, "sampling_rate": 16000}, **call_kwargs)
    except Exception as e:
        if not _is_oom(e):
            raise MediaError(f"transcription failed: {e}") from e
        _cuda_empty_cache()
        try:
            asr.model.to("cpu")
            asr.device = __import__("torch").device("cpu")
            result = asr({"array": audio, "sampling_rate": 16000}, **call_kwargs)
        except Exception as e2:
            raise MediaError(f"transcription failed after OOM retry: {e2}") from e2
    segments = []
    for ch in result.get("chunks") or []:
        ts = ch.get("timestamp") or (None, None)
        segments.append((ts[0], ts[1], ch.get("text") or ""))
    if not segments and (result.get("text") or "").strip():
        segments = [(0.0, None, result["text"])]
    return segments, (result.get("text") or "").strip()


def _apply_subtitles(exe: str, video_path: str, srt_dir: str, srt_name: str,
                     out_path: str, *, burn_in: bool, timeout: float,
                     has_audio: bool) -> str:
    """Burn with the `subtitles` filter iff the build lists it, else soft-mux
    mov_text. Returns the mode used ("burned"|"soft")."""
    srt_path = os.path.join(srt_dir, srt_name)
    if burn_in and _ffmpeg_lists(exe, "filters", "subtitles"):
        cmd = [exe, "-y", "-hide_banner", "-loglevel", "error", "-i", video_path,
               "-vf", f"subtitles={srt_name}",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
        if has_audio:
            cmd += ["-c:a", "aac", "-b:a", "160k"]
        cmd += ["-movflags", "+faststart", out_path]
        # cwd = the srt's dir: the subtitles filter parses ':' and '\' in paths,
        # a bare relative filename sidesteps every escaping pitfall.
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=srt_dir)
        except subprocess.TimeoutExpired as e:
            raise MediaError("subtitle burn timed out") from e
        if proc.returncode == 0:
            return "burned"
        # burn failed (e.g. build lacks libass at runtime) → fall through to soft
    cmd = [exe, "-y", "-hide_banner", "-loglevel", "error", "-i", video_path,
           "-i", srt_path, "-map", "0", "-map", "1:0", "-c", "copy",
           "-c:s", "mov_text", "-movflags", "+faststart", out_path]
    _run(cmd, timeout=timeout, what="subtitle soft-mux")
    return "soft"


def subtitle_video(input_path: str, out_dir: str, *, language: str = "auto",
                   burn_in: bool = True, progress: ProgressFn = None) -> dict:
    """Whisper transcription → ZIP {subtitled video, captions.srt, transcript.txt}."""
    t0 = time.time()
    input_path = os.path.abspath(input_path)
    exe = _ffmpeg()
    info = probe_video(input_path)
    check_input_resolution(info["width"], info["height"])
    if info["duration_s"] > MAX_SUBS_S:
        raise MediaError(f"input is {info['duration_s']:.0f}s — video_subtitles caps at {MAX_SUBS_S:.0f}s")
    if not info["has_audio"]:
        raise MediaError("input has no audio track to transcribe")

    _p(progress, 2, "extracting audio")
    audio = _extract_audio_f32(exe, input_path, timeout=max(120.0, info["duration_s"] * 4))
    _p(progress, 10, "loading whisper")
    asr = _load_asr()
    _p(progress, 15, "transcribing")
    segments, text = _transcribe(asr, audio, language)
    srt_text = srt_from_segments(segments)
    if not srt_text.strip():
        raise MediaError("transcription produced no speech segments")

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "subtitles.zip")
    with tempfile.TemporaryDirectory(prefix="anmsub_") as td:
        srt_name = "captions.srt"
        with open(os.path.join(td, srt_name), "w", encoding="utf-8") as f:
            f.write(srt_text)
        _p(progress, 70, "rendering subtitle track")
        subbed = os.path.join(td, "subtitled.mp4")
        mode = _apply_subtitles(exe, input_path, td, srt_name, subbed,
                                burn_in=bool(burn_in),
                                timeout=max(600.0, info["duration_s"] * 20),
                                has_audio=True)
        _p(progress, 92, "packing zip")
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(subbed, "subtitled.mp4")
            z.write(os.path.join(td, srt_name), "captions.srt")
            z.writestr("transcript.txt", text + "\n")

    meta = {"model": _WHISPER_MODEL, "device": _device(), "language": language,
            "subtitle_mode": mode, "segments": len(segments),
            "timings": {"total_s": round(time.time() - t0, 2)}}
    return _finalize(out_path, "zip", "application/zip", meta)


# ── background removal ───────────────────────────────────────────────────────
def remove_background(input_path: str, out_dir: str, *, mode: str = "green",
                      progress: ProgressFn = None) -> dict:
    """Person matting (torchvision DeepLabV3-ResNet50, class 15) → green-screen mp4
    or alpha vp9 webm (iff the ffmpeg build has libvpx-vp9, else green + meta.fallback)."""
    t0 = time.time()
    if mode not in ("green", "alpha"):
        raise MediaError(f"mode must be green|alpha, not {mode!r}")
    input_path = os.path.abspath(input_path)
    exe = _ffmpeg()
    info = probe_video(input_path)
    check_input_resolution(info["width"], info["height"])
    if info["duration_s"] > MAX_BGREMOVE_S:
        raise MediaError(f"input is {info['duration_s']:.0f}s — video_bgremove caps at {MAX_BGREMOVE_S:.0f}s")
    w, h, fps = info["width"], info["height"], info["fps"]

    fallback = None
    if mode == "alpha" and not _ffmpeg_lists(exe, "encoders", "libvpx-vp9"):
        mode, fallback = "green", "no libvpx-vp9 encoder — delivered green-screen mp4"

    _p(progress, 1, "loading segmentation model")
    try:
        import torch
        import torch.nn.functional as TF
        from torchvision.models.segmentation import (DeepLabV3_ResNet50_Weights,
                                                     deeplabv3_resnet50)
    except Exception as e:  # pragma: no cover - env dependent
        raise MediaBackendUnavailable(f"torchvision not installed: {e}") from e
    device = _device()
    try:
        seg = deeplabv3_resnet50(weights=DeepLabV3_ResNet50_Weights.DEFAULT).eval().to(device)
    except Exception as e:
        raise MediaError(f"failed to load deeplabv3_resnet50: {e}") from e
    mean = torch.tensor([0.485, 0.456, 0.406], device=device).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], device=device).view(1, 3, 1, 1)
    green = np.array(GREEN_RGB, dtype=np.float32).reshape(1, 1, 3)

    os.makedirs(out_dir, exist_ok=True)
    if mode == "alpha":
        out_path = os.path.join(out_dir, "bg_removed.webm")
        enc_extra = ["-b:v", "0", "-crf", "32", "-deadline", "good", "-cpu-used", "5",
                     "-auto-alt-ref", "0"]
        audio_codec = "libopus" if _ffmpeg_lists(exe, "encoders", "libopus") else "libvorbis"
        enc_cmd_kwargs = dict(in_pix_fmt="rgba", vcodec="libvpx-vp9",
                              out_pix_fmt="yuva420p", extra=enc_extra, audio_codec=audio_codec)
        magic, mime = "webm", "video/webm"
    else:
        out_path = os.path.join(out_dir, "bg_removed.mp4")
        enc_cmd_kwargs = dict()
        magic, mime = "mp4", "video/mp4"

    def _segment_batch(batch: "torch.Tensor") -> "torch.Tensor":
        """(B,3,H,W) uint8 0..255 float → (B,H,W) person prob, model run at ≤520px."""
        x = (batch / 255.0 - mean) / std
        long_side = max(h, w)
        if long_side > 520:
            s = 520.0 / long_side
            x = TF.interpolate(x, size=(max(1, int(h * s)) // 2 * 2, max(1, int(w * s)) // 2 * 2),
                               mode="bilinear", align_corners=False)
        with torch.no_grad():
            logits = seg(x)["out"]
        prob = logits.softmax(dim=1)[:, 15]                       # person class
        prob = TF.interpolate(prob[:, None], size=(h, w), mode="bilinear",
                              align_corners=False)
        # dilate via max-pool (NO opencv) so hair/edges aren't shaved off
        return TF.max_pool2d(prob, kernel_size=5, stride=1, padding=2)[:, 0]

    expected = max(1, int(round(info["duration_s"] * fps)))
    frame_cap = expected + int(fps * 5) + 8
    batch_size = 4
    ema_alpha = 0.6      # history weight: m_t = 0.6*m_{t-1} + 0.4*mask_t
    frames = 0
    ema = None

    with tempfile.TemporaryDirectory(prefix="anmbgr_") as td:
        pipe = _Pipeline(
            build_decode_cmd(exe, input_path, fps=fps),
            build_encode_cmd(exe, out_path, width=w, height=h, fps=fps,
                             audio_from=input_path, **enc_cmd_kwargs),
            td)
        try:
            nbytes = w * h * 3
            eof = False
            while not eof and frames < frame_cap:
                batch_np = []
                while len(batch_np) < batch_size:
                    raw = pipe.read_frame(nbytes)
                    if raw is None:
                        eof = True
                        break
                    batch_np.append(np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3).copy())
                if not batch_np:
                    break
                bt = torch.from_numpy(np.stack(batch_np)).permute(0, 3, 1, 2).float().to(device)
                for attempt in (0, 1):
                    try:
                        if attempt == 1 and bt.shape[0] > 1:   # halve the batch, retry once
                            half = bt.shape[0] // 2
                            masks = torch.cat([_segment_batch(bt[:half]), _segment_batch(bt[half:])])
                        else:
                            masks = _segment_batch(bt)
                        break
                    except Exception as e:
                        if _is_oom(e) and attempt == 0:
                            _cuda_empty_cache()
                            continue
                        raise MediaError(f"segmentation failed at frame {frames}: {e}") from e
                masks = masks.clamp(0, 1).cpu()
                for i, frame_np in enumerate(batch_np):
                    m = masks[i]
                    ema = m if ema is None else ema_alpha * ema + (1 - ema_alpha) * m
                    a = ema.numpy()[..., None]                      # (H,W,1) soft matte
                    if mode == "alpha":
                        rgba = np.empty((h, w, 4), dtype=np.uint8)
                        rgba[..., :3] = frame_np
                        rgba[..., 3] = (a[..., 0] * 255.0 + 0.5).astype(np.uint8)
                        pipe.write(rgba.tobytes())
                    else:
                        comp = frame_np.astype(np.float32) * a + green * (1.0 - a)
                        pipe.write(comp.clip(0, 255).astype(np.uint8).tobytes())
                    frames += 1
                _p(progress, 2 + 95.0 * min(frames / expected, 1.0), f"frame {frames}/{expected}")
            if frames == 0:
                raise MediaError("input produced no frames")
            pipe.finish(timeout=600)
        except MediaError:
            pipe.kill()
            raise
        except Exception as e:
            pipe.kill()
            raise MediaError(f"background-removal pipeline failed: {e}") from e

    _p(progress, 99, "finalizing")
    meta = {"model": "torchvision/deeplabv3_resnet50", "device": device, "mode": mode,
            "frames": frames, "ema_alpha": ema_alpha,
            "timings": {"total_s": round(time.time() - t0, 2)}}
    if fallback:
        meta["fallback"] = fallback
    return _finalize(out_path, magic, mime, meta)


# ── shorts ───────────────────────────────────────────────────────────────────
_SCENE_PTS_RE = re.compile(r"pts_time:\s*([0-9]+(?:\.[0-9]+)?)")
_SCENE_SCORE_RE = re.compile(r"lavfi\.scene_score=([0-9]+(?:\.[0-9]+)?)")
_MEAN_VOL_RE = re.compile(r"mean_volume:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*dB")


def parse_scene_times(text: str) -> List[Tuple[float, float]]:
    """Pure parser for `select='gt(scene,T)',metadata=print` stderr →
    [(pts_time_s, scene_score)] in stream order."""
    out: List[Tuple[float, float]] = []
    pending: Optional[float] = None
    for line in text.splitlines():
        m = _SCENE_PTS_RE.search(line)
        if m:
            pending = float(m.group(1))
        m = _SCENE_SCORE_RE.search(line)
        if m and pending is not None:
            out.append((pending, float(m.group(1))))
            pending = None
    return out


def parse_mean_volume(text: str) -> Optional[float]:
    """Pure parser for volumedetect stderr → mean volume in dB (None if absent)."""
    m = _MEAN_VOL_RE.search(text)
    return float(m.group(1)) if m else None


def score_window(seg_len: float, duration: float, mean_vol_db: Optional[float]) -> float:
    """Pure score: normalized segment length + normalized loudness (−60..0 dB → 0..1)."""
    length_score = max(0.0, min(seg_len, duration)) / max(duration, 1e-6)
    vol_score = 0.5 if mean_vol_db is None else max(0.0, min((mean_vol_db + 60.0) / 60.0, 1.0))
    return length_score + vol_score


def pick_short_windows(candidates: Sequence[Tuple[float, float]], video_duration: float,
                       *, count: int, duration: float) -> List[Tuple[float, float]]:
    """Pure: greedy top-score non-overlapping windows of `duration`. candidates are
    (start_s, score). Degrades gracefully — with no/few scene cuts it fills with
    evenly-spaced windows; a video shorter than count*duration yields fewer."""
    video_duration = float(video_duration)
    if video_duration <= 0:
        raise MediaError("cannot pick windows from an empty video")
    d = min(float(duration), video_duration)
    chosen: List[Tuple[float, float]] = []

    def _fits(s: float) -> bool:
        return all(s + d <= a + 1e-6 or s >= b - 1e-6 for a, b in chosen)

    for start, _score in sorted(candidates, key=lambda c: -c[1]):
        s = max(0.0, min(float(start), video_duration - d))
        if _fits(s):
            chosen.append((s, s + d))
        if len(chosen) >= count:
            break
    if len(chosen) < count:  # evenly-spaced fill (also the 0-scene-changes path)
        span = video_duration - d
        for i in range(count):
            if len(chosen) >= count:
                break
            s = 0.0 if count == 1 or span <= 0 else span * i / (count - 1)
            if _fits(s):
                chosen.append((s, s + d))
    return sorted(round_window(w) for w in chosen)


def round_window(w: Tuple[float, float]) -> Tuple[float, float]:
    return (round(w[0], 3), round(w[1], 3))


def make_shorts(input_path: str, out_dir: str, *, count: int = 3, duration: float = 30,
                aspect: str = "9:16", subtitles: bool = True,
                progress: ProgressFn = None) -> dict:
    """Scene-cut detection → scored non-overlapping windows → center-cropped clips
    (+ best-effort Whisper subtitles) → ZIP of shorts."""
    t0 = time.time()
    count = max(1, min(int(count), 5))
    duration = max(10.0, min(float(duration), 60.0))
    if aspect not in _ASPECTS:
        raise MediaError(f"aspect must be one of {_ASPECTS}, not {aspect!r}")
    input_path = os.path.abspath(input_path)
    exe = _ffmpeg()
    info = probe_video(input_path)
    check_input_resolution(info["width"], info["height"])
    if info["duration_s"] > MAX_SHORTS_S:
        raise MediaError(f"input is {info['duration_s']:.0f}s — video_shorts caps at {MAX_SHORTS_S:.0f}s")
    dur_v = info["duration_s"]

    # 1) scene cuts
    _p(progress, 2, "detecting scene cuts")
    try:
        proc = subprocess.run(
            [exe, "-hide_banner", "-i", input_path, "-an",
             "-vf", f"select='gt(scene,{_SCENE_THRESHOLD})',metadata=print",
             "-f", "null", "-"],
            capture_output=True, timeout=max(300.0, dur_v * 4))
    except subprocess.TimeoutExpired as e:
        raise MediaError("scene detection timed out") from e
    if proc.returncode != 0:
        raise MediaError(f"scene detection failed: {(proc.stderr or b'').decode(errors='replace')[-300:]}")
    scenes = parse_scene_times((proc.stderr or b"").decode("utf-8", errors="replace"))

    # 2) candidate windows at each segment start, scored by length + loudness
    cut_times = sorted(t for t, _ in scenes if 0.0 < t < dur_v)
    seg_starts = [0.0] + cut_times
    candidates: List[Tuple[float, float]] = []
    for i, s in enumerate(seg_starts[:40]):  # bounded volumedetect probes
        seg_end = seg_starts[i + 1] if i + 1 < len(seg_starts) else dur_v
        vol = None
        if info["has_audio"]:
            try:
                vp = subprocess.run(
                    [exe, "-hide_banner", "-ss", f"{s:.3f}", "-t", f"{min(duration, dur_v - s):.3f}",
                     "-i", input_path, "-vn", "-af", "volumedetect", "-f", "null", "-"],
                    capture_output=True, timeout=120)
                vol = parse_mean_volume((vp.stderr or b"").decode("utf-8", errors="replace"))
            except Exception:
                vol = None
        candidates.append((s, score_window(seg_end - s, duration, vol)))
        _p(progress, 5 + 15.0 * (i + 1) / max(len(seg_starts[:40]), 1), "scoring segments")
    windows = pick_short_windows(candidates, dur_v, count=count, duration=duration)
    if not windows:
        raise MediaError("no usable short windows found")

    # 3) cut + crop (+ optional subtitles) each window, then zip
    asr = None
    subs_note = "off"
    if subtitles and info["has_audio"]:
        try:
            asr = _load_asr()     # best-effort: video_shorts is an ffmpeg-only capability
            subs_note = "whisper"
        except Exception:
            subs_note = "unavailable"
    elif subtitles:
        subs_note = "no-audio"

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "shorts.zip")
    crop = build_crop_filter(aspect)
    made: List[Tuple[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="anmsht_") as td:
        for i, (s, e) in enumerate(windows, start=1):
            base = 20 + 75.0 * (i - 1) / len(windows)
            _p(progress, base, f"cutting short {i}/{len(windows)}")
            clip = os.path.join(td, f"short_{i:02d}.mp4")
            cmd = [exe, "-y", "-hide_banner", "-loglevel", "error",
                   "-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", input_path,
                   "-vf", crop, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                   "-movflags", "+faststart", clip]
            _run(cmd, timeout=max(300.0, (e - s) * 30), what=f"short {i} cut")
            final = clip
            if asr is not None:
                try:
                    audio = _extract_audio_f32(exe, clip, timeout=120)
                    segs, _txt = _transcribe(asr, audio, "auto")
                    srt = srt_from_segments(segs)
                    if srt.strip():
                        with open(os.path.join(td, f"short_{i:02d}.srt"), "w", encoding="utf-8") as f:
                            f.write(srt)
                        subbed = os.path.join(td, f"short_{i:02d}_sub.mp4")
                        _apply_subtitles(exe, clip, td, f"short_{i:02d}.srt", subbed,
                                         burn_in=True, timeout=600, has_audio=True)
                        final = subbed
                except Exception:
                    pass  # a silent/unintelligible short still ships un-subtitled
            made.append((final, f"short_{i:02d}.mp4"))
        _p(progress, 96, "packing zip")
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            for src, arcname in made:
                z.write(src, arcname)

    meta = {"model": (_WHISPER_MODEL if asr is not None else "ffmpeg-scene"),
            "device": _device(), "aspect": aspect, "count": len(made),
            "windows": [list(w) for w in windows], "scene_cuts": len(cut_times),
            "subtitles": subs_note,
            "timings": {"total_s": round(time.time() - t0, 2)}}
    return _finalize(out_path, "zip", "application/zip", meta)
