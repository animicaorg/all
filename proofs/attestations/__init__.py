"""
Animica | proofs.attestations

Purpose
-------
Common utilities and light-weight glue for attestation subsystems used by
proofs/ai.py and proofs/quantum.py:

- Vendor trust roots loader (PEM bundles shipped under vendor_roots/)
- Minimal typed container for root sets
- Convenience helpers to access TEE/QPU-specific verifiers implemented in:
    proofs.attestations.tee.common
    proofs.attestations.tee.sgx
    proofs.attestations.tee.sev_snp
    proofs.attestations.tee.cca
    proofs.attestations.tpm_dice
- Lazy import pattern so importing `proofs` stays fast and optional backends
  can be missing without breaking the world (callers handle NotImplemented).

Notes
-----
All heavy parsing/validation lives in the submodules (sgx.py, sev_snp.py, ...).
This package provides *data access* and *registry glue* only.

The shipped PEMs are *illustrative placeholders*; networks MUST update roots
via governance before production (see proofs/attestations/vendor_roots/README.md).
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

try:
    # Python 3.9+: modern resource API
    from importlib.resources import files as _pkg_files
except Exception:  # pragma: no cover - py<3.9 fallback
    _pkg_files = None  # type: ignore[assignment]

# Local error type (soft dependency during early bootstrap)
try:
    from proofs.errors import SchemaError
except Exception:  # pragma: no cover
    class SchemaError(Exception):
        pass


# --------------------------------------------------------------------------------------
# Trust roots container
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class AttestationRoots:
    """In-memory bundle of vendor trust roots (PEM-encoded)."""
    sgx_root_pem: Optional[bytes] = None
    amd_sev_snp_root_pem: Optional[bytes] = None
    arm_cca_root_pem: Optional[bytes] = None
    qpu_provider_root_pem: Optional[bytes] = None  # demo placeholder

    def as_trust_store(self) -> List[Tuple[str, bytes]]:
        """Return a list of (name, pem_bytes) pairs suitable for verifiers."""
        out: List[Tuple[str, bytes]] = []
        if self.sgx_root_pem:
            out.append(("intel_sgx", self.sgx_root_pem))
        if self.amd_sev_snp_root_pem:
            out.append(("amd_sev_snp", self.amd_sev_snp_root_pem))
        if self.arm_cca_root_pem:
            out.append(("arm_cca", self.arm_cca_root_pem))
        if self.qpu_provider_root_pem:
            out.append(("qpu_demo", self.qpu_provider_root_pem))
        return out


# --------------------------------------------------------------------------------------
# Resource helpers
# --------------------------------------------------------------------------------------

def _vendor_dir() -> Path:
    """
    Locate the vendor_roots/ directory whether installed as a package or from source.
    """
    # Try importlib.resources first (installed package)
    if _pkg_files is not None:
        try:
            return Path(_pkg_files(__package__) / "vendor_roots")
        except Exception:
            pass
    # Fallback: resolve relative to this file (editable installs / source tree)
    here = Path(__file__).resolve().parent
    return here / "vendor_roots"


def list_vendor_roots() -> List[str]:
    """
    Return available root filenames (e.g., ["intel_sgx_root.pem", ...]).
    """
    d = _vendor_dir()
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.suffix.lower() == ".pem")


def read_vendor_root(name: str) -> bytes:
    """
    Read a specific vendor root PEM by filename (e.g., "intel_sgx_root.pem").
    Raises SchemaError if missing/unreadable.
    """
    p = _vendor_dir() / name
    try:
        data = p.read_bytes()
        # Tiny sanity: looks like a PEM
        if b"-----BEGIN" not in data:
            raise SchemaError(f"Vendor root {name} does not look like PEM")
        return data
    except FileNotFoundError as e:
        raise SchemaError(f"Vendor root {name} not found at {p}") from e
    except Exception as e:  # noqa: PERF203
        raise SchemaError(f"Failed reading vendor root {name}: {e}") from e


def load_default_roots() -> AttestationRoots:
    """
    Load the default shipped PEMs into an AttestationRoots object.

    Filenames:
      - intel_sgx_root.pem
      - amd_sev_snp_root.pem
      - arm_cca_root.pem
      - example_qpu_root.pem   (placeholder for demo QPU providers)

    Missing files are tolerated (fields become None).
    """
    names = {
        "sgx_root_pem": "intel_sgx_root.pem",
        "amd_sev_snp_root_pem": "amd_sev_snp_root.pem",
        "arm_cca_root_pem": "arm_cca_root.pem",
        "qpu_provider_root_pem": "example_qpu_root.pem",
    }
    payload: Dict[str, Optional[bytes]] = {}
    for fld, fname in names.items():
        try:
            payload[fld] = read_vendor_root(fname)
        except SchemaError:
            payload[fld] = None
    return AttestationRoots(**payload)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# Lazy accessors for verifier modules (do not import heavy deps on package import)
# --------------------------------------------------------------------------------------

def tee_common():
    """Return the tee.common module (lazy import)."""
    from .tee import common as _common  # local import
    return _common


def tee_sgx():
    """Return the tee.sgx verifier module (lazy import)."""
    from .tee import sgx as _sgx  # local import
    return _sgx


def tee_sev_snp():
    """Return the tee.sev_snp verifier module (lazy import)."""
    from .tee import sev_snp as _sev  # local import
    return _sev


def tee_cca():
    """Return the tee.cca verifier module (lazy import)."""
    from .tee import cca as _cca  # local import
    return _cca


def tpm_dice():
    """Return the tpm_dice validator module (lazy import)."""
    from . import tpm_dice as _tpm  # local import
    return _tpm


# --------------------------------------------------------------------------------------
# Public exports
# --------------------------------------------------------------------------------------

__all__ = [
    "AttestationRoots",
    "list_vendor_roots",
    "read_vendor_root",
    "load_default_roots",
    # Lazy module getters
    "tee_common",
    "tee_sgx",
    "tee_sev_snp",
    "tee_cca",
    "tpm_dice",
]
