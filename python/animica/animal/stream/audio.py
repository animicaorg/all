"""Continuous audio mixer for the stream.

Produces an unbroken interleaved s16le stereo PCM stream (one chunk per video frame)
by looping a background music bed and ducking in queued voice clips. Exposes the
currently-playing clip's metadata (so the pipeline can drive the subtitle + behavior)
and the live voice RMS (so the renderer can lip-sync the mouth).
"""
from __future__ import annotations

import math
import subprocess

import numpy as np

from .contract import Character, StreamConfig, clamp


class AudioMixer:
    def __init__(self, cfg: StreamConfig, char: Character):
        self.cfg = cfg
        self.rate = cfg.audio_rate
        self.bed = self._load_bed()               # (N,2) float32, looped
        self.bed_pos = 0
        self._queue: list = []                    # list[(np.ndarray mono, dict meta)]
        self._cur = None                          # (samples, meta)
        self._vp = 0
        self.last_voice_rms = 0.0
        self._duck = 0.85                          # music gain, eased toward target

    # ── public API ───────────────────────────────────────────────────────────
    def enqueue(self, samples: np.ndarray, meta: dict) -> None:
        if samples is None or len(samples) == 0:
            # still surface the line briefly so the subtitle shows even with voice off
            samples = np.zeros(int(self.rate * clamp(len(meta.get("text", "")) * 0.05, 0.8, 6.0)),
                               dtype=np.float32)
        self._queue.append((samples.astype(np.float32), meta))

    @property
    def current_meta(self):
        return self._cur[1] if self._cur else None

    @property
    def speaking(self) -> bool:
        return self._cur is not None

    @property
    def backlog(self) -> int:
        return len(self._queue) + (1 if self._cur else 0)

    def read(self, nframes: int) -> bytes:
        """Return `nframes` interleaved stereo s16le samples as bytes."""
        # background bed (wrap-around slice)
        music = self._bed_slice(nframes)
        # advance/attach a voice clip
        if self._cur is None and self._queue:
            self._cur, self._vp = self._queue.pop(0), 0
        voice = np.zeros(nframes, dtype=np.float32)
        if self._cur is not None:
            samp = self._cur[0]
            end = min(self._vp + nframes, len(samp))
            n = end - self._vp
            if n > 0:
                voice[:n] = samp[self._vp:end]
            self._vp = end
            if self._vp >= len(samp):
                self._cur = None
        # lip-sync RMS from the voice in this chunk
        rms = float(np.sqrt(np.mean(voice ** 2))) if voice.size else 0.0
        self.last_voice_rms = clamp(rms * 3.2)
        # duck the music toward a target depending on whether voice is present
        target = 0.32 if self._cur is not None else 0.85
        self._duck += (target - self._duck) * 0.08
        mix = music * self._duck
        mix[:, 0] += voice * 0.92
        mix[:, 1] += voice * 0.92
        np.clip(mix, -1.0, 1.0, out=mix)
        return (mix * 32767.0).astype("<i2").tobytes()

    # ── bed ──────────────────────────────────────────────────────────────────
    def _bed_slice(self, nframes: int) -> np.ndarray:
        if self.bed is None or len(self.bed) == 0:
            return np.zeros((nframes, 2), dtype=np.float32)
        N = len(self.bed)
        idx = (np.arange(self.bed_pos, self.bed_pos + nframes) % N)
        self.bed_pos = int((self.bed_pos + nframes) % N)
        return self.bed[idx].copy()

    def _load_bed(self):
        m = (self.cfg.music or "auto").strip()
        if m == "off":
            return None
        if m and m != "auto":
            dec = _decode_audio(m, self.rate)
            if dec is not None and len(dec):
                return dec * 0.5
        return _synth_lofi(self.rate)


# ── background music synthesis (loopable lofi bed) ─────────────────────────────
def _synth_lofi(rate: int) -> np.ndarray:
    bpm = 72.0
    beat = 60.0 / bpm
    chord_len = beat * 4                        # one chord per bar
    # Cmaj7 - Am7 - Fmaj7 - G7 (cozy loop), midi roots
    prog = [
        [60, 64, 67, 71],
        [57, 60, 64, 67],
        [53, 57, 60, 64],
        [55, 59, 62, 65],
    ]
    def midi_hz(m):
        return 440.0 * 2 ** ((m - 69) / 12.0)
    bars = []
    for ci, chord in enumerate(prog):
        n = int(rate * chord_len)
        t = np.arange(n) / rate
        sig = np.zeros(n)
        for j, m in enumerate(chord):
            f = midi_hz(m - 12)                 # an octave down = warmer pad
            partial = 0.5 * np.sin(2 * math.pi * f * t) + 0.18 * np.sin(2 * math.pi * 2 * f * t)
            sig += partial * (0.9 - 0.12 * j)
        # slow tremolo + soft attack/release per bar
        sig *= 1.0 + 0.06 * np.sin(2 * math.pi * 0.5 * t)
        env = np.clip(np.minimum(t / 0.25, (chord_len - t) / 0.4), 0, 1)
        sig *= env
        # gentle kick on each beat
        for b in range(4):
            k0 = int(b * beat * rate)
            kl = int(0.12 * rate)
            kt = np.arange(kl) / rate
            kick = np.sin(2 * math.pi * (90 * np.exp(-kt * 20)) * kt) * np.exp(-kt * 22) * 0.5
            sig[k0:k0 + kl] += kick[:max(0, min(kl, n - k0))][:max(0, min(kl, n - k0))]
        bars.append(sig)
    mono = np.concatenate(bars)
    # quiet vinyl-ish noise for texture
    rng = np.random.RandomState(7)
    mono += rng.randn(len(mono)) * 0.008
    mono /= (np.max(np.abs(mono)) + 1e-6)
    mono *= 0.22
    # tiny stereo width
    st = np.stack([mono, np.roll(mono, 60)], axis=1).astype(np.float32)
    # equal-power crossfade the loop seam
    x = int(rate * 0.15)
    if x * 2 < len(st):
        fade = np.linspace(0, 1, x)[:, None]
        st[:x] = st[:x] * fade + st[-x:][::-1] * (1 - fade)
    return st


def _decode_audio(path: str, rate: int):
    try:
        from animica.media.base import resolve_ffmpeg
        ff = resolve_ffmpeg()
        if not ff:
            return None
        out = subprocess.run(
            [ff, "-v", "error", "-i", path, "-ac", "2", "-ar", str(rate), "-f", "f32le", "pipe:1"],
            capture_output=True, timeout=60).stdout
        if not out:
            return None
        return np.frombuffer(out, dtype=np.float32).reshape(-1, 2).copy()
    except Exception:
        return None
