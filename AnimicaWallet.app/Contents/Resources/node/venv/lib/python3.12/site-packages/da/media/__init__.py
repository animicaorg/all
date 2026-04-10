"""
Animica DA Media module.

Provides data structures and utilities for storing media (images, videos, etc.)
in the Data Availability layer with manifest-based metadata.
"""

from __future__ import annotations

from .manifest import (
    MediaManifest,
    MediaKind,
    Integrity,
    ChunkingParams,
    create_manifest,
    verify_manifest,
)

__all__ = [
    "MediaManifest",
    "MediaKind",
    "Integrity",
    "ChunkingParams",
    "create_manifest",
    "verify_manifest",
]
