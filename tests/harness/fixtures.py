"""
fixtures.py — load CBOR/JSON vectors from spec/test_vectors/*
==============================================================

Utilities for discovering and loading canonical test vectors used
across integration/unit tests. Supports both JSON (*.json) and
CBOR (*.cbor) files.

Search strategy
---------------
1) If the environment variable TEST_VECTORS_DIR is set, it is used.
2) Otherwise we search upwards from CWD for "spec/test_vectors".
3) Finally we fall back to "./spec/test_vectors" relative to CWD.

CBOR decoding
-------------
We try (in order):
  • msgspec.cbor (if available, usually via "pip install msgspec[cbor]")
  • cbor2        (if available, via "pip install cbor2")

If neither is present and a CBOR file is encountered, an ImportError
is raised with a helpful message.

Typical usage
-------------
    from tests.harness.fixtures import find_vectors, load_vector

    vecs = find_vectors()                       # discover all
    data = load_vector(vecs[0])                 # load first
    named = load_by_basename("transfer_01")     # load by base filename
"""

from __future__ import annotations

import os
import json
import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

# Optional decoders
_msgspec_cbor_decode = None
try:
    import msgspec  # type: ignore

    # msgspec >= 0.18 exposes a cbor submodule
    if hasattr(msgspec, "cbor"):
        _msgspec_cbor_decode = msgspec.cbor.decode  # type: ignore[attr-defined]
except Exception:
    pass

_cbor2_loads = None
try:
    import cbor2  # type: ignore

    _cbor2_loads = cbor2.loads
except Exception:
    pass


@dataclass(frozen=True)
class TestVector:
    path: Path
    fmt: str       # "json" or "cbor"
    name: str      # basename without extension (for quick lookups)

    def __str__(self) -> str:
        return f"{self.name} ({self.fmt}) @ {self.path}"


# ------------------------------------------------------------------------------
# Roots & discovery
# ------------------------------------------------------------------------------

def _resolve_vectors_root() -> Path:
    """Resolve the directory containing test vectors."""
    env = os.getenv("TEST_VECTORS_DIR")
    if env:
        p = Path(env).expanduser().resolve()
        return p

    # Walk upwards looking for spec/test_vectors
    start = Path.cwd()
    target_rel = Path("spec") / "test_vectors"
    for base in (start, *start.parents):
        cand = (base / target_rel)
        if cand.is_dir():
            return cand.resolve()

    # Fallback: relative to CWD (may not exist)
    return (Path("spec") / "test_vectors").resolve()


def _guess_format(path: Path) -> Optional[str]:
    ext = path.suffix.lower()
    if ext == ".json":
        return "json"
    if ext == ".cbor":
        return "cbor"
    return None


@lru_cache(maxsize=1)
def vectors_root() -> Path:
    """Cached root path for test vectors."""
    return _resolve_vectors_root()


def find_vectors(
    *,
    root: Optional[Path] = None,
    patterns: Sequence[str] = ("**/*.json", "**/*.cbor"),
) -> List[TestVector]:
    """
    Discover vectors under `root` matching glob patterns.
    Returns a stable-sorted list of TestVector.
    """
    r = (root or vectors_root())
    if not r.exists():
        return []

    files: List[Path] = []
    for pat in patterns:
        files.extend(r.glob(pat))

    # Deduplicate and sort by relative path for stability
    uniq = sorted({f.resolve() for f in files}, key=lambda p: str(p))
    out: List[TestVector] = []
    for p in uniq:
        fmt = _guess_format(p)
        if fmt is None:
            continue
        out.append(TestVector(path=p, fmt=fmt, name=p.stem))
    return out


def iter_vectors(**kwargs) -> Iterator[TestVector]:
    """Generator over find_vectors()."""
    yield from find_vectors(**kwargs)


# ------------------------------------------------------------------------------
# Loading helpers
# ------------------------------------------------------------------------------

def _require_cbor_decoder() -> None:
    if _msgspec_cbor_decode or _cbor2_loads:
        return
    raise ImportError(
        "CBOR support is required to load *.cbor test vectors. "
        "Install one of: 'pip install msgspec[cbor]' or 'pip install cbor2'."
    )


def _load_cbor_bytes(data: bytes) -> Any:
    if _msgspec_cbor_decode:
        return _msgspec_cbor_decode(data)  # type: ignore[misc]
    if _cbor2_loads:
        return _cbor2_loads(data)          # type: ignore[misc]
    _require_cbor_decoder()
    raise AssertionError("Unreachable")  # for type-checkers


def _load_json_text(text: str) -> Any:
    return json.loads(text)


def read_bytes(path: Path) -> bytes:
    with path.open("rb") as f:
        return f.read()


def read_text(path: Path, encoding: str = "utf-8") -> str:
    with path.open("r", encoding=encoding) as f:
        return f.read()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_vector(tv: TestVector) -> Any:
    """
    Load a single TestVector into a Python object (dict/list/scalars).
    """
    if tv.fmt == "json":
        return _load_json_text(read_text(tv.path))
    if tv.fmt == "cbor":
        _require_cbor_decoder()
        return _load_cbor_bytes(read_bytes(tv.path))
    raise ValueError(f"Unknown vector format: {tv.fmt}")


def load_by_basename(
    name: str,
    *,
    root: Optional[Path] = None,
) -> Any:
    """
    Load a vector by its base filename (without extension).

    Example: load_by_basename("transfer_01")
    """
    for tv in find_vectors(root=root):
        if tv.name == name:
            return load_vector(tv)
    raise FileNotFoundError(f"No test vector named '{name}' under {root or vectors_root()}")


def load_all(
    *,
    root: Optional[Path] = None,
    formats: Sequence[str] = ("json", "cbor"),
) -> Dict[str, Any]:
    """
    Load all vectors into a dict keyed by basename. If duplicate basenames
    exist across formats, later formats in `formats` win (last one wins).
    """
    out: Dict[str, Any] = {}
    fmts = tuple(formats)
    for tv in find_vectors(root=root):
        if tv.fmt not in fmts:
            continue
        out[tv.name] = load_vector(tv)
    return out


def list_files(
    *,
    root: Optional[Path] = None,
    formats: Sequence[str] = ("json", "cbor"),
) -> List[Path]:
    """List vector file paths filtered by format."""
    fmts = set(formats)
    return [tv.path for tv in find_vectors(root=root) if tv.fmt in fmts]


# ------------------------------------------------------------------------------
# Convenience: metadata snapshot (filename, sha256)
# ------------------------------------------------------------------------------

def snapshot_metadata(
    *,
    root: Optional[Path] = None,
    formats: Sequence[str] = ("json", "cbor"),
) -> List[Dict[str, str]]:
    """
    Return a list of metadata dictionaries:
      { "name": <basename>, "format": "json|cbor", "path": "<rel or abs>", "sha256": "<hex>" }
    Useful for asserting that the test suite is running against the expected
    set of vectors without loading their structured content.
    """
    meta: List[Dict[str, str]] = []
    for tv in find_vectors(root=root):
        if tv.fmt not in formats:
            continue
        b = read_bytes(tv.path)
        meta.append(
            {
                "name": tv.name,
                "format": tv.fmt,
                "path": str(tv.path),
                "sha256": sha256_hex(b),
            }
        )
    return meta


# ------------------------------------------------------------------------------
# CLI (optional)
# ------------------------------------------------------------------------------

def _main(argv: Optional[Sequence[str]] = None) -> int:
    """
    Minimal CLI for quick debugging:
        python -m tests.harness.fixtures list
        python -m tests.harness.fixtures show transfer_01
        python -m tests.harness.fixtures meta
    """
    import argparse

    p = argparse.ArgumentParser(prog="fixtures")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")
    show = sub.add_parser("show")
    show.add_argument("name", help="basename without extension")
    sub.add_parser("meta")

    args = p.parse_args(argv)

    if args.cmd == "list":
        for tv in find_vectors():
            print(f"{tv.name}\t{tv.fmt}\t{tv.path}")
        return 0

    if args.cmd == "show":
        obj = load_by_basename(args.name)
        print(json.dumps(obj, indent=2, sort_keys=True, default=str))
        return 0

    if args.cmd == "meta":
        for m in snapshot_metadata():
            print(json.dumps(m, sort_keys=True))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
