#!/usr/bin/env python3
from __future__ import annotations

"""
omni pq keygen — generate Animica PQ keypairs (signing or KEM).

Usage examples:
  # Dilithium3 signing key (default), write to ./keys/
  python -m pq.cli.pq_keygen --alg dilithium3 --out-dir ./keys --name alice

  # SPHINCS+-SHAKE-128s signing key, print JSON to stdout only
  python -m pq.cli.pq_keygen --alg sphincs-shake-128s --stdout

  # ML-KEM-768 (Kyber) KEM key for P2P handshakes (no address derived)
  python -m pq.cli.pq_keygen --alg kyber768 --kem --out-dir ./p2p --name node01

  # Allow *insecure* pure-Python fallbacks (devnet only)
  ANIMICA_ALLOW_PQ_PURE_FALLBACK=1 python -m pq.cli.pq_keygen --alg dilithium3 --stdout

Notes:
  • Address bech32m (anim1...) is derived for *signing* algs only (Dilithium3, SPHINCS+).
  • KEM keys (Kyber/ML-KEM-768) are for P2P handshake & do not produce an account address.
"""

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Dict

# Local imports from the pq package
try:
    from pq.py import address as pq_address
    from pq.py import keygen as pq_keygen
    from pq.py import registry as pq_registry
except Exception as e:  # pragma: no cover
    print(
        "FATAL: could not import pq package. Ensure your PYTHONPATH includes repo root.",
        file=sys.stderr,
    )
    raise

# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _secure_write(path: Path, data: bytes, secret: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    if secret:
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
        except Exception:
            pass


def _as_hex(b: bytes) -> str:
    return b.hex()


def _is_kem_name(name: str) -> bool:
    n = name.lower()
    return n.startswith("kyber") or "kem" in n


def _detect_kind(alg: str, kem_flag: bool | None) -> str:
    if kem_flag is True:
        return "kem"
    if kem_flag is False:
        return "sig"
    # auto-detect from name
    return "kem" if _is_kem_name(alg) else "sig"


def _derive_address_or_none(alg: str, pk: bytes) -> str | None:
    # Only signatures map to account addresses
    if _is_kem_name(alg):
        return None
    try:
        return pq_address.address_from_pubkey(alg, pk)
    except Exception:
        # Fallback to None if registry/policy not available
        return None


def _lengths_for(alg: str, kind: str) -> Dict[str, int]:
    try:
        if kind == "sig":
            meta = pq_registry.signature_alg_info(alg)
            return {"pk": meta.pk_len, "sk": meta.sk_len, "sig": meta.sig_len}
        else:
            meta = pq_registry.kem_alg_info(alg)
            return {
                "pk": meta.pk_len,
                "sk": meta.sk_len,
                "ct": meta.ct_len,
                "ss": meta.ss_len,
            }
    except Exception:
        return {}


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="omni pq keygen", description="Generate Animica post-quantum keypairs."
    )
    p.add_argument(
        "--alg",
        required=True,
        help="Algorithm: dilithium3 | sphincs-shake-128s | kyber768",
    )
    p.add_argument(
        "--kem",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Force KEM (True) or signature (False). Default: auto-detect by name.",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Directory to write {name}.sk/.pk/.json",
    )
    p.add_argument(
        "--name", default=None, help="Base filename (default: <alg>_<short>)"
    )
    p.add_argument(
        "--stdout", action="store_true", help="Print JSON to stdout (keys hex-encoded)"
    )
    p.add_argument(
        "--no-files",
        action="store_true",
        help="Do not write any files (implies --stdout)",
    )
    p.add_argument(
        "--json",
        dest="json_only",
        action="store_true",
        help="Only write {name}.json (no raw .sk/.pk)",
    )
    p.add_argument(
        "--addr-only",
        action="store_true",
        help="Only print derived address (if signature alg)",
    )
    args = p.parse_args(argv)

    alg = args.alg.strip().lower()
    kind = _detect_kind(alg, args.kem)

    # Generate
    if kind == "sig":
        sk, pk = pq_keygen.sig_keygen(alg)  # type: ignore[attr-defined]
        address = _derive_address_or_none(alg, pk)
        result: Dict[str, Any] = {
            "kind": "signature",
            "alg": alg,
            "lengths": _lengths_for(alg, "sig"),
            "pk_hex": _as_hex(pk),
            "sk_hex": _as_hex(sk),
            "address": address,
        }
    else:
        sk, pk = pq_keygen.kem_keygen(alg)  # type: ignore[attr-defined]
        result = {
            "kind": "kem",
            "alg": alg,
            "lengths": _lengths_for(alg, "kem"),
            "pk_hex": _as_hex(pk),
            "sk_hex": _as_hex(sk),
            "note": "KEM keys do not produce account addresses.",
        }

    # addr-only mode
    if args.addr_only:
        addr = result.get("address")
        if addr:
            print(addr)
            return 0
        print("no-address-for-alg", file=sys.stderr)
        return 2

    # Decide file names
    short = result["pk_hex"][:16]
    base_name = args.name or f"{alg}_{short}"
    out_dir = args.out_dir or Path.cwd()
    json_path = out_dir / f"{base_name}.json"
    pk_path = out_dir / f"{base_name}.pk"
    sk_path = out_dir / f"{base_name}.sk"

    # Emit JSON to stdout
    if args.stdout or args.no_files:
        print(json.dumps(result, indent=2))

    # Write files?
    if not args.no_files:
        # JSON
        _secure_write(
            json_path, json.dumps(result, indent=2).encode("utf-8"), secret=False
        )
        # Raw blobs unless json-only
        if not args.json_only:
            _secure_write(pk_path, bytes.fromhex(result["pk_hex"]), secret=False)
            _secure_write(sk_path, bytes.fromhex(result["sk_hex"]), secret=True)

        # Friendly message
        sys.stderr.write(
            f"[ok] wrote: {json_path.name}"
            + ("" if args.json_only else f", {pk_path.name}, {sk_path.name} (0600)")
            + f" in {str(out_dir)}\n"
        )

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
