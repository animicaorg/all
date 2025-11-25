#!/usr/bin/env python3
from __future__ import annotations
"""
omni pq verify — domain-separated post-quantum signature verification for Animica.

Examples:
  # Verify with explicit params
  python -m pq.cli.pq_verify --in msg.bin --sig msg.bin.dilithium3.sig \
      --alg dilithium3 --pk alice.pk --domain tx

  # Use JSON key (pk_hex) and metadata produced by pq_sign
  python -m pq.cli.pq_verify --in msg.bin --sig msg.bin.dilithium3.sig \
      --key ./keys/alice.json --meta msg.bin.dilithium3.sig.json

  # Signature hex on stdin; infer alg by trying supported sig-algs
  cat sig.hex | python -m pq.cli.pq_verify --in msg.bin --sig-hex - --key alice.json

  # Check that a bech32m address matches the public key/algorithm before verifying
  python -m pq.cli.pq_verify --in msg.bin --sig msg.bin.sig --key alice.json --addr anim1...
"""

import sys
import os
import json
import argparse
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

# Local package imports
try:
    from pq.py import registry as pq_registry
    from pq.py import verify as pq_verify
    from pq.py.utils.hash import sha3_256
    from pq.py import address as pq_address  # optional use
except Exception as e:  # pragma: no cover
    print("FATAL: could not import pq package. Ensure repo root is on PYTHONPATH.", file=sys.stderr)
    raise

SUPPORTED_SIG_ALGS = ("dilithium3", "sphincs-shake-128s")

# --------------------------------------------------------------------------------------
# Domain helpers (must match pq_sign.py)
# --------------------------------------------------------------------------------------

_WELL_KNOWN_LABELS = {
    "generic", "tx", "p2p", "da", "contract", "aicf", "randomness",
}

def _domain_tag_from_label(label: str) -> bytes:
    if not label:
        label = "generic"
    base = ("animica|sign|" + label.lower()).encode("utf-8")
    return sha3_256(base)

def _resolve_domain(domain_arg: Optional[str], prehash_mode: str) -> Tuple[str, bytes]:
    """
    Accept label/custom/hex just like pq_sign.py and fold the prehash tag into the domain.
    """
    if domain_arg is None:
        label = "generic"
        tag = _domain_tag_from_label(label)
        return (label, _fold_prehash(tag, prehash_mode))

    if domain_arg.startswith("hex:"):
        hexstr = domain_arg[4:].strip().lower()
        try:
            b = bytes.fromhex(hexstr)
        except ValueError:
            raise SystemExit("Invalid --domain hex form; expected hex after 'hex:'.")
        if len(b) == 0:
            raise SystemExit("Empty domain bytes are not allowed.")
        return ("custom-hex", _fold_prehash(b, prehash_mode))

    if domain_arg.startswith("custom:"):
        label = domain_arg.split(":", 1)[1].strip()
        if not label:
            raise SystemExit("custom domain label cannot be empty. e.g., --domain custom:mytool")
        return (label, _fold_prehash(_domain_tag_from_label(label), prehash_mode))

    # plain label
    label = domain_arg.strip().lower()
    return (label, _fold_prehash(_domain_tag_from_label(label), prehash_mode))

def _fold_prehash(base_tag: bytes, prehash_mode: str) -> bytes:
    """
    If the message was prehashed before signing, pq_sign.py incorporates a small tag into the domain:
        domain' = sha3_256(domain || b"|ph:sha3-256")
    Mirror that here for verification.
    """
    if prehash_mode == "sha3-256":
        return sha3_256(base_tag + b"|ph:sha3-256")
    return base_tag

# --------------------------------------------------------------------------------------
# Inputs: pk/sig/meta
# --------------------------------------------------------------------------------------

def _load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _load_pk_from_keyjson(path: Path) -> Tuple[bytes, Optional[str]]:
    j = _load_json(path)
    pk_hex = j.get("pk_hex")
    if not pk_hex:
        raise SystemExit("Key JSON missing 'pk_hex'. Use a file produced by pq_keygen.")
    try:
        pk = bytes.fromhex(pk_hex)
    except ValueError:
        raise SystemExit("Invalid pk_hex in key JSON.")
    addr = j.get("address") or None
    return pk, addr

def _load_pk(path: Path) -> bytes:
    if path.suffix.lower() == ".json":
        pk, _ = _load_pk_from_keyjson(path)
        return pk
    return path.read_bytes()

def _maybe_check_address(addr_str: Optional[str], alg: Optional[str], pk: Optional[bytes]) -> Optional[str]:
    """
    If address string is provided and pq_address has helpers, ensure it matches the given alg+pk.
    Returns a warning string if we could not check; raises SystemExit on mismatch.
    """
    if not addr_str:
        return None
    warn: Optional[str] = None
    try:
        # Prefer a direct re-derive if available
        if hasattr(pq_address, "address_from_pubkey") and alg and pk:
            derived = pq_address.address_from_pubkey(alg, pk)  # type: ignore[attr-defined]
            if derived != addr_str:
                raise SystemExit(f"Address mismatch: provided {addr_str} != derived {derived} from alg+pk.")
            return None
        elif hasattr(pq_address, "decode_address"):
            _alg_id, _digest = pq_address.decode_address(addr_str)  # type: ignore[attr-defined]
            # Without a canonical reverse map to alg/pk, accept decode-only as a weak check.
            warn = "address decoded but could not be cross-checked against pk (no helper available)"
        else:
            warn = "address check skipped (pq.address helpers not available)"
    except Exception as e:
        raise SystemExit(f"Invalid address {addr_str}: {e}")
    return warn

def _read_sig(sig_path: Optional[Path], sig_hex: Optional[str]) -> bytes:
    if sig_path and sig_hex:
        raise SystemExit("Provide only one of --sig or --sig-hex.")
    if sig_path:
        return sig_path.read_bytes()
    if sig_hex is not None:
        data = sys.stdin.read().strip() if sig_hex == "-" else sig_hex.strip()
        try:
            return bytes.fromhex(data)
        except ValueError:
            raise SystemExit("Invalid --sig-hex (must be hex string or '-' for stdin).")
    raise SystemExit("Provide --sig <file> or --sig-hex <hex|->.")

# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="omni pq verify", description="Verify Animica PQ signatures.")
    ap.add_argument("--in", dest="in_path", required=True, type=Path, help="Input message file that was signed.")
    gsig = ap.add_mutually_exclusive_group(required=True)
    gsig.add_argument("--sig", dest="sig_path", type=Path, help="Signature file (binary).")
    gsig.add_argument("--sig-hex", dest="sig_hex", help="Signature hex string, or '-' to read from stdin.")
    ap.add_argument("--alg", help=f"Signature algorithm to use ({', '.join(SUPPORTED_SIG_ALGS)}). "
                                  "If omitted, the verifier will try supported algorithms.")
    gk = ap.add_mutually_exclusive_group(required=True)
    gk.add_argument("--pk", type=Path, help="Public-key file (raw bytes).")
    gk.add_argument("--key", type=Path, help="Key JSON (from pq_keygen; uses pk_hex).")
    ap.add_argument("--addr", help="Optional bech32m address to cross-check pk/alg compatibility.")
    ap.add_argument("--domain", default="tx",
                    help="Domain label: tx|p2p|da|contract|aicf|randomness|generic, "
                         "or custom:<label>, or hex:<bytes>. Default: tx")
    ap.add_argument("--prehash", choices=["none", "sha3-256"], default="none",
                    help="If the message was prehashed before signing, set this accordingly.")
    ap.add_argument("--meta", type=Path, help="Optional metadata JSON (from pq_sign) to cross-check fields.")
    ap.add_argument("--use-meta", action="store_true",
                    help="Fill in missing --alg/--domain/--prehash from --meta automatically.")
    ap.add_argument("--json", action="store_true", help="Emit a JSON result object.")
    args = ap.parse_args(argv)

    # Load inputs
    msg_path: Path = args.in_path
    if not msg_path.exists():
        raise SystemExit(f"Input file not found: {msg_path}")
    msg = msg_path.read_bytes()
    sig = _read_sig(args.sig_path, args.sig_hex)

    # Meta (optional)
    meta: Dict[str, Any] = {}
    if args.meta:
        try:
            meta = _load_json(args.meta)
        except Exception as e:
            raise SystemExit(f"Failed to read --meta: {e}")

    alg = (args.alg or (meta.get("alg") if args.use_meta else None))
    prehash_mode = args.prehash if not args.use_meta else (args.prehash if args.prehash != "none" else meta.get("prehash", "none"))
    domain_label = args.domain if not args.use_meta else (args.domain if args.domain != "tx" else meta.get("domain_label", "tx"))

    # Prehash handling (must match pq_sign)
    if prehash_mode == "sha3-256":
        msg = sha3_256(msg)

    # Resolve domain
    domain_label, domain_tag = _resolve_domain(domain_label, prehash_mode)

    # Public key
    pk: bytes
    addr_from_key: Optional[str] = None
    if args.key:
        pk, addr_from_key = _load_pk_from_keyjson(args.key)
    else:
        pk = _load_pk(args.pk)

    # Optional address check
    addr_warn = _maybe_check_address(args.addr or addr_from_key, alg, pk)

    # Try verification
    tried_algs = []
    valid_alg: Optional[str] = None
    valid = False
    error: Optional[str] = None

    def _try(alg_name: str) -> bool:
        try:
            return pq_verify.verify_detached(alg_name, pk, msg, sig, domain=domain_tag)
        except pq_registry.PQNotAvailableError as e:
            # Environment missing PQ backend for this alg; treat as a hard failure for this alg
            nonlocal error
            error = f"{e}"
            return False
        except Exception as e:
            nonlocal error
            error = f"{e}"
            return False

    if alg:
        alg_norm = alg.strip().lower()
        tried_algs.append(alg_norm)
        valid = _try(alg_norm)
        if valid:
            valid_alg = alg_norm
    else:
        # Probe supported signature algorithms
        for cand in SUPPORTED_SIG_ALGS:
            tried_algs.append(cand)
            if _try(cand):
                valid = True
                valid_alg = cand
                break

    # Cross-check meta (if provided)
    meta_ok = True
    meta_mismatch: Dict[str, str] = {}
    if args.meta:
        if "alg" in meta and valid_alg and meta["alg"].lower() != valid_alg:
            meta_ok = False
            meta_mismatch["alg"] = f"{meta['alg']} != {valid_alg}"
        if "domain_label" in meta and meta["domain_label"] != domain_label:
            meta_ok = False
            meta_mismatch["domain_label"] = f"{meta['domain_label']} != {domain_label}"
        if "prehash" in meta and str(meta["prehash"]) != str(prehash_mode):
            meta_ok = False
            meta_mismatch["prehash"] = f"{meta['prehash']} != {prehash_mode}"

    # Output
    if args.json:
        out = {
            "valid": bool(valid),
            "algorithm": valid_alg,
            "tried_algs": tried_algs,
            "domain_label": domain_label,
            "prehash": prehash_mode,
            "addr_check_warning": addr_warn,
            "meta_checked": bool(args.meta is not None),
            "meta_ok": bool(meta_ok),
            "meta_mismatch": meta_mismatch,
            "error": error,
        }
        print(json.dumps(out, indent=2))
    else:
        if valid:
            print(f"VALID ✓  (alg={valid_alg}, domain={domain_label}, prehash={prehash_mode})")
            if addr_warn:
                print(f"  note: {addr_warn}")
            if args.meta:
                print(f"  meta: {'ok' if meta_ok else 'mismatch'}")
                if meta_mismatch:
                    for k, v in meta_mismatch.items():
                        print(f"    - {k}: {v}")
        else:
            print(f"INVALID ✗  (tried={tried_algs}; domain={domain_label}; prehash={prehash_mode})")
            if error:
                print(f"  error: {error}")

    # Exit code: 0 valid, 1 invalid, 2 meta mismatch (if otherwise valid)
    if valid and meta_ok:
        return 0
    if valid and not meta_ok:
        return 2
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
