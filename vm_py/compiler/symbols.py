"""
symbols.py — Symbol table, method dispatch map, and storage-key hinting.

This module is used by the compiler/type-checker to:
  • Track declared variables, constants, functions, and methods.
  • Build a deterministic method-dispatch registry keyed by (owner_type, method_name).
  • Derive *storage key hints* for contract-scoped variables in a stable, domain-separated way.

Design notes
------------
- Storage key hints are derived with SHA3-256 over a domain string:
      "animica.vm.storage-key|{contract}|{symbol}"
  and then widened to 32 bytes by repeating+truncating deterministically. This is only a *hint* for
  tooling and off-chain simulators; the execution layer can map/adjust slots as needed.

- Method dispatch is a pure compile-time registry. The runtime can later bind these to actual
  implementations or ABIs; the IDs computed here are stable hashes for cross-referencing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from hashlib import sha3_256
from typing import Dict, List, Mapping, MutableMapping, Optional, Tuple


# ------------------------------- Symbol Kinds -------------------------------- #

class SymbolKind(Enum):
    VAR = auto()
    CONST = auto()
    FUNC = auto()
    METHOD = auto()
    EVENT = auto()
    STORAGE_KEY = auto()


# --------------------------------- Symbols ---------------------------------- #

@dataclass(frozen=True)
class Symbol:
    name: str
    kind: SymbolKind
    type_hint: Optional[str] = None
    is_storage: bool = False
    is_exported: bool = False
    meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class FunctionSymbol(Symbol):
    params: Tuple[str, ...] = field(default_factory=tuple)
    returns: Optional[str] = None

    def signature(self) -> str:
        """Human-readable signature like 'foo(int,bytes)->bool' (types are best-effort hints)."""
        args = ",".join(self.params)
        ret = self.returns or "void"
        return f"{self.name}({args})->{ret}"


@dataclass(frozen=True)
class MethodSymbol(FunctionSymbol):
    owner_type: str = "Contract"

    @property
    def dispatch_key(self) -> Tuple[str, str]:
        return (self.owner_type, self.name)

    @property
    def dispatch_id(self) -> str:
        """
        Deterministic, human-stable ID for wiring and cross-file references.
        16-hex prefix of SHA3(owner|name|params|return); safe to display/log.
        """
        h = sha3_256()
        h.update(b"animica.vm.method|")
        h.update(self.owner_type.encode())
        h.update(b"|")
        h.update(self.name.encode())
        h.update(b"|")
        h.update(",".join(self.params).encode())
        h.update(b"|")
        h.update((self.returns or "void").encode())
        return h.hexdigest()[:16]


# ------------------------------ Storage Key Hints ---------------------------- #

_DOMAIN = b"animica.vm.storage-key|"


@dataclass(frozen=True)
class StorageKeyHint:
    contract: str
    symbol: str

    @property
    def key32(self) -> bytes:
        """
        32-byte slot hint derived from SHA3-256(domain|contract|symbol), then
        widened to 32 bytes by deterministic repetition (if ever needed).
        """
        base = _hash_storage_key(self.contract, self.symbol)
        # Already 32 bytes for SHA3-256; keep as-is for clarity.
        return base

    @property
    def hex(self) -> str:
        return "0x" + self.key32.hex()


def _hash_storage_key(contract: str, symbol: str) -> bytes:
    h = sha3_256()
    h.update(_DOMAIN)
    h.update(contract.encode())
    h.update(b"|")
    h.update(symbol.encode())
    return h.digest()  # 32 bytes


# ----------------------------- Method Dispatch Map --------------------------- #

class MethodDispatch:
    """
    Method registry keyed by (owner_type, method_name) → MethodSymbol.

    Provides deterministic lookups and a compact `.index` that can be embedded into
    IR metadata or debugging info.
    """
    def __init__(self) -> None:
        self._by_type: Dict[str, Dict[str, MethodSymbol]] = {}

    def register(self, m: MethodSymbol) -> None:
        bucket = self._by_type.setdefault(m.owner_type, {})
        if m.name in bucket:
            # Deterministic collision guard: exact same signature is ok; differing signature is not.
            existing = bucket[m.name]
            if (existing.params, existing.returns) != (m.params, m.returns):
                raise ValueError(
                    f"Method redefinition with different signature: "
                    f"{existing.signature()} vs {m.signature()}"
                )
            return
        bucket[m.name] = m

    def get(self, owner_type: str, method_name: str) -> Optional[MethodSymbol]:
        return self._by_type.get(owner_type, {}).get(method_name)

    def methods_for(self, owner_type: str) -> Mapping[str, MethodSymbol]:
        return self._by_type.get(owner_type, {})

    @property
    def index(self) -> Dict[str, Dict[str, str]]:
        """owner_type → method_name → dispatch_id (hex prefix)"""
        return {
            otyp: {name: m.dispatch_id for name, m in methods.items()}
            for otyp, methods in self._by_type.items()
        }


# -------------------------------- Symbol Table ------------------------------- #

class SymbolTable:
    """
    A compact symbol table with single lexical scope for contract source units.

    If you need nested scopes later, layer small child tables with a `.parent`
    pointer and override `declare/resolve` to fall back into parent on misses.
    """
    def __init__(self, *, contract_name: str = "Contract", parent: Optional["SymbolTable"] = None) -> None:
        self.contract_name = contract_name
        self.parent = parent
        self._symbols: Dict[str, Symbol] = {}
        self._dispatch = MethodDispatch()
        self._storage_hints: Dict[str, StorageKeyHint] = {}

    # ---- Declarations ---- #

    def declare_var(self, name: str, *, type_hint: Optional[str] = None, storage: bool = False,
                    exported: bool = False) -> Symbol:
        if name in self._symbols:
            raise ValueError(f"Symbol already declared: {name}")
        sym = Symbol(name=name, kind=SymbolKind.VAR, type_hint=type_hint, is_storage=storage, is_exported=exported)
        self._symbols[name] = sym
        if storage:
            self._storage_hints[name] = StorageKeyHint(self.contract_name, name)
        return sym

    def declare_const(self, name: str, *, type_hint: Optional[str] = None, exported: bool = False) -> Symbol:
        if name in self._symbols:
            raise ValueError(f"Symbol already declared: {name}")
        sym = Symbol(name=name, kind=SymbolKind.CONST, type_hint=type_hint, is_storage=False, is_exported=exported)
        self._symbols[name] = sym
        return sym

    def declare_func(self, name: str, params: List[str], returns: Optional[str] = None,
                     *, exported: bool = False) -> FunctionSymbol:
        if name in self._symbols:
            raise ValueError(f"Symbol already declared: {name}")
        fs = FunctionSymbol(name=name, kind=SymbolKind.FUNC, params=tuple(params), returns=returns,
                            is_exported=exported)
        self._symbols[name] = fs
        return fs

    def declare_method(self, owner_type: str, name: str, params: List[str],
                       returns: Optional[str] = None, *, exported: bool = True) -> MethodSymbol:
        ms = MethodSymbol(name=name, kind=SymbolKind.METHOD, params=tuple(params),
                          returns=returns, owner_type=owner_type, is_exported=exported)
        # Methods are also addressable by plain name in the table if unique.
        if name in self._symbols and not isinstance(self._symbols[name], (FunctionSymbol, MethodSymbol)):
            raise ValueError(f"Cannot declare method; symbol name taken by non-callable: {name}")
        # Allow shadowing a plain function with a method of the same name; method dispatch uses (owner_type, name).
        self._dispatch.register(ms)
        self._symbols[name] = ms
        return ms

    # ---- Queries ---- #

    def resolve(self, name: str) -> Optional[Symbol]:
        s = self._symbols.get(name)
        if s is not None:
            return s
        if self.parent:
            return self.parent.resolve(name)
        return None

    def resolve_method(self, owner_type: str, method_name: str) -> Optional[MethodSymbol]:
        m = self._dispatch.get(owner_type, method_name)
        if m:
            return m
        if self.parent:
            return self.parent.resolve_method(owner_type, method_name)
        return None

    # ---- Storage hints ---- #

    def storage_key_hint(self, symbol_name: str) -> Optional[StorageKeyHint]:
        hint = self._storage_hints.get(symbol_name)
        if hint:
            return hint
        if self.parent:
            return self.parent.storage_key_hint(symbol_name)
        return None

    @property
    def storage_index(self) -> Dict[str, str]:
        """symbol_name → 0x…32-byte hex hint"""
        idx = {k: v.hex for k, v in self._storage_hints.items()}
        if self.parent:
            parent_idx = self.parent.storage_index
            parent_idx.update({k: v for k, v in idx.items() if k not in parent_idx})
            return parent_idx
        return idx

    # ---- Introspection ---- #

    @property
    def dispatch_index(self) -> Dict[str, Dict[str, str]]:
        return self._dispatch.index

    @property
    def all_symbols(self) -> Mapping[str, Symbol]:
        if not self.parent:
            return dict(self._symbols)
        # Merge with parent for a flattened view (current scope wins)
        merged = dict(self.parent.all_symbols)
        merged.update(self._symbols)
        return merged


__all__ = [
    "SymbolKind",
    "Symbol",
    "FunctionSymbol",
    "MethodSymbol",
    "StorageKeyHint",
    "SymbolTable",
    "MethodDispatch",
]
