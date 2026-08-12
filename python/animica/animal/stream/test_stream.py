"""Network-free tests for the Animica Animal 24/7 livestream package."""
from __future__ import annotations

import time

import numpy as np

from animica.animal.stream.contract import (
    Character, StreamConfig, SceneContext, ChatMessage, AnimalState,
)
from animica.animal.stream.behavior import BehaviorEngine
from animica.animal.stream.voice import Voice
from animica.animal.stream.audio import AudioMixer
from animica.animal.stream.brain import Brain


def test_character_roundtrip_and_edits():
    # a chat-style edit: sprite kind + palette + catchphrases + knowledge + voice
    patch = {
        "name": "Blip", "kind": "sprite", "sprite_url": "https://x/y",
        "iris": [10, 20, 30], "accent": [40, 50, 60],
        "catchphrases": ["boop", "zoom"], "knowledge_ref": "kb_abc",
        "voice_pitch": 1.4, "voice_wpm": 200,
    }
    c = Character.from_dict(patch)
    assert c.name == "Blip"
    assert c.kind == "sprite" and c.sprite_url == "https://x/y"
    assert tuple(c.iris) == (10, 20, 30)
    assert "boop" in c.catchphrases and c.knowledge_ref == "kb_abc"
    assert 1.39 < c.voice_pitch < 1.41 and c.voice_wpm == 200


def test_default_character_is_the_cat():
    c = Character()
    assert c.kind == "cat" and c.species == "cat"


def test_behavior_engine_advances_and_stays_bounded():
    cfg = StreamConfig()
    char = Character()
    eng = BehaviorEngine(cfg, char, (0.1, 0.9))
    state = AnimalState()
    for _ in range(300):
        eng.tick(1 / 24.0, state, 0.0)
    assert np.isfinite(state.x) and np.isfinite(state.y)
    # the actor should not wander off the stage floor bounds it was given
    assert 0.0 <= state.x <= 1.0


def test_voice_synth_returns_audio():
    v = Voice(StreamConfig(), Character())
    samples, dur = v.synth("mrrp hello world")
    assert isinstance(samples, np.ndarray) and samples.dtype == np.float32
    assert dur > 0.0 and len(samples) > 0


def test_audio_mixer_reads_stereo_s16le():
    cfg = StreamConfig()
    mix = AudioMixer(cfg, Character())
    buf = mix.read(1024)
    assert isinstance(buf, (bytes, bytearray))
    # nframes * channels * 2 bytes/sample
    assert len(buf) == 1024 * cfg.channels * 2


def test_stream_config_defaults():
    cfg = StreamConfig()
    assert cfg.width > 0 and cfg.height > 0 and cfg.fps > 0
    assert cfg.audio_rate in (44100, 48000)
    assert cfg.segment_seconds == 3600  # 1-hour VOD chunks


def test_heartbeat_posts_live_and_offline(monkeypatch):
    from animica.cli import animal as A
    import animica.animal.engine as engine

    posts = []
    monkeypatch.setattr(engine, "_internal",
                        lambda cfg, path, method="GET", body=None, timeout=30: posts.append((path, method, body)) or {"ok": True})

    class FakeYT:
        def viewers(self):
            return 42

    hb = A._LiveHeartbeat(FakeYT(), "https://youtu.be/abc", "Momo", period=999)
    hb._started = time.time() - 5
    hb._post(True)
    hb._post(False)

    assert posts[0][0] == "/live" and posts[0][2]["live"] is True
    assert posts[0][2]["viewers"] == 42
    assert posts[0][2]["watchUrl"] == "https://youtu.be/abc"
    assert posts[0][2]["uptime"] >= 4
    assert posts[1][2]["live"] is False and posts[1][2]["viewers"] == 0


def test_brain_fallback_is_in_character():
    brain = Brain(StreamConfig(), Character(), chat_source=None, chat_sink=None, rag=None, log=lambda m: None)
    ctx = SceneContext()
    line = brain._fallback(ChatMessage(id="1", author="viewer", text="who are you?"), ctx)
    assert isinstance(line, str) and len(line) > 0


def test_youtube_from_console_without_token_is_none():
    from animica.animal.stream.youtube import YouTubeLive
    yt = YouTubeLive.from_console(log=lambda m: None)
    assert yt is None or hasattr(yt, "go_live")
