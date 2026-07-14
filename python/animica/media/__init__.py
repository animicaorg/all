"""Animica generative-media runtime (8.0.0).

Miners serve image / video / audio generation as AICF job kinds. This package holds the
model runtimes; the node broker (rpc/methods/aicf_jobs.py) routes media job kinds here and
validates outputs fail-closed (real bytes or reject — never a stub).

Design rules:
  * Heavy deps (torch, diffusers) are imported LAZILY inside functions so that importing this
    package on a node/venv WITHOUT the media extra never fails — it just reports unavailable.
  * Generators return real encoded bytes (PNG/MP4/WAV) or raise MediaError. They NEVER return a
    placeholder, because a placeholder is indistinguishable from a real result to the job queue
    and could win a K-way race and be credited.
"""

from .base import MediaError, MediaBackendUnavailable, MediaKind, sha3_hex, media_available

__all__ = ["MediaError", "MediaBackendUnavailable", "MediaKind", "sha3_hex", "media_available"]
