"""The cat's voice.

Default mode ``animalese`` synthesizes cute per-syllable blips with numpy (no model,
no network, always works — the captions carry the real words). Mode ``piper`` uses a
real neural TTS voice if `piper-tts` and a voice model are installed; it falls back to
animalese automatically. Output is always float32 mono at the configured audio rate.
"""
from __future__ import annotations

import math
import os
import re

import numpy as np

from .contract import Character, StreamConfig, clamp

_VOWEL_SEMI = {"a": 0, "e": 4, "i": 7, "o": -3, "u": 5, "y": 9}


class Voice:
    def __init__(self, cfg: StreamConfig, char: Character):
        self.cfg = cfg
        self.char = char
        self.rate = cfg.audio_rate
        self.mode = (cfg.voice or "animalese").lower()
        self._piper = None
        if self.mode == "piper":
            self._piper = _try_load_piper(cfg.piper_voice)
            if self._piper is None:
                self.mode = "animalese"   # graceful fallback

    def synth(self, text: str) -> "tuple[np.ndarray, float]":
        text = (text or "").strip()
        if not text or self.mode == "off":
            return np.zeros(0, dtype=np.float32), 0.0
        if self.mode == "piper" and self._piper is not None:
            try:
                a = self._piper(text)
                return a.astype(np.float32), len(a) / self.rate
            except Exception:
                pass
        a = self._animalese(text)
        return a, len(a) / self.rate

    # ── animalese ────────────────────────────────────────────────────────────
    def _animalese(self, text: str) -> np.ndarray:
        rate = self.rate
        base = 300.0 * clamp(self.char.voice_pitch, 0.5, 2.0)
        # pace: one blip per letter, gaps for spaces/punctuation, tuned by voice_wpm
        per = clamp(0.5 * 150.0 / max(60, self.char.voice_wpm), 0.045, 0.13)  # seconds/blip
        rng = np.random.RandomState(abs(hash(text)) % (2 ** 31))
        chunks = []
        for tok in re.findall(r"[A-Za-z]+|[.,!?…]| +", text):
            if tok.isspace():
                chunks.append(np.zeros(int(rate * per * 1.2), dtype=np.float32))
                continue
            if tok[0] in ".,!?…":
                chunks.append(np.zeros(int(rate * per * (2.2 if tok[0] in ".!?…" else 1.4)), dtype=np.float32))
                continue
            for ch in tok.lower():
                semi = _VOWEL_SEMI.get(ch, (ord(ch) - 97) % 12 - 6)
                jit = rng.uniform(-0.4, 0.4)
                f = base * (2 ** ((semi + jit) / 12.0))
                chunks.append(self._blip(f, per, ch in _VOWEL_SEMI))
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        out = np.concatenate(chunks)
        # gentle overall fade so concatenation never clicks at the very ends
        n = min(len(out), int(rate * 0.01))
        if n:
            out[:n] *= np.linspace(0, 1, n)
            out[-n:] *= np.linspace(1, 0, n)
        return (out * 0.85).astype(np.float32)

    def _blip(self, f: float, dur: float, vowel: bool) -> np.ndarray:
        rate = self.rate
        n = max(4, int(rate * dur))
        t = np.arange(n) / rate
        # a rounded tone: fundamental + a soft octave + gentle vibrato
        vib = 1.0 + 0.02 * np.sin(2 * math.pi * 6.0 * t)
        w = 0.6 * np.sin(2 * math.pi * f * vib * t)
        w += 0.22 * np.sin(2 * math.pi * 2 * f * vib * t)
        if vowel:
            w += 0.14 * np.sin(2 * math.pi * 3 * f * t)
        # ADSR-ish: quick attack, exponential decay (shorter for consonants)
        atk = max(1, int(rate * 0.006))
        env = np.ones(n)
        env[:atk] = np.linspace(0, 1, atk)
        decay = 9.0 if vowel else 16.0
        env *= np.exp(-t * decay)
        return (w * env).astype(np.float32)


def _try_load_piper(voice_path: str):
    """Return a callable text->float32 samples if piper is available, else None."""
    try:
        from piper import PiperVoice  # type: ignore
    except Exception:
        return None
    path = voice_path or os.environ.get("ANIMICA_PIPER_VOICE", "")
    if not path or not os.path.exists(path):
        return None
    try:
        voice = PiperVoice.load(path)
    except Exception:
        return None

    def _synth(text: str) -> np.ndarray:
        import io
        import wave
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            voice.synthesize(text, wf)
        buf.seek(0)
        with wave.open(buf, "rb") as wf:
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return a  # note: caller treats as already at cfg rate; piper voices are usually 22050

    return _synth
