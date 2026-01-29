"""
Animica | proofs.utils

Lightweight package initializer for proof-related utilities.

Design notes
- **Do not import submodules here.** Python executes this file whenever any
  submodule (e.g., `proofs.utils.hash`) is imported. Keeping this file import-free
  avoids ordering pitfalls during bootstrap when files may be generated in
  sequence.
- Submodules provided by this package:
    • hash           — SHA3-256/512, optional BLAKE3, small domain-tag helpers
    • math           — safe log/clamp, fixed-point helpers, ratio utilities
    • keccak_stream  — streaming Keccak utilities for header/nonce binding
    • schema         — JSON-Schema / CDDL loading & validation helpers

Usage examples
    from proofs.utils.hash import sha3_256
    from proofs.utils.math import clamp
    from proofs.utils.keccak_stream import KeccakStream
    from proofs.utils.schema import load_cddl, load_json_schema
"""

# Public submodules (exported as names only; not imported here)
__all__ = ["hash", "math", "keccak_stream", "schema"]

# Optional package metadata
__package_name__ = "animica-proofs-utils"
__version__ = "0.1.0"
