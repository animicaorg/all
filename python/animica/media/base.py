"""Shared media types + fail-closed helpers. No heavy imports here."""

from __future__ import annotations

import hashlib
import os
from enum import Enum


class MediaKind(str, Enum):
    IMAGE = "image"          # text -> image
    VIDEO_T2V = "video_t2v"  # text -> video
    VIDEO_I2V = "video_i2v"  # image + text -> video
    VIDEO_V2V = "video_v2v"  # video + text -> video
    AUDIO = "audio"          # text -> audio (music / sfx / tts)

    @classmethod
    def parse(cls, value: str) -> "MediaKind":
        v = (value or "").strip().lower()
        for k in cls:
            if k.value == v:
                return k
        raise MediaError(f"unknown media kind: {value!r}")

    @property
    def is_media(self) -> bool:
        return True


MEDIA_KINDS = {k.value for k in MediaKind}


def is_media_kind(job_kind: str | None) -> bool:
    return (job_kind or "").strip().lower() in MEDIA_KINDS


class MediaError(RuntimeError):
    """Any media generation failure. Callers must fail closed (reject the job), never stub."""


class MediaBackendUnavailable(MediaError):
    """The media extra (diffusers/torch) is not installed on this worker."""


def sha3_hex(data: bytes) -> str:
    """Canonical content hash for receipts (matches the chain's sha3_256 usage)."""
    return hashlib.sha3_256(data).hexdigest()


# Magic-byte signatures for fail-closed output validation on the node broker.
_MAGIC = {
    "png": b"\x89PNG\r\n\x1a\n",
    "jpeg": b"\xff\xd8\xff",
    "webp": b"RIFF",  # + WEBP at offset 8
    "mp4": None,       # checked specially (ftyp box)
    "wav": b"RIFF",    # + WAVE at offset 8
}


def validate_magic(data: bytes, expect: str) -> bool:
    """True iff `data` really is the claimed container. Used to reject stub/garbage media."""
    if not data or len(data) < 12:
        return False
    expect = expect.lower()
    if expect == "png":
        return data.startswith(_MAGIC["png"])
    if expect in ("jpg", "jpeg"):
        return data.startswith(_MAGIC["jpeg"])
    if expect == "webp":
        return data[0:4] == b"RIFF" and data[8:12] == b"WEBP"
    if expect == "wav":
        return data[0:4] == b"RIFF" and data[8:12] == b"WAVE"
    if expect == "mp4":
        # ISO base media: 'ftyp' box near the start.
        return b"ftyp" in data[:32]
    return False


def media_available() -> tuple[bool, str]:
    """Cheap probe: is the media backend importable on this worker? (does not load a model)."""
    if os.environ.get("ANIMICA_MEDIA_DISABLE") == "1":
        return False, "disabled by ANIMICA_MEDIA_DISABLE=1"
    try:
        import torch  # noqa: F401
        import diffusers  # noqa: F401
    except Exception as e:  # pragma: no cover - env dependent
        return False, f"media extra not installed: {e}"
    return True, "ok"
