"""
Data Availability (DA) helpers for Animica.

This module is responsible for blob → chunk → NMT/RS wiring on the
Python side. The `chunk_blob` function is the canonical blob splitter.
"""
from __future__ import annotations

from .blob_chunking import chunk_blob

__all__ = ["chunk_blob"]
