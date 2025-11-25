"""
Animica SDK Codegen — Common IR & Normalization
================================================

This package exposes a *language-agnostic* intermediate representation (IR)
for contract ABIs and the canonical normalization pipeline that all target
generators (Python/TypeScript/Rust) consume.

Public surface:
- IR dataclasses: see ``AbiIR``, ``FunctionIR``, ``EventIR``, ``ErrorIR``,
  ``TypeRef``, ``Param``.
- Normalizer: ``normalize_abi`` to validate + sanitize + canonicalize ABIs.
- Utilities: ``compute_abi_hash`` for reproducible build metadata.
- Error types: ``AbiNormalizationError``.

Implementations live in ``model.py`` and ``normalize.py`` respectively.
"""

from .model import (
    AbiIR,
    FunctionIR,
    EventIR,
    ErrorIR,
    TypeRef,
    Param,
)
from .normalize import (
    normalize_abi,
    compute_abi_hash,
    AbiNormalizationError,
)

__all__ = [
    # IR
    "AbiIR",
    "FunctionIR",
    "EventIR",
    "ErrorIR",
    "TypeRef",
    "Param",
    # Normalization API
    "normalize_abi",
    "compute_abi_hash",
    # Errors
    "AbiNormalizationError",
]

# Bumped when the IR/normalization *output contract* changes in a way
# that could affect downstream templates. Generators may embed this
# for reproducibility.
__version__ = "0.1.0"
