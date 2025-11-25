from __future__ import annotations

"""
Language-agnostic ABI IR (Intermediate Representation)
=====================================================

These dataclasses model a normalized contract surface that all codegen
backends (Python / TypeScript / Rust) consume. Instances are produced by
`sdk.codegen.common.normalize.normalize_abi`.

The IR is deliberately simple and deterministic:

- `TypeRef` covers the small set of scalar/container kinds required by the
  Animica Python-VM ABI (ints, bytes, bool, address, string, arrays, tuples).
- Functions, events, and errors carry a canonical textual `signature` plus a
  `selector`/`topic_id` (sha3-256 hex, 0x-prefixed) computed over the signature.
- Optional `discriminator` is assigned to resolve source-level name overloads.

Serialization helpers (`to_dict`) are provided for build metadata and caching.
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# -----------------
# Core type system
# -----------------

@dataclass
class TypeRef:
    """
    Canonical type descriptor used in ABI IR.

    Kinds:
      - "uint" | "int": optional `bits` (e.g., 256). If None, backend chooses default.
      - "bytes": optional fixed size via `bits` (multiple of 8); None ⇒ dynamic bytes.
      - "bool"
      - "address"
      - "string"
      - "array": `array_item` (TypeRef) + optional `array_len` (None ⇒ dynamic)
      - "tuple": `tuple_elems` (List[TypeRef])
    """
    kind: str
    bits: Optional[int] = None
    array_item: Optional["TypeRef"] = None
    array_len: Optional[int] = None
    tuple_elems: Optional[List["TypeRef"]] = None

    def canonical_str(self) -> str:
        """Canonical signature fragment for this type."""
        if self.kind in ("uint", "int"):
            return f"{self.kind}{self.bits or ''}"
        if self.kind == "bytes":
            if self.bits:
                return f"bytes{self.bits // 8}"
            return "bytes"
        if self.kind in ("bool", "address", "string"):
            return self.kind
        if self.kind == "array":
            if self.array_item is None:
                raise ValueError("array_item is required for array types")
            inner = self.array_item.canonical_str()
            suffix = f"[{self.array_len}]" if self.array_len is not None else "[]"
            return f"{inner}{suffix}"
        if self.kind == "tuple":
            elems = ",".join((e.canonical_str() for e in (self.tuple_elems or [])))
            return f"({elems})"
        raise ValueError(f"Unsupported TypeRef kind: {self.kind}")

    def is_dynamic(self) -> bool:
        """Heuristic dynamic-ness (useful for some encoders)."""
        if self.kind in ("bool", "address", "uint", "int"):
            return False
        if self.kind == "bytes":
            return self.bits is None
        if self.kind == "string":
            return True
        if self.kind == "array":
            # dynamic if length unknown or inner is dynamic
            return self.array_len is None or (self.array_item and self.array_item.is_dynamic())
        if self.kind == "tuple":
            return any(e.is_dynamic() for e in (self.tuple_elems or []))
        return True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Param:
    """Named parameter for functions/events/errors."""
    name: str
    type: TypeRef
    # Events may set `indexed=True`; for others it's ignored.
    indexed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type.to_dict(),
            "indexed": self.indexed,
        }


# ---------------
# ABI components
# ---------------

@dataclass
class FunctionIR:
    name: str
    inputs: List[Param] = field(default_factory=list)
    outputs: List[TypeRef] = field(default_factory=list)
    state_mutability: str = "nonpayable"  # "pure" | "view" | "nonpayable" | "payable"
    signature: str = ""                   # e.g., "transfer(address,uint256)"
    selector: str = ""                    # sha3-256 hex of signature (0x...)
    discriminator: Optional[str] = None   # short stable suffix for overloads

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "inputs": [p.to_dict() for p in self.inputs],
            "outputs": [t.to_dict() for t in self.outputs],
            "stateMutability": self.state_mutability,
            "signature": self.signature,
            "selector": self.selector,
            "discriminator": self.discriminator,
        }


@dataclass
class EventIR:
    name: str
    inputs: List[Param] = field(default_factory=list)
    anonymous: bool = False
    signature: str = ""    # e.g., "Transfer(address,address,uint256)"
    topic_id: str = ""     # sha3-256 hex of signature (0x...)
    discriminator: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "inputs": [p.to_dict() for p in self.inputs],
            "anonymous": self.anonymous,
            "signature": self.signature,
            "topicId": self.topic_id,
            "discriminator": self.discriminator,
        }


@dataclass
class ErrorIR:
    name: str
    inputs: List[Param] = field(default_factory=list)
    signature: str = ""
    selector: str = ""      # sha3-256 hex of signature (0x...)
    discriminator: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "inputs": [p.to_dict() for p in self.inputs],
            "signature": self.signature,
            "selector": self.selector,
            "discriminator": self.discriminator,
        }


@dataclass
class AbiIR:
    functions: List[FunctionIR] = field(default_factory=list)
    events: List[EventIR] = field(default_factory=list)
    errors: List[ErrorIR] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "functions": [f.to_dict() for f in self.functions],
            "events": [e.to_dict() for e in self.events],
            "errors": [r.to_dict() for r in self.errors],
            "metadata": dict(self.metadata),
        }


__all__ = [
    "TypeRef",
    "Param",
    "FunctionIR",
    "EventIR",
    "ErrorIR",
    "AbiIR",
]
