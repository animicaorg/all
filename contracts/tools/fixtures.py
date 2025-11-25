# -*- coding: utf-8 -*-
"""
fixtures.py
===========

Small, reusable helpers and sample fixtures for the contracts tooling.

This module is intentionally **pure stdlib** so it can be imported by
scripts (build/deploy/call/verify) without pulling heavy deps. It provides:

- Paths: repo root discovery, build/outputs directory helpers
- JSON/bytes helpers: canonical JSON, seeded bytes for deterministic tests
- Sample addresses & seed wallets loader (devnet-friendly)
- Minimal ABI + manifest templates for example contracts (Counter/Escrow)
- Example inputs for capabilities (AI/Quantum) and DA blobs
- Convenience functions to materialize temporary files for local runs

Nothing here talks to the chain; higher-level scripts do that via the SDK.
"""

from __future__ import annotations

import json
import os
import re
import sys
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# --------------------------------------------------------------------------------------
# Paths & environment
# --------------------------------------------------------------------------------------

def repo_root() -> Path:
    """
    Best-effort repository root:
    - If ANIMICA_REPO_ROOT is set, prefer it.
    - Otherwise, traverse up from this file until we find a marker (e.g., .git or tests/).
    """
    env = os.getenv("ANIMICA_REPO_ROOT")
    if env:
        p = Path(env).expanduser().resolve()
        if p.exists():
            return p

    here = Path(__file__).resolve()
    for up in [here] + list(here.parents):
        # Heuristics: the root is where "contracts" and "tests" coexist OR a .git folder exists.
        if (up / "contracts").is_dir() and (up / "tests").is_dir():
            return up
        if (up / ".git").exists():
            return up
    # Fallback to two levels above contracts/tools/fixtures.py → repo/contracts/tools/.. → repo/
    return Path(__file__).resolve().parents[2]


def ensure_build_dir(subdir: Optional[str] = None) -> Path:
    """
    Ensure contracts/build[/subdir] exists and return the path.
    """
    base = repo_root() / "contracts" / "build"
    if subdir:
        base = base / subdir
    base.mkdir(parents=True, exist_ok=True)
    return base


def canonical_json_str(obj: Any) -> str:
    """
    Canonical JSON: UTF-8, stable key order, no extra spaces.
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, obj: Any) -> None:
    path.write_text(canonical_json_str(obj) + "\n", encoding="utf-8")


def write_bytes(path: Path, data: bytes) -> None:
    path.write_bytes(data)


# --------------------------------------------------------------------------------------
# Deterministic bytes & hashing helpers
# --------------------------------------------------------------------------------------

def drbg(seed: bytes, n: int) -> bytes:
    """
    Tiny deterministic byte generator using SHA3-256 in counter mode.
    Not for crypto; only for reproducible fixtures.
    """
    out = bytearray()
    ctr = 0
    while len(out) < n:
        h = hashlib.sha3_256(seed + ctr.to_bytes(8, "big")).digest()
        out.extend(h)
        ctr += 1
    return bytes(out[:n])


def sha3_256_hex(data: bytes) -> str:
    return "0x" + hashlib.sha3_256(data).hexdigest()


def sha3_512_hex(data: bytes) -> str:
    return "0x" + hashlib.sha3_512(data).hexdigest()


# --------------------------------------------------------------------------------------
# Fixtures: sample addresses & seed wallets
# --------------------------------------------------------------------------------------

# Devnet-safe sample addresses (bech32-like placeholders).
# Use load_seed_wallets() to fetch real funded keys for end-to-end tests.
SAMPLE_ADDRESSES: List[str] = [
    # These are format placeholders for demos; not derived from real keys.
    "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqny6j5t",
    "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqv4c4dm",
    "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq08xjha",
]


def load_seed_wallets() -> List[Dict[str, Any]]:
    """
    Load pre-funded devnet wallets if tests/devnet/seed_wallets.json exists.

    Returns a list of objects with (at minimum):
      - mnemonic (str)
      - address (str)
      - alg_id (str)
    """
    p = repo_root() / "tests" / "devnet" / "seed_wallets.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def sample_address(idx: int = 0) -> str:
    """
    Return a sample address. If seed wallets are present, return real one;
    otherwise return a static placeholder.
    """
    seeds = load_seed_wallets()
    if seeds:
        return seeds[idx % len(seeds)]["address"]
    return SAMPLE_ADDRESSES[idx % len(SAMPLE_ADDRESSES)]


# --------------------------------------------------------------------------------------
# Minimal ABI & manifest templates for examples
# --------------------------------------------------------------------------------------

def abi_counter() -> Dict[str, Any]:
    """
    Minimal ABI for the Counter contract, compatible with vm_py runtime.
    """
    return {
        "name": "Counter",
        "version": 1,
        "functions": [
            {"name": "inc", "inputs": [{"name": "n", "type": "uint64"}], "outputs": []},
            {"name": "get", "inputs": [], "outputs": [{"name": "value", "type": "uint64"}]},
        ],
        "events": [
            {"name": "Inc", "args": [{"name": "by", "type": "uint64"}]},
        ],
        "errors": [],
    }


def abi_escrow() -> Dict[str, Any]:
    """
    Minimal ABI for a simple escrow example.
    """
    return {
        "name": "Escrow",
        "version": 1,
        "functions": [
            {"name": "init", "inputs": [
                {"name": "party_a", "type": "address"},
                {"name": "party_b", "type": "address"},
                {"name": "amount", "type": "uint64"},
            ], "outputs": []},
            {"name": "release", "inputs": [{"name": "to", "type": "address"}], "outputs": []},
            {"name": "balance", "inputs": [], "outputs": [{"name": "amount", "type": "uint64"}]},
        ],
        "events": [{"name": "Released", "args": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint64"}]}],
        "errors": [],
    }


def manifest_template(name: str, abi: Dict[str, Any], code_hash: Optional[str] = None) -> Dict[str, Any]:
    """
    Skeleton manifest matching contracts/schemas/manifest.schema.json (loosely; code_hash can be filled post-compile).
    """
    return {
        "name": name,
        "abi": abi,
        "code_hash": code_hash or "<fill-after-compile>",
        "caps": {},         # reserved for future capability declarations
        "resources": {},    # optional metadata
        "version": 1,
    }


def example_counter_manifest() -> Dict[str, Any]:
    return manifest_template("Counter", abi_counter())


def example_escrow_manifest() -> Dict[str, Any]:
    return manifest_template("Escrow", abi_escrow())


# --------------------------------------------------------------------------------------
# Example argument builders
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CounterIncArgs:
    n: int = 1


def sample_counter_inc(n: int = 1) -> CounterIncArgs:
    return CounterIncArgs(n=max(0, int(n)))


@dataclass(frozen=True)
class EscrowInitArgs:
    party_a: str
    party_b: str
    amount: int


def sample_escrow_init(amount: int = 1000) -> EscrowInitArgs:
    a = sample_address(0)
    b = sample_address(1)
    return EscrowInitArgs(party_a=a, party_b=b, amount=max(0, int(amount)))


# --- Capabilities: AI/Quantum & DA sample payloads ------------------------------------

def sample_ai_prompt(model: str = "small:chat") -> Dict[str, Any]:
    """
    Small demo prompt used by studio-web/IDE and tests.
    """
    return {
        "model": model,
        "prompt": "Summarize: Animica contracts run a deterministic Python VM.",
        "max_tokens": 64,
        "temperature": 0.0,
    }


def sample_quantum_circuit(shots: int = 64) -> Dict[str, Any]:
    """
    Tiny "trap-friendly" toy circuit payload (illustrative only).
    """
    return {
        "backend": "demo-qpu",
        "shots": int(shots),
        "circuit": {
            "qubits": 2,
            "gates": [
                {"name": "h", "target": 0},
                {"name": "cx", "control": 0, "target": 1},
                {"name": "measure", "target": 0},
                {"name": "measure", "target": 1},
            ],
        },
        "trap_ratio": 0.1,  # devnet/testing only
    }


def sample_da_blob(size: int = 4 * 1024, ns: int = 24) -> Tuple[int, bytes]:
    """
    Produce a deterministic DA blob (namespace id, bytes). Size defaults to 4 KiB.
    """
    seed = b"animica:da-fixture"
    data = drbg(seed, max(0, int(size)))
    return (int(ns), data)


# --------------------------------------------------------------------------------------
# Fixture file access (tests/fixtures/*)
# --------------------------------------------------------------------------------------

def fixtures_dir() -> Path:
    return repo_root() / "tests" / "fixtures"


def read_fixture_bytes(relpath: str) -> bytes:
    p = fixtures_dir() / relpath
    return p.read_bytes()


def read_fixture_json(relpath: str) -> Any:
    p = fixtures_dir() / relpath
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------
# Temp artifact helpers
# --------------------------------------------------------------------------------------

def materialize_temp_json(name: str, obj: Any) -> Path:
    """
    Write a canonical JSON file into contracts/build/tmp/ and return its path.
    """
    out_dir = ensure_build_dir("tmp")
    path = out_dir / name
    write_json(path, obj)
    return path


def materialize_temp_bytes(name: str, data: bytes) -> Path:
    """
    Write bytes into contracts/build/tmp/ and return its path.
    """
    out_dir = ensure_build_dir("tmp")
    path = out_dir / name
    write_bytes(path, data)
    return path


# --------------------------------------------------------------------------------------
# Light validation utilities (for CLIs before hitting SDK/services)
# --------------------------------------------------------------------------------------

HEX_RE = re.compile(r"^0x[0-9a-fA-F]+$")


def is_hexlike(v: str) -> bool:
    return bool(HEX_RE.match(v))


def is_address_like(v: str) -> bool:
    """
    Very loose check: bech32-ish 'anim1...' string. SDK does strict validation.
    """
    return isinstance(v, str) and v.startswith("anim1") and 24 <= len(v) <= 80


def assert_address(v: str, field: str = "address") -> None:
    if not is_address_like(v):
        raise ValueError(f"Invalid {field}: expected bech32 anim1… address, got {v!r}")


# --------------------------------------------------------------------------------------
# Module export surface
# --------------------------------------------------------------------------------------

__all__ = [
    # paths & IO
    "repo_root", "ensure_build_dir", "canonical_json_str", "write_json", "write_bytes",
    # hashing & drbg
    "drbg", "sha3_256_hex", "sha3_512_hex",
    # addresses & seeds
    "SAMPLE_ADDRESSES", "load_seed_wallets", "sample_address",
    # ABI & manifests
    "abi_counter", "abi_escrow", "manifest_template",
    "example_counter_manifest", "example_escrow_manifest",
    # args & capability payloads
    "CounterIncArgs", "sample_counter_inc",
    "EscrowInitArgs", "sample_escrow_init",
    "sample_ai_prompt", "sample_quantum_circuit", "sample_da_blob",
    # fixtures access
    "fixtures_dir", "read_fixture_bytes", "read_fixture_json",
    # temp artifacts
    "materialize_temp_json", "materialize_temp_bytes",
    # validation
    "is_hexlike", "is_address_like", "assert_address",
]
