"""The cat's autonomous behavior + animation state machine.

`BehaviorEngine.tick(dt, state, mouth_rms)` mutates an :class:`AnimalState` in place
every frame: it wanders the stage, does quirky things (zoomies, tail-chase, groom,
nap, pounce, stretch, peek, sing), blinks, wags its tail and twitches its ears. When
the brain has the cat speaking, `set_speaking()` overrides it: the cat faces forward,
holds still-ish and lip-syncs to the live voice RMS.
"""
from __future__ import annotations

import math
import random

from .contract import AnimalState, Character, StreamConfig, clamp, ease

# behavior -> (min_dur, max_dur, weight, prop, thought)
_BEHAVIORS = {
    "idle":      (2.5, 5.0, 5, "", ""),
    "wander":    (3.0, 6.0, 5, "", ""),
    "sit":       (2.0, 4.0, 3, "", ""),
    "groom":     (2.5, 4.5, 3, "", "grooming…"),
    "tailchase": (2.0, 3.5, 2, "sparkle", "chasing its tail!"),
    "zoomies":   (2.0, 3.5, 2, "sparkle", "ZOOMIES!"),
    "pounce":    (1.2, 1.8, 2, "", "pounce!"),
    "stretch":   (1.8, 2.6, 2, "", "big stretch~"),
    "nap":       (4.0, 7.0, 2, "zzz", "napping…"),
    "peek":      (1.5, 2.5, 1, "?", "what's that?"),
}


class BehaviorEngine:
    def __init__(self, cfg: StreamConfig, char: Character, stage_bounds, seed: int = 1234):
        self.cfg = cfg
        self.char = char
        self.xmin, self.xmax = stage_bounds
        self.rng = random.Random(seed)
        self.t = 0.0
        self.behavior = "idle"
        self.beh_end = 2.0
        self.target_x = 0.35
        self.blink_at = 2.0
        self.blink_t = -1.0
        self.speaking = False
        self.speak_emotion = char.default_emotion
        self.speak_behavior = "react"
        self._mouth = 0.0

    # ── brain hooks ──────────────────────────────────────────────────────────
    def set_speaking(self, speaking: bool, emotion: str = "", behavior: str = ""):
        self.speaking = speaking
        if speaking:
            self.speak_emotion = emotion or self.char.default_emotion
            self.speak_behavior = behavior or "react"
            # stop wandering; settle near center-left where the subtitle reads well
            self.behavior = self.speak_behavior if self.speak_behavior in _BEHAVIORS else "sit"
            self.beh_end = self.t + 999

    def _pick(self):
        keys = list(_BEHAVIORS)
        weights = [_BEHAVIORS[k][2] for k in keys]
        b = self.rng.choices(keys, weights=weights, k=1)[0]
        mn, mx = _BEHAVIORS[b][:2]
        self.behavior = b
        self.beh_end = self.t + self.rng.uniform(mn, mx)
        if b in ("wander", "zoomies"):
            self.target_x = self.rng.uniform(self.xmin, self.xmax)

    # ── per-frame update ─────────────────────────────────────────────────────
    def tick(self, dt: float, s: AnimalState, mouth_rms: float) -> None:
        self.t += dt
        # smooth the lip-sync a touch so the mouth doesn't chatter
        self._mouth += (clamp(mouth_rms * 1.6) - self._mouth) * min(1.0, dt * 18)

        # blink scheduler (independent of behavior)
        if self.blink_t >= 0:
            self.blink_t += dt
            s.eye = 0.0 if self.blink_t < 0.07 else clamp(self.blink_t / 0.14)
            if self.blink_t >= 0.14:
                self.blink_t = -1.0
        elif self.t >= self.blink_at:
            self.blink_t = 0.0
            self.blink_at = self.t + self.rng.uniform(2.2, 5.5)
        else:
            s.eye = 1.0

        # continuous tail wag + subtle ear
        s.tail = math.sin(self.t * (5.0 if self.speaking else 2.4))
        s.ear = 0.35 * math.sin(self.t * 1.3 + 1.0)

        if self.speaking:
            self._drive_speaking(dt, s)
            return

        if self.t >= self.beh_end:
            self._pick()
        prop, thought = _BEHAVIORS[self.behavior][3], _BEHAVIORS[self.behavior][4]
        s.prop, s.thought = prop, thought
        s.emotion = self.char.default_emotion
        s.mouth = max(self._mouth, 0.0)
        s.scale = 1.0
        s.lean = 0.0
        getattr(self, f"_do_{self.behavior}", self._do_idle)(dt, s)

    # ── per-behavior motion ──────────────────────────────────────────────────
    def _approach(self, s: AnimalState, speed: float, dt: float) -> bool:
        dx = self.target_x - s.x
        if abs(dx) < 0.004:
            return True
        step = math.copysign(min(abs(dx), speed * dt), dx)
        s.x = clamp(s.x + step, self.xmin, self.xmax)
        s.facing = 1 if dx > 0 else -1
        return False

    def _do_idle(self, dt, s):
        s.bob = 0.06 * math.sin(self.t * 1.8)
        s.emotion = "neutral" if self.rng.random() < 0.5 else self.char.default_emotion

    def _do_sit(self, dt, s):
        s.bob = 0.02 * math.sin(self.t * 1.2)
        s.scale = 0.97

    def _do_wander(self, dt, s):
        reached = self._approach(s, 0.16, dt)
        s.bob = 0.16 * abs(math.sin(self.t * 7.0))       # walk cycle
        s.emotion = "curious"
        if reached:
            self.beh_end = min(self.beh_end, self.t + 0.2)

    def _do_zoomies(self, dt, s):
        reached = self._approach(s, 0.55, dt)
        s.bob = 0.30 * abs(math.sin(self.t * 15.0))
        s.lean = 0.6 * s.facing
        s.emotion = "excited"
        if reached:
            self.target_x = self.rng.uniform(self.xmin, self.xmax)

    def _do_groom(self, dt, s):
        s.bob = -0.04 + 0.03 * math.sin(self.t * 6.0)
        s.emotion = "happy"
        s.mouth = 0.15 + 0.15 * abs(math.sin(self.t * 8.0))

    def _do_tailchase(self, dt, s):
        s.facing = 1 if int(self.t * 6) % 2 == 0 else -1     # spin illusion
        s.bob = 0.12 * abs(math.sin(self.t * 12.0))
        s.emotion = "excited"

    def _do_pounce(self, dt, s):
        ph = clamp((self.t - (self.beh_end - 1.6)) / 1.6)
        s.bob = math.sin(ease(ph) * math.pi) * 0.6
        s.lean = 0.4
        s.emotion = "surprised" if ph > 0.4 else "curious"

    def _do_stretch(self, dt, s):
        ph = clamp((self.t - (self.beh_end - 2.2)) / 2.2)
        s.scale = 1.0 + 0.12 * math.sin(ph * math.pi)
        s.emotion = "sleepy"

    def _do_nap(self, dt, s):
        s.eye = min(s.eye, 0.12)
        s.bob = -0.05 + 0.03 * math.sin(self.t * 1.1)
        s.scale = 0.95
        s.emotion = "sleepy"
        s.mouth = 0.0

    def _do_peek(self, dt, s):
        s.emotion = "curious"
        s.bob = 0.05 * math.sin(self.t * 3.0)
        s.facing = 1 if math.sin(self.t * 2.0) > 0 else -1

    def _drive_speaking(self, dt, s):
        s.emotion = self.speak_emotion
        s.mouth = self._mouth
        s.facing = 1
        if self.speak_behavior == "sing":
            s.bob = 0.18 * math.sin(self.t * 6.0)
            s.prop = "note"
        else:
            s.bob = 0.05 * math.sin(self.t * 3.0)
            s.prop = ""
        s.thought = ""
