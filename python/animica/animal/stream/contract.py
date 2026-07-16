"""Shared contract for the Animica Animal 24/7 livestream pipeline.

Every module in :mod:`animica.animal.stream` codes against these types so the renderer,
behavior engine, voice, audio mixer, brain and YouTube client stay decoupled and each
independently testable. Keep this module import-light (stdlib only) — it is imported
everywhere, including in self-tests that must run without numpy/PIL/ffmpeg.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

# ── stream defaults ──────────────────────────────────────────────────────────
FPS_DEFAULT = 24
WIDTH_DEFAULT = 1280
HEIGHT_DEFAULT = 720
AUDIO_RATE = 48000
AUDIO_CHANNELS = 2
SEGMENT_SECONDS = 3600  # upload the recording in 1-hour VOD chunks

# Behavior + emotion are open string enums (the renderer/behavior engine agree on the
# vocabulary below; unknown values degrade gracefully to "idle"/"neutral").
EMOTIONS = ("neutral", "happy", "curious", "surprised", "sleepy", "excited", "sassy", "love")
BEHAVIORS = (
    "idle", "wander", "pounce", "zoomies", "groom", "tailchase",
    "nap", "sing", "react", "sit", "stretch", "peek",
)


@dataclass
class Character:
    """The mascot's identity — editable live from the console via chat prompts.
    Appearance fields drive the renderer; personality fields drive the brain; voice
    fields drive the synth. Delivered to the stream in the internal /state payload."""
    name: str = "Momo"
    species: str = "cat"
    # Actor kind: "cat" = the programmatic Animica mascot (default, always available);
    # "sprite" = an end-user character built from an uploaded PNG (puppet-animated).
    kind: str = "cat"
    sprite_url: str = ""        # where the marketplace serves the uploaded PNG (worker fetches it)
    sprite_path: str = ""       # local PNG path on the streaming box (resolved from sprite_url)
    sprite_open_path: str = ""  # optional mouth-open PNG for PNGtuber-style talking
    knowledge_ref: str = ""     # id of this character's uploaded knowledge base (RAG), if any
    # personality → brain
    personality: str = (
        "a cheerful, curious ginger cat who adores the Animica network, hypes up "
        "miners and creators, and cracks gentle, wholesome jokes")
    speaking_style: str = "short, upbeat, playful — one or two sentences, lots of warmth"
    catchphrases: list = field(default_factory=lambda: ["mrrp!", "purr-fect", "let's gooo", "nya~"])
    topics: list = field(default_factory=lambda: [
        "the Animica network", "mining", "AI on-chain", "the .anm internet", "being a cat"])
    # appearance → renderer (RGB tuples)
    fur: tuple = (245, 176, 106)
    fur_dk: tuple = (223, 141, 76)
    belly: tuple = (255, 241, 224)
    iris: tuple = (60, 206, 200)
    accent: tuple = (109, 94, 246)
    default_emotion: str = "happy"
    # voice → synth
    voice_pitch: float = 1.0        # animalese base-pitch multiplier
    voice_wpm: int = 150            # spoken pacing

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Character":
        c = cls()
        if not isinstance(d, dict):
            return c
        for k, v in d.items():
            if not hasattr(c, k) or v is None:
                continue
            if k in ("fur", "fur_dk", "belly", "iris", "accent") and isinstance(v, (list, tuple)) and len(v) >= 3:
                setattr(c, k, (int(v[0]), int(v[1]), int(v[2])))
            elif k in ("catchphrases", "topics") and isinstance(v, list):
                setattr(c, k, [str(x) for x in v][:12])
            elif k in ("voice_pitch",):
                setattr(c, k, float(v))
            elif k in ("voice_wpm",):
                setattr(c, k, int(v))
            elif isinstance(getattr(c, k), str):
                setattr(c, k, str(v))
        return c


@dataclass
class AnimalState:
    """Everything the renderer needs to draw one frame of the cat. Mutated in place
    by the behavior engine each tick; read (never written) by the scene renderer."""
    x: float = 0.5           # position on the stage floor, normalized [0,1]
    y: float = 0.72          # normalized [0,1]; ~0.72 == standing on the floor line
    facing: int = 1          # +1 faces right, -1 faces left
    scale: float = 1.0       # size multiplier (pounce/peek tweak this)
    mouth: float = 0.0       # 0 closed .. 1 fully open (driven by voice RMS = lip-sync)
    eye: float = 1.0         # 1 open .. 0 fully blinked
    emotion: str = "neutral"
    behavior: str = "idle"
    bob: float = 0.0         # vertical bob for walk/sing, -1..1
    tail: float = 0.0        # tail wag phase, -1..1
    ear: float = 0.0         # ear twitch, -1..1
    lean: float = 0.0        # body lean for running/pouncing, -1..1
    prop: str = ""           # transient emote near the head: heart|note|zzz|sparkle|!|?
    thought: str = ""        # short quirky-action caption ("chasing its tail!")


@dataclass
class SceneContext:
    """Non-cat scene state: overlays, HUD, chat ticker. Owned by the pipeline."""
    title: str = "Animica Animal"
    subtitle: str = ""                              # current spoken words (real text)
    viewers: int = 0
    uptime_s: float = 0.0
    now_playing: str = ""
    net_stats: dict = field(default_factory=dict)   # {"height","price","peers","hashrate"}
    chat: list = field(default_factory=list)        # recent dicts: {"author","text","reply"}
    live: bool = True
    episode: int = 1
    daytime: float = 0.5                            # 0=midnight .. 0.5=noon .. 1=midnight (bg tint)


@dataclass
class SpeechItem:
    """A line the cat should say. Produced by the brain, consumed by the pipeline
    (voice → audio queue) and reflected on-screen (subtitle + emotion + behavior)."""
    text: str
    emotion: str = "neutral"
    behavior: str = "react"
    priority: int = 0        # higher wins; chat replies (10) > idle chatter (1)
    to_chat: str = ""        # optional short text to also post into the YouTube live chat
    source: str = "idle"     # "idle" | "chat" | "event"


@dataclass
class ChatMessage:
    id: str
    author: str
    text: str
    ts: float = 0.0


@dataclass
class StreamConfig:
    """Static configuration for one stream run."""
    width: int = WIDTH_DEFAULT
    height: int = HEIGHT_DEFAULT
    fps: int = FPS_DEFAULT
    bitrate_k: int = 4500
    audio_rate: int = AUDIO_RATE
    channels: int = AUDIO_CHANNELS
    segment_seconds: int = SEGMENT_SECONDS
    voice: str = "animalese"     # "animalese" (numpy, zero-dep) | "piper" | "off"
    piper_voice: str = ""        # path/name of a piper ONNX voice when voice == "piper"
    music: str = "auto"          # "auto" (synth lofi bed) | <path to audio file> | "off"
    persona_name: str = "Momo"
    channel_name: str = "Animica Animal"
    # Output routing: exactly one of these is active.
    rtmp_url: str = ""           # YouTube ingest, e.g. rtmp://a.rtmp.youtube.com/live2/<key>
    preview_path: str = ""       # if set, render to this local mp4 instead of RTMP (no YouTube)
    record_dir: str = ""         # where 1-hour segment mp4s are written for VOD upload
    seed: int = 1234             # deterministic behavior/animation for reproducible previews


# ── provider callables (dependency injection; keeps modules testable) ─────────
# net_provider() -> dict of live network stats for the HUD + brain grounding.
NetProvider = Callable[[], dict]
# chat_source() -> list[ChatMessage] of NEW messages since last call.
ChatSource = Callable[[], "list[ChatMessage]"]
# chat_sink(text) -> post a short line into the live chat (best-effort).
ChatSink = Callable[[str], None]


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def ease(t: float) -> float:
    """Smoothstep easing on [0,1]."""
    t = clamp(t)
    return t * t * (3.0 - 2.0 * t)
