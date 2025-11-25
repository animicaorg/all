# -*- coding: utf-8 -*-
"""
contracts.stdlib
================

A tiny helper/registry for the standard library of Animica contracts.

Goals
-----
- Provide a light-weight *runtime* discovery mechanism for stdlib modules
  (e.g., Counter, Escrow, AN20 token) without introducing heavy dependencies.
- Offer utilities to read `manifest.json`/`abi.json`, compute a reproducible
  source code hash (sha3-256 of the `contract.py` bytes), and surface a simple
  typed view for tools/tests.
- Be **tolerant** of partial checkouts: if a module directory is missing, the
  discovery logic simply skips it.

Conventions
-----------
Each stdlib module lives in a directory under this package, for example:

    contracts/stdlib/an20/
      ├── contract.py
      ├── manifest.json
      └── abi.json

This helper **does not** validate schemas; it only loads/returns JSON blobs.
Schema validation belongs to the higher-level toolchain (e.g., studio-services,
contracts/tools/build_package.py) which uses the canonical schemas in `contracts/schemas`.

CLI
---
You can quickly list discovered stdlib modules:

    python -m contracts.stdlib

This prints: name, version (from manifest if present), and paths.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from hashlib import sha3_256, sha256
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

__all__ = [
    "__version__",
    "StdlibArtifact",
    "discover_stdlib",
    "get_available",
    "get_artifacts",
    "load_manifest",
    "load_abi",
    "compute_code_hash",
    "STD_MODULE_CANDIDATES",
]

# Bump when stdlib layout/conventions change (not ABI of individual contracts).
__version__ = "0.1.0"

# Known/expected module directory names (best-effort; absence is fine)
STD_MODULE_CANDIDATES: Tuple[str, ...] = (
    "counter",
    "escrow",
    "an20",              # Fungible token (Animica AN20)
    "an721",             # Non-fungible token (Animica AN721)
    "payment_splitter",
    "multisig",
    "timelock",
    "registry",
    "da_vault",
    "ai_job_client",
    "quantum_rng",
)


@dataclass(frozen=True)
class StdlibArtifact:
    """
    A simple, typed view over one stdlib contract directory.
    All paths may be missing if the checkout is partial; consumers should check existence.
    """
    name: str
    base_dir: Path
    source_path: Path
    manifest_path: Path
    abi_path: Path
    version: Optional[str]  # from manifest["version"] if present
    code_hash: Optional[str]  # hex "0x..." if source present and readable


def _stdlib_base_dir() -> Path:
    """
    Return the base path where stdlib modules live. By default this is the directory
    of this file. Can be overridden via env `ANIMICA_STDLIB_DIR` for tooling.
    """
    env = os.getenv("ANIMICA_STDLIB_DIR")
    if env:
        p = Path(env).expanduser().resolve()
        return p
    return Path(__file__).parent.resolve()


def _safe_read_json(path: Path) -> Optional[Dict]:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # Leave validation/strict parsing to upper layers; discovery must be tolerant.
        return None
    return None


def compute_code_hash(source_path: Path) -> Optional[str]:
    """
    Compute a deterministic content hash for a contract source file.

    - Preferred: SHA3-256 (Keccak family standardized in hashlib as sha3_256).
    - Fallback: SHA-256 if SHA3 is unavailable (shouldn't happen on CPython >=3.8).

    Returns hex string (0x-prefixed) or None if the file does not exist.
    """
    if not source_path.is_file():
        return None
    try:
        data = source_path.read_bytes()
        try:
            digest = sha3_256(data).hexdigest()
        except Exception:
            digest = sha256(data).hexdigest()
        return "0x" + digest
    except Exception:
        return None


def _artifact_from_dir(name: str, base: Path) -> StdlibArtifact:
    mod_dir = base / name
    src = mod_dir / "contract.py"
    man = mod_dir / "manifest.json"
    abi = mod_dir / "abi.json"
    manifest = _safe_read_json(man)
    version = None
    if isinstance(manifest, dict):
        # Common fields: "version" or "abiVersion"; be tolerant.
        version = manifest.get("version") or manifest.get("abiVersion") or None
    code_hash = compute_code_hash(src)
    return StdlibArtifact(
        name=name,
        base_dir=mod_dir,
        source_path=src,
        manifest_path=man,
        abi_path=abi,
        version=version,
        code_hash=code_hash,
    )


def discover_stdlib(extra_names: Iterable[str] | None = None, base_dir: Optional[Path] = None) -> List[StdlibArtifact]:
    """
    Discover stdlib modules by scanning the known candidate list plus any extras.

    - Skips non-directories silently.
    - Does **not** require files to exist; returns artifacts with paths so callers can
      decide what to do.

    Args:
        extra_names: Additional directory names to try.
        base_dir: Override the stdlib base directory.

    Returns:
        List of StdlibArtifact entries (order: candidates then extras).
    """
    base = base_dir or _stdlib_base_dir()
    names: List[str] = list(STD_MODULE_CANDIDATES)
    if extra_names:
        for n in extra_names:
            if n not in names:
                names.append(n)

    artifacts: List[StdlibArtifact] = []
    for name in names:
        mod_dir = base / name
        if not mod_dir.exists():
            # It's okay for modules to be missing (planned or optional)
            continue
        if not mod_dir.is_dir():
            continue
        artifacts.append(_artifact_from_dir(name, base))
    return artifacts


def get_available() -> List[str]:
    """
    Return the list of module names that are *present* (directory exists).
    """
    base = _stdlib_base_dir()
    return sorted([p.name for p in base.iterdir() if p.is_dir()])


def get_artifacts(name: str) -> Optional[StdlibArtifact]:
    """
    Return artifact info for a specific stdlib module if its directory exists.
    """
    base = _stdlib_base_dir()
    if not (base / name).is_dir():
        return None
    return _artifact_from_dir(name, base)


def load_manifest(name: str) -> Optional[Dict]:
    """
    Load and return manifest.json for a module (tolerant of missing or invalid JSON).
    """
    art = get_artifacts(name)
    if not art:
        return None
    return _safe_read_json(art.manifest_path)


def load_abi(name: str) -> Optional[Dict]:
    """
    Load and return abi.json for a module (tolerant of missing or invalid JSON).
    """
    art = get_artifacts(name)
    if not art:
        return None
    return _safe_read_json(art.abi_path)


# ---- Pretty printer / CLI ----------------------------------------------------

def _fmt_path(p: Path) -> str:
    try:
        bp = _stdlib_base_dir()
        return str(p.relative_to(bp))
    except Exception:
        return str(p)


def _print_table(rows: List[Tuple[str, str, str, str, str]]) -> None:
    # naive fixed-width printer; avoids external deps
    widths = [max(len(row[i]) for row in rows) for i in range(5)]
    line = lambda r: "  ".join(col.ljust(widths[i]) for i, col in enumerate(r))
    hdr = ("name", "version", "code_hash", "source", "manifest")
    print(line(hdr))
    print(line(tuple("-" * w for w in widths)))
    for r in rows:
        print(line(r))


def _as_row(a: StdlibArtifact) -> Tuple[str, str, str, str, str]:
    return (
        a.name,
        a.version or "-",
        a.code_hash or "-",
        _fmt_path(a.source_path) if a.source_path.exists() else "-",
        _fmt_path(a.manifest_path) if a.manifest_path.exists() else "-",
    )


def _main() -> None:
    artifacts = discover_stdlib()
    if not artifacts:
        print("No stdlib modules discovered in:", _stdlib_base_dir())
        return
    rows = [_as_row(a) for a in artifacts]
    _print_table(rows)


if __name__ == "__main__":
    _main()
