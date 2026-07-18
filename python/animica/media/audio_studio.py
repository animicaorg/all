"""Audio Studio — miner-side processing for the 9.0.0 GPU-Studio audio kinds.

Implements (docs/gpu-studios-9.0.0.md):

  * separate_stems  — audio_stems / audio_isolate: HDemucs source separation
                      (torchaudio HDEMUCS_HIGH_MUSDB_PLUS), chunked overlap-add
                      inference so full-length songs fit in memory, GPU with a
                      one-shot CPU retry. Delivers a zip of encoded stems.
  * enhance_audio   — audio_enhance: non-stationary spectral denoise
                      (noisereduce) + 40 Hz high-pass + integrated-loudness
                      normalization (pyloudnorm) + look-ahead peak limiter.
  * master_audio    — audio_master: optional reference spectral match (median
                      Welch band spectra -> clamped per-band gains -> linear
                      phase FIR) + LUFS preset + limiter.

Every public entry point returns {"path", "mime", "sha3", "meta"} with the
artifact written inside out_dir, and raises MediaError on any failure — never a
placeholder. All audio I/O goes through the resolved ffmpeg binary (raw f32le
pipes; no torchaudio IO, no ffprobe). Heavy imports (numpy/scipy/torch/
torchaudio/pyloudnorm/noisereduce) stay inside functions so importing this
module is free. The DSP helpers (limiter, chunking, band spectra, FIR match,
duration parser) are small deterministic functions unit-tested on CPU with no
models or network (test_audio_studio.py).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from typing import Callable, Dict, Optional, Tuple

from .base import MediaError, MediaBackendUnavailable, resolve_ffmpeg, validate_magic

# ── limits / knobs ───────────────────────────────────────────────────────────
STEMS_MAX_SECONDS = 20 * 60      # audio_stems / audio_isolate
ENHANCE_MAX_SECONDS = 60 * 60    # audio_enhance
MASTER_MAX_SECONDS = 60 * 60     # audio_master (program and reference alike)

MASTER_PRESETS = {"streaming": -14.0, "loud": -9.0, "podcast": -16.0}

_PROBE_TIMEOUT_S = 60
_DECODE_TIMEOUT_S = 1800
_ENCODE_TIMEOUT_S = 1800

_STEM_CHUNK_SECONDS = 10.0       # HDemucs window per inference call
_STEM_OVERLAP_SECONDS = 1.0      # linear crossfade between windows

_DEFAULT_SOURCES = ("drums", "bass", "other", "vocals")

Progress = Optional[Callable[..., None]]


def _p(progress: Progress, pct: float, note: str = "") -> None:
    """Best-effort progress relay — a broken callback must never fail a job."""
    if progress is None:
        return
    try:
        progress(float(pct), note)
    except Exception:
        pass


def _ffmpeg() -> str:
    exe = resolve_ffmpeg()
    if not exe:
        raise MediaBackendUnavailable("ffmpeg not installed — required for audio studio")
    return exe


# ── duration probing (pure parser + ffmpeg -i probe) ─────────────────────────

_DURATION_RE = re.compile(r"Duration:\s*(\d+):([0-5]?\d):([0-5]?\d(?:\.\d+)?)")


def parse_duration(ffmpeg_stderr: str) -> Optional[float]:
    """Seconds from an ``ffmpeg -i`` stderr dump, or None (e.g. 'Duration: N/A').

    Pure and deterministic so it is unit-testable on canned output."""
    if not ffmpeg_stderr:
        return None
    m = _DURATION_RE.search(ffmpeg_stderr)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def probe_duration(path: str) -> float:
    """Input duration in seconds via ``ffmpeg -i`` (no ffprobe). Fail-closed."""
    if not path or not os.path.exists(path):
        raise MediaError(f"input not found: {path!r}")
    exe = _ffmpeg()
    try:
        proc = subprocess.run([exe, "-hide_banner", "-i", path],
                              capture_output=True, stdin=subprocess.DEVNULL,
                              timeout=_PROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise MediaError("ffmpeg probe timed out") from e
    dur = parse_duration(proc.stderr.decode(errors="replace"))
    if dur is None or dur <= 0:
        raise MediaError("could not determine input duration (not an audio file?)")
    return dur


def _guard_duration(path: str, max_seconds: int, what: str = "input") -> float:
    dur = probe_duration(path)
    if dur > max_seconds:
        raise MediaError(f"{what} is {dur:.0f}s — limit is {max_seconds}s for this job")
    return dur


# ── decode (ffmpeg raw-float pipe; never torchaudio IO) ──────────────────────

# Hard ceiling on how much PCM a decode may ever stream, no matter what the
# duration probe claimed: a forged container (or absurd VBR stream) must not be
# able to buffer multi-GB of f32 into RAM. 62 min = the longest job cap (60)
# plus slack for container rounding.
_MAX_DECODE_SECONDS = 62 * 60
_PIPE_CHUNK = 1 << 20


def _read_stream_capped(stream, max_bytes: int, chunk: int = _PIPE_CHUNK) -> bytearray:
    """Read a binary stream to exhaustion, failing closed past max_bytes.

    Reads in bounded chunks so an oversized/forged stream is rejected the
    moment it crosses the cap instead of after buffering it all. Returns a
    bytearray (zero-copy compatible with np.frombuffer)."""
    buf = bytearray()
    while True:
        b = stream.read(chunk)
        if not b:
            return buf
        if len(buf) + len(b) > max_bytes:
            raise MediaError(f"decoded audio stream exceeds the {max_bytes}-byte cap "
                             f"({_MAX_DECODE_SECONDS // 60} min) — rejecting input")
        buf += b


def decode_audio(path: str, *, sr: int = 44100):
    """Decode any audio container to a float32 stereo array.

    Returns (samples, sr) with samples shaped (2, n). Uses the resolved ffmpeg
    binary piping raw f32le (`-f f32le -ac 2 -ar sr -`) — works with the
    bundled imageio-ffmpeg build, which has no ffprobe. The pipe is read in
    chunks against a hard 62-minute byte cap (duration probes can be spoofed;
    the cap cannot), and ffmpeg is killed the moment the cap is crossed."""
    try:
        import numpy as np
    except Exception as e:  # pragma: no cover - numpy is a base dep
        raise MediaBackendUnavailable(f"numpy not installed: {e}") from e
    if not path or not os.path.exists(path):
        raise MediaError(f"input not found: {path!r}")
    exe = _ffmpeg()
    sr = int(sr)
    max_bytes = _MAX_DECODE_SECONDS * sr * 2 * 4  # max samples * 2ch * f32
    cmd = [exe, "-v", "error", "-i", path, "-vn",
           "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "2", "-ar", str(sr), "-"]
    timed_out: list = []
    with tempfile.TemporaryFile() as errf:  # file-backed stderr: no pipe deadlock
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf,
                                stdin=subprocess.DEVNULL)
        watchdog = threading.Timer(_DECODE_TIMEOUT_S,
                                   lambda: (timed_out.append(1), proc.kill()))
        watchdog.start()
        try:
            try:
                raw = _read_stream_capped(proc.stdout, max_bytes)
            except MediaError:
                proc.kill()
                proc.wait()
                raise
            rc = proc.wait()
        finally:
            watchdog.cancel()
            proc.stdout.close()
        if timed_out:
            raise MediaError("audio decode timed out")
        if rc != 0:
            errf.seek(0)
            msg = errf.read().decode(errors="replace")[-400:]
            raise MediaError(f"audio decode failed: {msg}")
    n = len(raw) // 8  # stereo * 4-byte float
    if n <= 0:
        raise MediaError("audio decode produced no samples")
    arr = np.frombuffer(memoryview(raw)[: n * 8], dtype="<f4").reshape(-1, 2).T.copy()
    return arr, sr


# ── limiter (pure, deterministic, unit-tested) ───────────────────────────────

# resample_poly's default kaiser filter spans 10*oversample input samples; pad
# each analysis window well past that so windowed slabs reproduce the one-shot
# oversampled values everywhere in their interior.
_TP_PAD_SAMPLES = 512


def _true_peak_env(x, sr: int, oversample: int, *, window_s: float = 10.0):
    """Per-sample true-peak envelope of (C, n) audio, >= |x| everywhere.

    The oversampled (inter-sample) peak estimate is computed in overlapping
    windows of ~window_s seconds so peak extra RAM stays bounded (~window
    * oversample) instead of materializing a 4x copy of the whole track.
    Each window is padded by _TP_PAD_SAMPLES on both sides — far beyond the
    resampler's filter support — so the result matches the one-shot
    computation. Deterministic."""
    import numpy as np
    from scipy.signal import resample_poly
    C, n = x.shape
    env = np.abs(x).max(axis=0)
    ovs = int(oversample)
    if ovs <= 1 or n < 8:
        return env
    window_n = max(_TP_PAD_SAMPLES * 4, int(round(window_s * sr)))
    for s in range(0, n, window_n):
        e = min(n, s + window_n)
        a = max(0, s - _TP_PAD_SAMPLES)
        b = min(n, e + _TP_PAD_SAMPLES)
        up = resample_poly(x[:, a:b], ovs, 1, axis=-1)
        seg = up[:, (s - a) * ovs: (e - a) * ovs]
        m = seg.shape[-1] // ovs
        peaks = np.abs(seg[:, : m * ovs]).reshape(C, m, ovs).max(axis=-1).max(axis=0)
        env[s:s + m] = np.maximum(env[s:s + m], peaks)
    return env


def limit_peaks(audio, *, sr: int = 44100, ceiling_db: float = -1.0,
                knee_db: float = 2.0, lookahead_ms: float = 1.5,
                oversample: int = 4, tp_window_s: float = 10.0):
    """Look-ahead soft-knee peak limiter with a -1.0 dBFS default ceiling.

    True peaks are estimated on a 4x-oversampled copy (computed in bounded
    ~tp_window_s windows so RAM stays flat on hour-long tracks); the gain
    curve is an infinite-ratio quadratic knee applied to a windowed-max
    envelope (the look-ahead), then linearly smoothed. Deterministic;
    guarantees ``|out| <= ceiling`` for every sample and exact unity gain for
    signals that never enter the knee. Accepts (n,) or (channels, n); shape
    is preserved."""
    import numpy as np
    from scipy.ndimage import maximum_filter1d, uniform_filter1d

    x = np.asarray(audio)
    was_1d = x.ndim == 1
    orig_dtype = x.dtype
    x = np.atleast_2d(x).astype(np.float64)
    if x.ndim != 2 or x.shape[-1] == 0:
        raise MediaError("limiter: expected non-empty (channels, samples) audio")
    n = x.shape[-1]
    ceiling = 10.0 ** (ceiling_db / 20.0)

    # per-sample true-peak estimate (max over channels; >= the sample itself)
    env = _true_peak_env(x, sr, oversample, window_s=tp_window_s)

    win = max(1, int(round(sr * lookahead_ms / 1000.0)))
    if win % 2 == 0:
        win += 1
    env_max = maximum_filter1d(env, size=win) if win > 1 else env

    # infinite-ratio soft knee (quadratic, width knee_db centered on the ceiling)
    knee = max(0.1, float(knee_db))
    ceil_db = 20.0 * np.log10(ceiling)
    lo, hi = ceil_db - knee / 2.0, ceil_db + knee / 2.0
    level_db = 20.0 * np.log10(np.maximum(env_max, 1e-12))
    out_db = np.where(level_db >= hi, ceil_db,
                      level_db - np.square(np.maximum(level_db - lo, 0.0)) / (2.0 * knee))
    gain = np.minimum(10.0 ** ((out_db - level_db) / 20.0), 1.0)

    # smooth with the same centered window (mean of window minima never exceeds
    # the local target, so the ceiling bound survives smoothing), then snap
    # float dust so quiet material passes through bit-exact.
    if win > 1:
        gain = uniform_filter1d(gain, size=win)
    gain[gain > 1.0 - 1e-9] = 1.0

    y = x * gain[None, :]
    np.clip(y, -ceiling, ceiling, out=y)  # belt-and-braces: the hard invariant
    if orig_dtype == np.float32:
        y = y.astype(np.float32)
    return y[0] if was_1d else y


# ── loudness (pyloudnorm) ────────────────────────────────────────────────────

def measure_lufs(audio, sr: int) -> float:
    """ITU-R BS.1770 integrated loudness of (n,) or (channels, n) float audio."""
    import numpy as np
    try:
        import pyloudnorm as pyln
    except Exception as e:
        raise MediaBackendUnavailable(f"pyloudnorm not installed: {e}") from e
    x = np.asarray(audio, dtype=np.float64)
    data = x.T if x.ndim == 2 else x
    if data.shape[0] < int(sr * 0.5):
        raise MediaError("audio too short to measure loudness (< 0.5 s)")
    lufs = float(pyln.Meter(int(sr)).integrated_loudness(data))
    if not np.isfinite(lufs):
        raise MediaError("could not measure loudness (silent input?)")
    return lufs


def normalize_loudness(audio, sr: int, target_lufs: float):
    """Gain the signal so integrated loudness hits target_lufs.

    Returns (scaled_audio, measured_input_lufs). Peak safety is the limiter's
    job — run limit_peaks() after this."""
    import numpy as np
    before = measure_lufs(audio, sr)
    gain = 10.0 ** ((float(target_lufs) - before) / 20.0)
    return np.asarray(audio, dtype=np.float64) * gain, before


# ── chunked overlap-add inference (model-agnostic, unit-tested) ──────────────

def chunked_apply(x, sr: int, apply_fn, *, chunk_s: float = _STEM_CHUNK_SECONDS,
                  overlap_s: float = _STEM_OVERLAP_SECONDS, progress: Progress = None,
                  p_lo: float = 0.0, p_hi: float = 100.0):
    """Run ``apply_fn`` over (channels, n) audio in overlapping chunks.

    apply_fn maps a (C, m) window to (K, C, m) source stack; windows are blended
    with complementary linear crossfades over the overlap and the weight sum is
    normalized out, so a passthrough apply_fn reconstructs the input exactly.
    This is what lets a 20-minute song run through HDemucs in ~10 s slices."""
    import numpy as np
    x = np.asarray(x, dtype=np.float32)
    if x.ndim != 2 or x.shape[-1] == 0:
        raise MediaError("chunked_apply: expected non-empty (channels, samples) audio")
    C, n = x.shape
    chunk_n = max(1, int(round(chunk_s * sr)))
    overlap_n = max(0, min(int(round(overlap_s * sr)), chunk_n // 2))
    hop = max(1, chunk_n - overlap_n)
    starts = [0]
    while starts[-1] + chunk_n < n:
        starts.append(starts[-1] + hop)
    last = len(starts) - 1

    out = None
    wsum = np.zeros(n, dtype=np.float64)
    for idx, s in enumerate(starts):
        e = min(n, s + chunk_n)
        y = np.asarray(apply_fn(x[:, s:e]), dtype=np.float32)
        if y.ndim != 3 or y.shape[1] != C or y.shape[2] != e - s:
            raise MediaError(f"separation model returned shape {y.shape}, "
                             f"expected (K, {C}, {e - s})")
        if out is None:
            out = np.zeros((y.shape[0], C, n), dtype=np.float64)
        elif y.shape[0] != out.shape[0]:
            raise MediaError("separation model changed source count between chunks")
        w = np.ones(e - s, dtype=np.float64)
        m = overlap_n
        if m:
            ramp = np.arange(1, m + 1, dtype=np.float64) / (m + 1)  # never 0 → wsum > 0
            if idx > 0:
                w[:m] = ramp
            if idx < last:
                w[-m:] = w[-m:] * (1.0 - ramp)
        out[:, :, s:e] += y.astype(np.float64) * w
        wsum[s:e] += w
        _p(progress, p_lo + (p_hi - p_lo) * (idx + 1) / len(starts),
           f"chunk {idx + 1}/{len(starts)}")
    return (out / np.maximum(wsum, 1e-9)[None, None, :]).astype(np.float32)


# ── reference spectral match (Welch bands → clamped gains → FIR) ─────────────

def band_spectrum_db(mono, sr: int, *, n_bands: int = 31,
                     fmin: float = 50.0, fmax: float = 16000.0):
    """Median Welch spectrum of a mono signal folded into log-spaced bands.

    Returns (centers_hz, band_db) with n_bands entries each."""
    import numpy as np
    from scipy.signal import welch
    x = np.asarray(mono, dtype=np.float64).ravel()
    if x.size < 256:
        raise MediaError("audio too short for spectrum analysis")
    f, p = welch(x, fs=int(sr), nperseg=min(4096, x.size), average="median")
    hi = min(float(fmax), sr / 2.0 * 0.98)
    edges = np.geomspace(float(fmin), hi, int(n_bands) + 1)
    band_db = np.empty(int(n_bands), dtype=np.float64)
    for i in range(int(n_bands)):
        m = (f >= edges[i]) & (f < edges[i + 1])
        power = float(p[m].mean()) if m.any() else 0.0
        band_db[i] = 10.0 * np.log10(max(power, 1e-20))
    centers = np.sqrt(edges[:-1] * edges[1:])
    return centers, band_db


def band_gains_db(program_db, reference_db, *, clamp_db: float = 9.0):
    """Per-band correction gains: reference − program, clamped to ±clamp_db."""
    import numpy as np
    prog = np.asarray(program_db, dtype=np.float64)
    ref = np.asarray(reference_db, dtype=np.float64)
    if prog.shape != ref.shape:
        raise MediaError("program/reference band spectra differ in shape")
    return np.clip(ref - prog, -abs(clamp_db), abs(clamp_db))


def design_match_fir(centers_hz, gains_db, sr: int, *, numtaps: int = 2049):
    """Linear-phase FIR realizing the per-band gain curve (scipy firwin2)."""
    import numpy as np
    from scipy.signal import firwin2
    numtaps = int(numtaps) | 1  # firwin2 needs odd taps for nonzero Nyquist gain
    nyq = sr / 2.0
    centers = np.asarray(centers_hz, dtype=np.float64) / nyq
    gains = 10.0 ** (np.asarray(gains_db, dtype=np.float64) / 20.0)
    keep = (centers > 0.0) & (centers < 0.99)
    centers, gains = centers[keep], gains[keep]
    if centers.size == 0:
        raise MediaError("no usable bands for spectral match")
    freq = np.concatenate(([0.0], centers, [1.0]))
    gain = np.concatenate(([gains[0]], gains, [gains[-1]]))
    return firwin2(numtaps, freq, gain)


def apply_fir(x, taps):
    """Apply a linear-phase FIR to (channels, n) audio once (fftconvolve) and
    trim the group delay, so the output stays sample-aligned with the input."""
    import numpy as np
    from scipy.signal import fftconvolve
    a = np.atleast_2d(np.asarray(x, dtype=np.float64))
    t = np.asarray(taps, dtype=np.float64).ravel()
    n = a.shape[-1]
    delay = (t.size - 1) // 2
    y = fftconvolve(a, t[None, :], mode="full", axes=-1)[:, delay:delay + n]
    return y[0] if np.asarray(x).ndim == 1 else y


# ── encoding (wav via scipy, mp3 via ffmpeg libmp3lame w/ wav fallback) ──────

_LAME_OK: Optional[bool] = None


def _mp3_encoder_available() -> bool:
    """True iff the resolved ffmpeg lists the libmp3lame encoder. Cached."""
    global _LAME_OK
    if _LAME_OK is None:
        try:
            proc = subprocess.run([_ffmpeg(), "-hide_banner", "-encoders"],
                                  capture_output=True, stdin=subprocess.DEVNULL,
                                  timeout=_PROBE_TIMEOUT_S)
            _LAME_OK = proc.returncode == 0 and b"libmp3lame" in proc.stdout
        except Exception:
            _LAME_OK = False
    return _LAME_OK


def _head_magic(path: str, expect: str) -> bool:
    try:
        with open(path, "rb") as f:
            return validate_magic(f.read(4096), expect)
    except OSError:
        return False


def _file_sha3(path: str) -> str:
    """Streaming sha3_256 hexdigest — identical to base.sha3_hex(file bytes)."""
    import hashlib
    h = hashlib.sha3_256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_wav(audio, sr: int, path: str) -> None:
    """16-bit PCM WAV via scipy.io.wavfile; input float (n,) or (channels, n)."""
    import numpy as np
    from scipy.io import wavfile
    a = np.atleast_2d(np.asarray(audio, dtype=np.float64))
    if a.size == 0:
        raise MediaError("no audio samples to encode")
    ints = np.round(np.clip(a.T, -1.0, 1.0) * 32767.0).astype("<i2")
    wavfile.write(path, int(sr), ints)


def _encode_audio_file(audio, sr: int, out_dir: str, stem: str, fmt: str):
    """Encode float audio into out_dir/<stem>.<ext>.

    Returns (path, mime, actual_fmt). ``fmt='mp3'`` uses ffmpeg libmp3lame at
    320k when available, else transparently delivers WAV (caller records
    meta.fallback). Encodes in a scratch dir and moves the finished file, so
    out_dir never holds a partial artifact."""
    fmt = (fmt or "mp3").strip().lower()
    if fmt not in ("mp3", "wav"):
        raise MediaError(f"unsupported audio format {fmt!r} (mp3|wav)")
    use_mp3 = fmt == "mp3" and _mp3_encoder_available()
    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="anmenc_") as td:
        if use_mp3:
            src = os.path.join(td, "src.wav")
            _write_wav(audio, sr, src)
            tmp = os.path.join(td, f"{stem}.mp3")
            try:
                proc = subprocess.run(
                    [_ffmpeg(), "-y", "-v", "error", "-i", src,
                     "-codec:a", "libmp3lame", "-b:a", "320k", tmp],
                    capture_output=True, stdin=subprocess.DEVNULL,
                    timeout=_ENCODE_TIMEOUT_S)
            except subprocess.TimeoutExpired as e:
                raise MediaError("mp3 encode timed out") from e
            if proc.returncode != 0 or not os.path.exists(tmp):
                raise MediaError(
                    f"mp3 encode failed: {proc.stderr.decode(errors='replace')[-300:]}")
            if not _head_magic(tmp, "mp3"):
                raise MediaError("encoded output is not a valid MP3")
            mime, actual = "audio/mpeg", "mp3"
        else:
            tmp = os.path.join(td, f"{stem}.wav")
            _write_wav(audio, sr, tmp)
            if not _head_magic(tmp, "wav"):
                raise MediaError("encoded output is not a valid WAV")
            mime, actual = "audio/wav", "wav"
        final = os.path.join(out_dir, os.path.basename(tmp))
        shutil.move(tmp, final)
    return final, mime, actual


# ── GPU helpers (audio_gen.py pattern) ───────────────────────────────────────

def _cuda_on() -> bool:
    if os.environ.get("ANIMICA_AUDIO_FORCE_CPU", "") in ("1", "true"):
        return False
    try:
        import torch
    except Exception:
        return False
    return bool(torch.cuda.is_available())


def _is_gpu_error(e: Exception) -> bool:
    s = str(e).lower()
    return isinstance(e, RuntimeError) or "out of memory" in s or "cuda" in s or "cublas" in s


def _reclaim_gpu() -> None:
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:
        pass


# ── stem separation (HDemucs) ────────────────────────────────────────────────

def _demucs_sources(mix, sr: int, bundle, device: str,
                    progress: Progress) -> Dict[str, "object"]:
    """Run HDemucs over the (2, n) mix in overlap-add chunks on `device`.

    Returns {source_name: (2, n) float32 array} in the mix's scale."""
    import numpy as np
    import torch
    model = bundle.get_model().to(device).eval()
    names = [str(s) for s in getattr(model, "sources", _DEFAULT_SOURCES)]

    # standard demucs conditioning: zero-mean / unit-std on the mono reference
    ref = mix.mean(axis=0)
    mean, std = float(ref.mean()), float(ref.std()) + 1e-8
    normed = ((mix - mean) / std).astype(np.float32)

    def apply_fn(seg):
        t = torch.from_numpy(np.ascontiguousarray(seg)).to(device).unsqueeze(0)
        with torch.no_grad():
            y = model(t)[0]
        return y.cpu().numpy()

    out = chunked_apply(normed, sr, apply_fn, progress=progress, p_lo=15.0, p_hi=85.0)
    if out.shape[0] != len(names):
        raise MediaError(f"model produced {out.shape[0]} sources, expected {len(names)}")
    out = out * std + mean
    return {names[i]: out[i] for i in range(len(names))}


def separate_stems(input_path: str, out_dir: str, *, fmt: str = "mp3",
                   two_stem: bool = False, progress: Progress = None) -> dict:
    """audio_stems / audio_isolate: split a song into stems with HDemucs.

    two_stem=False → zip of drums/bass/other/vocals; two_stem=True → zip of
    {vocals, instrumental=mix−vocals}. Inputs are capped at 20 minutes. Runs on
    CUDA when available with one CPU retry on GPU failure (VRAM reclaimed
    first). Returns {"path", "mime", "sha3", "meta"}."""
    import numpy as np
    _p(progress, 1, "probing input")
    dur = _guard_duration(input_path, STEMS_MAX_SECONDS)
    try:
        from torchaudio.pipelines import HDEMUCS_HIGH_MUSDB_PLUS as bundle
    except Exception as e:
        raise MediaBackendUnavailable(f"torchaudio not installed: {e}") from e
    sr = int(bundle.sample_rate)
    mix, _ = decode_audio(input_path, sr=sr)
    _p(progress, 10, "loading HDemucs")

    cuda = _cuda_on()
    device = "cuda" if cuda else "cpu"
    try:
        sources = _demucs_sources(mix, sr, bundle, device, progress)
    except MediaError:
        raise
    except Exception as e:
        if cuda and _is_gpu_error(e):
            _reclaim_gpu()
            device = "cpu"
            try:
                sources = _demucs_sources(mix, sr, bundle, device, progress)
            except Exception as e2:
                raise MediaError(f"stem separation failed (gpu+cpu): {e2}") from e2
        else:
            raise MediaError(f"stem separation failed: {e}") from e

    if two_stem:
        vocals = sources.get("vocals")
        if vocals is None:
            raise MediaError("separation model produced no vocals source")
        tracks = {"vocals": vocals, "instrumental": mix.astype(np.float64) - vocals}
        zip_name = "vocals_instrumental.zip"
    else:
        tracks = sources
        zip_name = "stems.zip"

    _p(progress, 88, "encoding stems")
    fmt_req = (fmt or "mp3").strip().lower()
    actual_fmt = fmt_req
    with tempfile.TemporaryDirectory(prefix="anmstems_") as td:
        files = []
        for i, (name, arr) in enumerate(tracks.items()):
            path, _, actual_fmt = _encode_audio_file(arr, sr, td, name, fmt_req)
            files.append(path)
            _p(progress, 88 + 10 * (i + 1) / len(tracks), f"encoded {name}")
        ztmp = os.path.join(td, zip_name)
        with zipfile.ZipFile(ztmp, "w", zipfile.ZIP_DEFLATED) as z:
            for path in files:
                z.write(path, arcname=os.path.basename(path))
        if not _head_magic(ztmp, "zip"):
            raise MediaError("stem archive is not a valid zip")
        sha3 = _file_sha3(ztmp)
        os.makedirs(out_dir, exist_ok=True)
        final = os.path.join(out_dir, zip_name)
        shutil.move(ztmp, final)

    meta = {"stems": list(tracks), "format": actual_fmt, "sample_rate": sr,
            "duration_s": round(dur, 2), "device": device,
            "model": "HDEMUCS_HIGH_MUSDB_PLUS"}
    if fmt_req == "mp3" and actual_fmt == "wav":
        meta["fallback"] = "wav"
    _p(progress, 100, "done")
    return {"path": final, "mime": "application/zip", "sha3": sha3, "meta": meta}


# ── enhancement (denoise + high-pass + loudness + limiter) ───────────────────

def _highpass(x, sr: int, hz: float = 40.0, order: int = 4):
    """Zero-phase 40 Hz butterworth high-pass (rumble / DC removal)."""
    from scipy.signal import butter, sosfiltfilt
    sos = butter(order, float(hz), btype="highpass", fs=int(sr), output="sos")
    return sosfiltfilt(sos, x, axis=-1)


def enhance_audio(input_path: str, out_dir: str, *, denoise: bool = True,
                  loudness: float = -16.0, fmt: str = "mp3",
                  progress: Progress = None) -> dict:
    """audio_enhance: spectral denoise + 40 Hz high-pass + LUFS + limiter.

    `loudness` is the integrated-loudness target (clamped to −30..−6 LUFS).
    Inputs capped at 60 minutes. Returns {"path", "mime", "sha3", "meta"}."""
    import numpy as np
    _p(progress, 1, "probing input")
    dur = _guard_duration(input_path, ENHANCE_MAX_SECONDS)
    target = float(np.clip(float(loudness), -30.0, -6.0))
    x, sr = decode_audio(input_path)
    _p(progress, 15, "decoded")

    if denoise:
        try:
            import noisereduce as nr
        except Exception as e:
            raise MediaBackendUnavailable(f"noisereduce not installed: {e}") from e
        try:
            x = np.asarray(nr.reduce_noise(y=x, sr=sr, stationary=False),
                           dtype=np.float64)
        except Exception as e:
            raise MediaError(f"denoise failed: {e}") from e
        _p(progress, 55, "denoised")

    x = _highpass(x, sr)
    _p(progress, 65, "high-passed")
    x, before = normalize_loudness(x, sr, target)
    x = limit_peaks(x, sr=sr)
    after = measure_lufs(x, sr)
    _p(progress, 80, "loudness normalized")

    path, mime, actual = _encode_audio_file(x, sr, out_dir, "enhanced", fmt)
    meta = {"duration_s": round(dur, 2), "sample_rate": sr, "denoise": bool(denoise),
            "target_lufs": target, "input_lufs": round(before, 2),
            "output_lufs": round(after, 2), "format": actual}
    if (fmt or "mp3").strip().lower() == "mp3" and actual == "wav":
        meta["fallback"] = "wav"
    _p(progress, 100, "done")
    return {"path": path, "mime": mime, "sha3": _file_sha3(path), "meta": meta}


# ── mastering (reference spectral match + LUFS preset + limiter) ─────────────

def master_audio(input_path: str, out_dir: str, *, preset: str = "streaming",
                 reference_path: Optional[str] = None, fmt: str = "mp3",
                 progress: Progress = None) -> dict:
    """audio_master: optional reference spectral match, then LUFS preset + limiter.

    Presets: streaming −14 LUFS, loud −9, podcast −16. With reference_path the
    program's 31-band median Welch spectrum is matched to the reference's via a
    ±9 dB-clamped linear-phase FIR before loudness. Inputs capped at 60 min.
    Returns {"path", "mime", "sha3", "meta"}."""
    key = (preset or "streaming").strip().lower()
    if key not in MASTER_PRESETS:
        raise MediaError(f"unknown master preset {preset!r} (streaming|loud|podcast)")
    target = MASTER_PRESETS[key]

    _p(progress, 1, "probing input")
    dur = _guard_duration(input_path, MASTER_MAX_SECONDS)
    x, sr = decode_audio(input_path)
    _p(progress, 15, "decoded")

    matched = False
    gains = None
    if reference_path:
        _guard_duration(reference_path, MASTER_MAX_SECONDS, what="reference")
        ref, _ = decode_audio(reference_path, sr=sr)
        _p(progress, 30, "analyzing spectra")
        centers, prog_db = band_spectrum_db(x.mean(axis=0), sr)
        _, ref_db = band_spectrum_db(ref.mean(axis=0), sr)
        gains = band_gains_db(prog_db, ref_db)
        x = apply_fir(x, design_match_fir(centers, gains, sr))
        matched = True
        _p(progress, 55, "spectrum matched")

    x, before = normalize_loudness(x, sr, target)
    x = limit_peaks(x, sr=sr)
    after = measure_lufs(x, sr)
    _p(progress, 80, "loudness set")

    path, mime, actual = _encode_audio_file(x, sr, out_dir, "mastered", fmt)
    meta = {"duration_s": round(dur, 2), "sample_rate": sr, "preset": key,
            "target_lufs": target, "input_lufs": round(before, 2),
            "output_lufs": round(after, 2), "format": actual,
            "reference_match": matched}
    if matched and gains is not None:
        meta["band_gains_db"] = [round(float(g), 2) for g in gains]
    if (fmt or "mp3").strip().lower() == "mp3" and actual == "wav":
        meta["fallback"] = "wav"
    _p(progress, 100, "done")
    return {"path": path, "mime": mime, "sha3": _file_sha3(path), "meta": meta}
