"""
randomness.utils
----------------

Utility namespace for the randomness beacon module.

This package is intended to host light helpers (e.g., byte ops, hashing
wrappers, encoding/decoding helpers, and small time/entropy utilities)
that are shared across the beacon components.

Modules may be added over time; this package file deliberately avoids
eager imports to keep dependency order simple during bootstrap.
"""

__all__: list[str] = []
