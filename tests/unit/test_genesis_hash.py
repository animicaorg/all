# SPDX-License-Identifier: Apache-2.0
"""
Reproducible genesis hash test.

Discovers a genesis fixture (JSON or CBOR), canonicalizes it deterministically,
and verifies its hash against an expected value stored alongside the fixture
(or provided via environment variables).

How it finds things:

- Genesis file search order (first existing is used):
  * $GENESIS_PATH
  * $GENESIS_DIR/genesis.json
  * spec/chain/genesis.json
  * spec/test_vectors/genesis.json
  * tests/fixtures/genesis.json
  * genesis.json
  * Any file named "genesis.json" or "genesis.cbor" underneath:
      spec/, spec/test_vectors/, tests/fixtures/

- Expected hash sources (first found wins):
  * $GENESIS_EXPECTED_HASH (env var; hex with or without 0x)
    * optional $GENESIS_HASH_ALGO: sha256 | keccak256 | blake3 (default: sha256)
  * Sidecar files next to genesis file (case-insensitive):
      - <genesis>.(sha256|keccak256|blake3)
      - <genesis>.hash         (format: "<algo>:<hex>" or just hex => sha256)
      - genesis.(sha256|keccak256|blake3|hash) in the same directory
  * JSON sidecar:
      - <genesis>.hash.json   ({"algo":"sha256","hex":"..."} or {"sha256":"..."} etc.)

If neither the genesis fixture nor an expected hash can be located, the test is
SKIPPED (with a detailed reason), rather than failing spuriously.

To force the algorithm, set GENESIS_HASH_ALGO (sha256 | keccak256 | blake3).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import pytest

# Optional dependencies (best-effort)
try:  # pragma: no cover
    import cbor2  # type: ignore
except Exception:  # pragma: no cover
    cbor2 = None


# keccak providers (try several)
def _keccak256(data: bytes) -> Optional[bytes]:
    # pysha3
    try:  # pragma: no cover
        import sha3  # type: ignore

        k = sha3.keccak_256()
        k.update(data)
        return k.digest()
    except Exception:
        pass
    # eth-hash
    try:  # pragma: no cover
        from eth_hash.auto import keccak  # type: ignore

        return keccak(data)
    except Exception:
        pass
    # hashlib shake_256 is NOT keccak-256; do not silently substitute
    return None


def _blake3(data: bytes) -> Optional[bytes]:
    try:  # pragma: no cover
        import blake3  # type: ignore

        return blake3.blake3(data).digest()
    except Exception:
        return None


# ------------------------------ Discovery ------------------------------------

CANDIDATE_FILES = [
    # explicit env path
    os.getenv("GENESIS_PATH") or "",
    # env dir
    (
        (Path(os.getenv("GENESIS_DIR")) / "genesis.json").as_posix()
        if os.getenv("GENESIS_DIR")
        else ""
    ),
    "spec/chain/genesis.json",
    "spec/test_vectors/genesis.json",
    "tests/fixtures/genesis.json",
    "genesis.json",
]

SEARCH_DIRS = [
    "spec",
    "spec/test_vectors",
    "tests/fixtures",
]


def _first_existing(*paths: str) -> Optional[Path]:
    for p in paths:
        if not p:
            continue
        pp = Path(p)
        if pp.exists() and pp.is_file():
            return pp
    return None


def _scan_for_genesis() -> Optional[Path]:
    direct = _first_existing(*CANDIDATE_FILES)
    if direct:
        return direct
    # fallback scan
    for d in SEARCH_DIRS:
        root = Path(d)
        if not root.exists():
            continue
        for p in root.rglob("genesis.json"):
            if p.is_file():
                return p
        for p in root.rglob("genesis.cbor"):
            if p.is_file():
                return p
    return None


# ------------------------------ Canonicalization ------------------------------


def _canonicalize_json_bytes(p: Path) -> bytes:
    obj = json.loads(p.read_text(encoding="utf-8"))
    # RFC 8785-inspired minimal canonicalization:
    return json.dumps(
        obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _canonicalize_cbor_bytes(p: Path) -> bytes:
    if cbor2 is None:
        pytest.skip(
            "CBOR fixture present but 'cbor2' is not installed. Install cbor2 or provide JSON genesis."
        )
    obj = cbor2.loads(p.read_bytes())
    # cbor2 supports canonical serialization for deterministic bytes
    return cbor2.dumps(obj, canonical=True)  # type: ignore[arg-type]


def _canonical_genesis_bytes(p: Path) -> bytes:
    ext = p.suffix.lower()
    if ext == ".json":
        return _canonicalize_json_bytes(p)
    if ext == ".cbor":
        return _canonicalize_cbor_bytes(p)
    # Fallback: treat as raw bytes, but this is unusual
    return p.read_bytes()


# ------------------------------ Expected hash ---------------------------------


@dataclass
class ExpectedHash:
    algo: str  # "sha256" | "keccak256" | "blake3"
    hex: str  # lowercase, no 0x


def _norm_hex(s: str) -> str:
    s = s.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    return s


def _parse_sidecar_text(text: str) -> ExpectedHash:
    s = text.strip()
    if ":" in s:
        algo, h = s.split(":", 1)
        return ExpectedHash(algo=algo.strip().lower(), hex=_norm_hex(h))
    # default algo if only hex is present
    return ExpectedHash(algo="sha256", hex=_norm_hex(s))


def _load_expected_from_sidecars(genesis_path: Path) -> Optional[ExpectedHash]:
    base = genesis_path
    candidates = [
        base.with_suffix(base.suffix + ".sha256"),
        base.with_suffix(base.suffix + ".keccak256"),
        base.with_suffix(base.suffix + ".blake3"),
        base.with_suffix(base.suffix + ".hash"),
        base.parent / "genesis.sha256",
        base.parent / "genesis.keccak256",
        base.parent / "genesis.blake3",
        base.parent / "genesis.hash",
    ]
    for c in candidates:
        if c.exists():
            if c.suffix == ".json":
                try:
                    data = json.loads(c.read_text(encoding="utf-8"))
                except Exception as e:
                    pytest.fail(f"Expected-hash JSON sidecar {c} is invalid JSON: {e}")
                # Accept either {"algo":"sha256","hex":"..."} or {"sha256":"..."} forms
                if "hex" in data and "algo" in data:
                    return ExpectedHash(
                        algo=str(data["algo"]).lower(), hex=_norm_hex(str(data["hex"]))
                    )
                for k, v in data.items():
                    kk = str(k).lower()
                    if kk in ("sha256", "keccak256", "blake3"):
                        return ExpectedHash(algo=kk, hex=_norm_hex(str(v)))
                continue
            else:
                return _parse_sidecar_text(c.read_text(encoding="utf-8"))
    return None


def _load_expected_from_env() -> Optional[ExpectedHash]:
    h = os.getenv("GENESIS_EXPECTED_HASH")
    if not h:
        return None
    algo = (os.getenv("GENESIS_HASH_ALGO") or "sha256").lower()
    return ExpectedHash(algo=algo, hex=_norm_hex(h))


def _expected_hash(genesis_path: Path) -> Optional[ExpectedHash]:
    # JSON sidecar first (explicit)
    json_sidecar = genesis_path.with_suffix(genesis_path.suffix + ".hash.json")
    if json_sidecar.exists():
        try:
            data = json.loads(json_sidecar.read_text(encoding="utf-8"))
            if "hex" in data and "algo" in data:
                return ExpectedHash(
                    algo=str(data["algo"]).lower(), hex=_norm_hex(str(data["hex"]))
                )
        except Exception as e:
            pytest.fail(
                f"Expected-hash JSON sidecar {json_sidecar} is invalid JSON: {e}"
            )

    # Plain sidecars
    s = _load_expected_from_sidecars(genesis_path)
    if s:
        return s

    # Environment last
    e = _load_expected_from_env()
    if e:
        return e

    return None


# ------------------------------ Hashing ---------------------------------------


def _compute_hash(algo: str, data: bytes) -> Tuple[str, Optional[str]]:
    """
    Returns (hex_digest, warning) where warning is a string if the algorithm isn't available.
    """
    algo = algo.lower()
    if algo == "sha256":
        return hashlib.sha256(data).hexdigest(), None
    if algo == "keccak256":
        d = _keccak256(data)
        if d is None:
            return "", "keccak256 not available (install pysha3 or eth-hash)."
        return d.hex(), None
    if algo == "blake3":
        d = _blake3(data)
        if d is None:
            return "", "blake3 not available (pip install blake3)."
        return d.hex(), None
    raise ValueError(f"Unknown hash algorithm: {algo}")


# ---------------------------------- Test --------------------------------------


def test_genesis_hash_is_reproducible_and_matches_expected():
    genesis = _scan_for_genesis()
    if not genesis:
        pytest.skip(
            "No genesis fixture found. Set GENESIS_PATH or place genesis.json in "
            "spec/chain/, spec/test_vectors/, or tests/fixtures/."
        )

    expected = _expected_hash(genesis)
    if not expected:
        pytest.skip(
            f"No expected-hash sidecar or env found for {genesis}.\n"
            "Provide one of:\n"
            f"  - {genesis}.sha256 | .keccak256 | .blake3 | .hash\n"
            f'  - {genesis}.hash.json with {{"algo":"sha256","hex":"..."}}\n'
            "  - GENESIS_EXPECTED_HASH (+ optional GENESIS_HASH_ALGO)."
        )

    # Canonical bytes (deterministic)
    b1 = _canonical_genesis_bytes(genesis)
    b2 = _canonical_genesis_bytes(genesis)
    assert b1 == b2, "Canonicalization must be deterministic (bytes differ across runs)"

    # Compute hash and compare
    got, warn = _compute_hash(expected.algo, b1)
    if warn:
        pytest.skip(f"Cannot compute {expected.algo} for {genesis}: {warn}")

    assert got.lower() == expected.hex.lower(), (
        f"Genesis hash mismatch for {genesis}\n"
        f"  algo:     {expected.algo}\n"
        f"  expected: {expected.hex}\n"
        f"  got:      {got}\n"
        "If you intentionally changed the genesis, update the expected-hash sidecar."
    )
