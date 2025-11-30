#!/usr/bin/env python3
from __future__ import annotations

"""
omni pq sign — domain-separated PQ signatures for Animica.

Examples:
  # Sign a binary message with Dilithium3; write .sig and .sig.json next to the file
  python -m pq.cli.pq_sign --in msg.bin --alg dilithium3 --key ./keys/alice_dilithium3.json

  # SPHINCS+-SHAKE-128s; print signature hex to stdout only
  python -m pq.cli.pq_sign --in msg.bin --alg sphincs-shake-128s --key ./keys/bob.json --stdout

  # Use a raw .sk file (binary), and a well-known domain label "tx"
  python -m pq.cli.pq_sign --in tx.cbor --alg dilithium3 --sk ./keys/alice_dilithium3.sk --domain tx

  # Provide a custom domain label; also verify against a given public key after signing
  python -m pq.cli.pq_sign --in payload.bin --alg dilithium3 --key alice.json --domain custom:mytool \
      --verify-with alice.json
"""

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Local package imports
try:
    from pq.py import address as pq_address
    from pq.py import registry as pq_registry
    from pq.py import sign as pq_sign
    from pq.py import verify as pq_verify
    from pq.py.utils.hash import sha3_256
except Exception as e:  # pragma: no cover
    print(
        "FATAL: could not import pq package. Ensure repo root is on PYTHONPATH.",
        file=sys.stderr,
    )
    raise

# --------------------------------------------------------------------------------------
# Domains
# --------------------------------------------------------------------------------------

# Well-known signing domain labels → deterministic 32-byte tags (derived from spec/domains.yaml semantics)
# We derive: tag = sha3_256(b"animica|sign|" + label.lower().encode()).  Keep in sync with node.
_WELL_KNOWN_LABELS = {
    "generic",
    "tx",  # transaction sign-bytes
    "p2p",  # P2P auth
    "da",  # data-availability envelope signing
    "contract",  # contract package/manifest
    "aicf",  # AI/Quantum job tickets
    "randomness",  # beacon commits/reveals
}


def _domain_tag_from_label(label: str) -> bytes:
    if not label:
        label = "generic"
    base = ("animica|sign|" + label.lower()).encode("utf-8")
    return sha3_256(base)


def _resolve_domain(domain_arg: Optional[str]) -> Tuple[str, bytes]:
    """
    Accepts:
      • None → "generic"
      • known label (e.g., "tx", "p2p") → sha3_256("animica|sign|<label>")
      • "custom:<label>" → same derivation but not checked against well-known set
      • "hex:<...>" → explicit hex bytes for advanced users
    """
    if domain_arg is None:
        label = "generic"
        return (label, _domain_tag_from_label(label))

    if domain_arg.startswith("hex:"):
        hexstr = domain_arg[4:].strip().lower()
        try:
            b = bytes.fromhex(hexstr)
        except ValueError:
            raise SystemExit("Invalid --domain hex form; expected hex after 'hex:'.")
        if len(b) == 0:
            raise SystemExit("Empty domain bytes are not allowed.")
        return ("custom-hex", b)

    if domain_arg.startswith("custom:"):
        label = domain_arg.split(":", 1)[1].strip()
        if not label:
            raise SystemExit(
                "custom domain label cannot be empty. e.g., --domain custom:mytool"
            )
        return (label, _domain_tag_from_label(label))

    # plain label
    label = domain_arg.strip().lower()
    return (label, _domain_tag_from_label(label))


# --------------------------------------------------------------------------------------
# Key loading
# --------------------------------------------------------------------------------------


def _load_json_key(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            raise SystemExit(f"Failed to parse JSON key file: {path} ({e})")


def _load_sk_and_meta(
    alg_cli: Optional[str], key_path: Optional[Path], sk_path: Optional[Path]
) -> Tuple[str, bytes, Optional[bytes], Optional[str]]:
    """
    Returns (alg, sk_bytes, pk_bytes_or_none, address_or_none)
    Accepts either --key (JSON produced by pq_keygen) or --sk (raw secret key bytes).
    """
    if key_path is None and sk_path is None:
        raise SystemExit("Provide --key <json> or --sk <file>.")
    if key_path is not None and sk_path is not None:
        raise SystemExit("Use only one: --key or --sk.")

    pk: Optional[bytes] = None
    addr: Optional[str] = None

    if key_path:
        data = _load_json_key(key_path)
        # Try to infer algorithm from JSON
        alg_json = (data.get("alg") or data.get("algorithm") or "").lower()
        alg = (alg_cli or alg_json).strip().lower()
        if not alg:
            raise SystemExit("No --alg provided and JSON did not contain 'alg'.")
        # Ensure signature algorithm (not KEM)
        if "kyber" in alg or "kem" in alg:
            raise SystemExit(
                "KEM keys cannot sign. Use a signature algorithm (dilithium3 or sphincs-shake-128s)."
            )

        sk_hex = data.get("sk_hex")
        if not sk_hex:
            raise SystemExit(
                "JSON did not contain 'sk_hex'. Use a key file produced by pq_keygen."
            )
        try:
            sk = bytes.fromhex(sk_hex)
        except ValueError:
            raise SystemExit("Invalid 'sk_hex' in JSON.")

        pk_hex = data.get("pk_hex")
        if pk_hex:
            try:
                pk = bytes.fromhex(pk_hex)
            except ValueError:
                pk = None

        addr = data.get("address") or None
        return alg, sk, pk, addr

    # Raw .sk path
    assert sk_path is not None
    if not alg_cli:
        raise SystemExit("When using --sk, you must provide --alg.")
    alg = alg_cli.strip().lower()
    if "kyber" in alg or "kem" in alg:
        raise SystemExit(
            "KEM keys cannot sign. Use a signature algorithm (dilithium3 or sphincs-shake-128s)."
        )
    with open(sk_path, "rb") as f:
        sk = f.read()
    # No pk or address unless the user later supplies --pk/--addr; we try deriving address if possible:
    try:
        # Some algs embed pk in sk; registry may know how to split; otherwise skip.
        # If not available, derive address from a provided public key via JSON key.
        pass
    except Exception:
        pass
    return alg, sk, pk, addr


# --------------------------------------------------------------------------------------
# Output helpers
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


def _default_out_paths(msg_path: Path, alg: str) -> Tuple[Path, Path]:
    sig_path = msg_path.with_suffix(msg_path.suffix + f".{alg}.sig")
    meta_path = sig_path.with_suffix(sig_path.suffix + ".json")
    return sig_path, meta_path


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="omni pq sign", description="Post-quantum signing CLI (domain-separated)."
    )
    ap.add_argument(
        "--in",
        dest="in_path",
        required=True,
        type=Path,
        help="Input message file to sign.",
    )
    ap.add_argument(
        "--alg",
        required=False,
        help="Signature algorithm: dilithium3 | sphincs-shake-128s",
    )
    gk = ap.add_mutually_exclusive_group(required=True)
    gk.add_argument(
        "--key", type=Path, help="Key JSON produced by pq_keygen (recommended)."
    )
    gk.add_argument(
        "--sk", type=Path, help="Raw secret-key file (binary). Requires --alg."
    )
    ap.add_argument(
        "--domain",
        default="tx",
        help="Domain label: tx|p2p|da|contract|aicf|randomness|generic, "
        "or custom:<label>, or hex:<bytes>. Default: tx",
    )
    ap.add_argument(
        "--prehash",
        choices=["none", "sha3-256"],
        default="none",
        help="Optionally prehash the input before signing (domain includes a tag).",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write signature to this path. Default derives from --in.",
    )
    ap.add_argument(
        "--stdout", action="store_true", help="Print signature hex to stdout."
    )
    ap.add_argument(
        "--json",
        dest="json_only",
        action="store_true",
        help="Only write metadata JSON (with sig_hex).",
    )
    ap.add_argument(
        "--no-files",
        action="store_true",
        help="Do not write any files (implies --stdout).",
    )
    ap.add_argument(
        "--verify-with",
        type=Path,
        default=None,
        help="Optional public key or JSON (same format as pq_keygen .json) to self-verify after signing.",
    )
    args = ap.parse_args(argv)

    # Load message
    msg_path: Path = args.in_path
    if not msg_path.exists():
        raise SystemExit(f"Input file not found: {msg_path}")
    msg = msg_path.read_bytes()

    # Prehash if requested
    prehash_tag = b""
    if args.prehash == "sha3-256":
        msg = sha3_256(msg)
        prehash_tag = b"|ph:sha3-256"

    # Resolve domain
    domain_label, domain = _resolve_domain(args.domain)
    domain = (
        sha3_256(domain + prehash_tag) if prehash_tag else domain
    )  # incorporate prehash mode into domain tag

    # Load keys
    alg, sk, pk_opt, addr_opt = _load_sk_and_meta(args.alg, args.key, args.sk)

    # Perform signature
    try:
        sig = pq_sign.sign_detached(alg, sk, msg, domain=domain)  # type: ignore[arg-type]
    except pq_registry.PQNotAvailableError as e:
        raise SystemExit(
            f"{e}. (Set ANIMICA_ALLOW_PQ_PURE_FALLBACK=1 to allow slow fallbacks if available.)"
        )
    except Exception as e:
        raise SystemExit(f"Signing failed: {e}")

    # Self-verify if requested
    verify_ok: Optional[bool] = None
    verify_error: Optional[str] = None
    if args.verify_with:
        # Load verify key
        vk_path: Path = args.verify_with
        vk_bytes: Optional[bytes] = None
        if vk_path.suffix.lower() == ".json":
            j = _load_json_key(vk_path)
            vk_hex = j.get("pk_hex")
            if not vk_hex:
                verify_error = "verify-with JSON missing pk_hex"
            else:
                try:
                    vk_bytes = bytes.fromhex(vk_hex)
                except ValueError:
                    verify_error = "verify-with pk_hex invalid hex"
        else:
            # Assume raw bytes file
            try:
                vk_bytes = vk_path.read_bytes()
            except Exception as e:
                verify_error = f"verify-with read error: {e}"

        if vk_bytes is not None:
            try:
                verify_ok = pq_verify.verify_detached(
                    alg, vk_bytes, msg, sig, domain=domain
                )
            except Exception as e:
                verify_ok = False
                verify_error = f"verify exception: {e}"

    # Derive default outputs
    sig_path, meta_path = _default_out_paths(msg_path, alg)
    if args.out:
        sig_path = args.out
        # place meta next to explicit out
        meta_path = sig_path.with_suffix(sig_path.suffix + ".json")

    # stdout?
    if args.stdout or args.no_files:
        print(sig.hex())

    # Write files unless disabled
    if not args.no_files:
        if not args.json_only:
            _secure_write(sig_path, sig, secret=False)

        meta: Dict[str, Any] = {
            "tool": "omni-pq-sign",
            "alg": alg,
            "domain_label": domain_label,
            "domain_hex": domain.hex(),
            "prehash": args.prehash,
            "input_file": str(msg_path),
            "input_len": msg_path.stat().st_size,
            "sig_hex": sig.hex(),
        }
        # Enrich meta when available
        if pk_opt is not None:
            meta["pk_hex"] = pk_opt.hex()
        if addr_opt:
            meta["address"] = addr_opt
        if verify_ok is not None:
            meta["self_verify_ok"] = bool(verify_ok)
        if verify_error:
            meta["self_verify_error"] = verify_error

        _secure_write(
            meta_path, json.dumps(meta, indent=2).encode("utf-8"), secret=False
        )

        sys.stderr.write(
            f"[ok] signature written to {sig_path.name} and metadata to {meta_path.name}\n"
        )

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
