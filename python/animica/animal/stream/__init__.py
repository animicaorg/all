"""Animica Animal — 24/7 interactive AI livestream pipeline (YouTube).

Renders a programmatic animated mascot with lip-sync, a behavior state machine,
animalese/piper voice, a numpy audio mixer and a chat-reactive brain, muxed through
bundled ffmpeg to a YouTube RTMP ingest plus 1-hour VOD segments.
"""
from .contract import (  # noqa: F401
    AnimalState,
    Character,
    ChatMessage,
    SceneContext,
    SpeechItem,
    StreamConfig,
)
