#!/usr/bin/env python3
"""
Animica zk.registry.update_vk
=============================

CLI to **add / replace / sign / verify** Verifying Keys (VKs) in
`zk/registry/vk_cache.json` with strong, canonical hashing (SHA3-256)
and optional signatures. It can also (optionally) keep
`zk/registry/registry.yaml` in sync by updating the `vk_hash` for the
corresponding `circuits.<circuit_id>` entry.

Design goals
------------
- Deterministic hashing of VK JSON via canonical JSON (sorted keys, no spaces).
- Explicit schema_version checks.
- Per-entry signature support:
  - Ed25519 (preferred) if PyNaCl is available.
  - HMAC-SHA3-256 as a fallback (shared-secret MAC).
- Safe, atomic writes.
- Zero non-stdlib hard dependencies (PyYAML & PyNaCl are optional).

Entry shape (vk_cache.json)
---------------------------
{
  "schema_version": "1",
  "entries": {
    "<circuit_id>": {
      "kind": "groth16_bn254" | "plonk_kzg_bn254" | "stark_fri_merkle" | ...,
      "vk_format": "snarkjs" | "plonkjs" | "fri_params" | ...,
      "vk": {...} | null,
      "fri_params": {...} | null,         # optional; e.g. for STARK toy verifier
      "vk_hash": "sha3-256:<hex>",
      "meta": {...},                      # optional
      "sig": {                            # optional
        "alg": "ed25519" | "hmac-sha3-256",
        "key_id": "<string>",
        "signature": "<hex>"
      }
    }
  }
}

Usage
-----
# Add a Groth16 VK from file (JSON), compute & store hash
python -m zk.registry.update_vk add \\
  --circuit-id counter_groth16_bn254@2 \\
  --kind groth16_bn254 \\
  --vk-format snarkjs \\
  --vk-file path/to/vk.json

# Overwrite if already present
... add --overwrite ...

# Sign an entry (Ed25519; requires PyNaCl)
python -m zk.registry.update_vk sign \\
  --circuit-id counter_groth16_bn254@2 \\
  --ed25519-secret-key <hex> --key-id my-key-2025Q4

# Verify hash & signature
python -m zk.registry.update_vk verify \\
  --circuit-id counter_groth16_bn254@2 \\
  --ed25519-public-key <hex>

# Update registry.yaml's circuits.<id>.vk_hash (if PyYAML installed)
python -m zk.registry.update_vk sync-registry --circuit-id counter_groth16_bn254@2

License: MIT
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from hashlib import sha3_256
from pathlib import Path
from typing import Any, Dict, Optional

VK_CACHE_PATH = Path(__file__).with_name("vk_cache.json")
REGISTRY_YAML_PATH = Path(__file__).with_name("registry.yaml")

# --- Optional deps ---
try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # graceful fallback

try:
    from nacl import signing  # type: ignore
    from nacl.encoding import RawEncoder  # type: ignore
except Exception:  # pragma: no cover
    signing = None
    RawEncoder = None


# =============================================================================
# Utilities
# =============================================================================

def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _dump_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
    data = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    # Write atomically
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmpf:
            tmpf.write(data)
        os.replace(tmp_path, path)
    except Exception:
        # Cleanup tmp if replace failed
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise


def _canonical_json_bytes(obj: Any) -> bytes:
    """Deterministic JSON bytes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha3_256_hex(data: bytes) -> str:
    return sha3_256(data).hexdigest()


def _compute_vk_hash(entry: Dict[str, Any]) -> str:
    """
    Compute the canonical hash of the VK *material*.
    We bind the following fields into the hash payload:
      - kind
      - vk_format
      - vk (if present)
      - fri_params (if present)
    """
    payload = {
        "kind": entry.get("kind"),
        "vk_format": entry.get("vk_format"),
        # Only include one of these if present; some schemes don't use both.
        "vk": entry.get("vk", None),
        "fri_params": entry.get("fri_params", None),
    }
    return f"sha3-256:{_sha3_256_hex(_canonical_json_bytes(payload))}"


def _ensure_schema(cache: Dict[str, Any]) -> None:
    if cache.get("schema_version") != "1":
        raise SystemExit("vk_cache.json schema_version must be '1'.")


def _load_vk_cache(path: Path = VK_CACHE_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {"schema_version": "1", "entries": {}}
    cache = _load_json(path)
    _ensure_schema(cache)
    cache.setdefault("entries", {})
    return cache


def _write_vk_cache(cache: Dict[str, Any], path: Path = VK_CACHE_PATH) -> None:
    _ensure_schema(cache)
    _dump_json_atomic(path, cache)


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit(msg)


def _hex_to_bytes(s: str) -> bytes:
    s = s[2:] if s.startswith("0x") else s
    return bytes.fromhex(s)


def _bytes_to_hex(b: bytes) -> str:
    return b.hex()


# =============================================================================
# Signatures
# =============================================================================

@dataclass
class Signature:
    alg: str
    key_id: str
    signature: str  # hex

    def to_dict(self) -> Dict[str, str]:
        return {"alg": self.alg, "key_id": self.key_id, "signature": self.signature}


def _sign_payload_ed25519(secret_key_hex: str, payload: Dict[str, Any], key_id: str) -> Signature:
    _require(signing is not None, "PyNaCl is not installed; cannot use Ed25519 signing.")
    sk_bytes = _hex_to_bytes(secret_key_hex)
    try:
        sk = signing.SigningKey(sk_bytes)
    except Exception as e:  # pragma: no cover
        raise SystemExit(f"Invalid Ed25519 secret key: {e}")
    signed = sk.sign(_canonical_json_bytes(payload), encoder=RawEncoder)
    sig = bytes(signed.signature)  # type: ignore
    return Signature(alg="ed25519", key_id=key_id, signature=_bytes_to_hex(sig))


def _verify_payload_ed25519(public_key_hex: str, payload: Dict[str, Any], signature_hex: str) -> bool:
    _require(signing is not None, "PyNaCl is not installed; cannot use Ed25519 verification.")
    pk = signing.VerifyKey(_hex_to_bytes(public_key_hex))
    try:
        pk.verify(_canonical_json_bytes(payload), _hex_to_bytes(signature_hex), encoder=RawEncoder)
        return True
    except Exception:
        return False


def _sign_payload_hmac(secret_hex: str, payload: Dict[str, Any], key_id: str) -> Signature:
    import hmac
    key = _hex_to_bytes(secret_hex)
    mac = hmac.new(key, _canonical_json_bytes(payload), digestmod=sha3_256).digest()
    return Signature(alg="hmac-sha3-256", key_id=key_id, signature=_bytes_to_hex(mac))


def _verify_payload_hmac(secret_hex: str, payload: Dict[str, Any], signature_hex: str) -> bool:
    import hmac
    key = _hex_to_bytes(secret_hex)
    expected = hmac.new(key, _canonical_json_bytes(payload), digestmod=sha3_256).digest().hex()
    # constant time compare
    return hmac.compare_digest(expected, signature_hex)


def _sign_entry(circuit_id: str, entry: Dict[str, Any], *, ed25519_sk: Optional[str], hmac_key: Optional[str], key_id: str) -> Signature:
    payload = {
        "circuit_id": circuit_id,
        "kind": entry.get("kind"),
        "vk_format": entry.get("vk_format"),
        "vk_hash": entry.get("vk_hash"),
    }
    if ed25519_sk:
        return _sign_payload_ed25519(ed25519_sk, payload, key_id)
    elif hmac_key:
        return _sign_payload_hmac(hmac_key, payload, key_id)
    else:
        raise SystemExit("Provide either --ed25519-secret-key or --hmac-key to sign.")


def _verify_entry_signature(circuit_id: str, entry: Dict[str, Any], *, ed25519_pk: Optional[str], hmac_key: Optional[str]) -> bool:
    sig = entry.get("sig")
    if not sig:
        print("No 'sig' present on entry.", file=sys.stderr)
        return False
    alg = sig.get("alg")
    signature_hex = sig.get("signature")
    if not isinstance(signature_hex, str):
        print("Malformed signature object.", file=sys.stderr)
        return False
    payload = {
        "circuit_id": circuit_id,
        "kind": entry.get("kind"),
        "vk_format": entry.get("vk_format"),
        "vk_hash": entry.get("vk_hash"),
    }
    if alg == "ed25519":
        _require(ed25519_pk is not None, "Provide --ed25519-public-key to verify an ed25519 signature.")
        return _verify_payload_ed25519(ed25519_pk, payload, signature_hex)
    elif alg == "hmac-sha3-256":
        _require(hmac_key is not None, "Provide --hmac-key to verify an HMAC signature.")
        return _verify_payload_hmac(hmac_key, payload, signature_hex)
    else:
        print(f"Unsupported signature alg: {alg}", file=sys.stderr)
        return False


# =============================================================================
# Registry.yaml sync (optional)
# =============================================================================

def _sync_registry_yaml(circuit_id: str, new_vk_hash: str, path: Path = REGISTRY_YAML_PATH) -> bool:
    if yaml is None:
        print("PyYAML not installed; skipping registry.yaml update.", file=sys.stderr)
        return False
    if not path.exists():
        print(f"{path} not found; skipping.", file=sys.stderr)
        return False
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    circuits = (data or {}).get("circuits") or {}
    if circuit_id not in circuits:
        print(f"circuits.{circuit_id} not found in registry.yaml; skipping.", file=sys.stderr)
        return False
    circuits[circuit_id]["vk_hash"] = new_vk_hash
    # Atomic write
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmpf:
            yaml.safe_dump(data, tmpf, sort_keys=True)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise
    return True


# =============================================================================
# Commands
# =============================================================================

def cmd_list(args: argparse.Namespace) -> None:
    cache = _load_vk_cache()
    for cid in sorted(cache["entries"].keys()):
        e = cache["entries"][cid]
        print(f"{cid}: kind={e.get('kind')} format={e.get('vk_format')} vk_hash={e.get('vk_hash')}")


def cmd_show(args: argparse.Namespace) -> None:
    cache = _load_vk_cache()
    e = cache["entries"].get(args.circuit_id)
    _require(e is not None, f"Entry not found: {args.circuit_id}")
    print(json.dumps(e, indent=2, sort_keys=True))


def cmd_add(args: argparse.Namespace) -> None:
    cache = _load_vk_cache()
    entries = cache["entries"]

    if args.circuit_id in entries and not args.overwrite:
        raise SystemExit(f"Entry already exists: {args.circuit_id} (use --overwrite to replace)")

    entry: Dict[str, Any] = {
        "kind": args.kind,
        "vk_format": args.vk_format,
        "vk": None,
        "fri_params": None,
        "meta": {},
    }

    if args.vk_file:
        vk_path = Path(args.vk_file)
        _require(vk_path.exists(), f"VK file not found: {vk_path}")
        entry["vk"] = _load_json(vk_path)

    if args.fri_params_file:
        fp_path = Path(args.fri_params_file)
        _require(fp_path.exists(), f"FRI params file not found: {fp_path}")
        entry["fri_params"] = _load_json(fp_path)

    if args.meta_file:
        meta_path = Path(args.meta_file)
        _require(meta_path.exists(), f"Meta file not found: {meta_path}")
        entry["meta"] = _load_json(meta_path)

    # Compute and set vk_hash
    entry["vk_hash"] = _compute_vk_hash(entry)

    # Optional signature
    if args.ed25519_secret_key or args.hmac_key:
        sig = _sign_entry(
            args.circuit_id,
            entry,
            ed25519_sk=args.ed25519_secret_key,
            hmac_key=args.hmac_key,
            key_id=args.key_id or "default",
        )
        entry["sig"] = sig.to_dict()

    # Store
    entries[args.circuit_id] = entry
    _write_vk_cache(cache)
    print(f"Stored {args.circuit_id} with vk_hash={entry['vk_hash']}")

    # Optional sync to registry.yaml
    if args.sync_registry:
        if _sync_registry_yaml(args.circuit_id, entry["vk_hash"]):
            print("registry.yaml updated.")


def cmd_sign(args: argparse.Namespace) -> None:
    cache = _load_vk_cache()
    e = cache["entries"].get(args.circuit_id)
    _require(e is not None, f"Entry not found: {args.circuit_id}")

    # Ensure vk_hash current
    current = e.get("vk_hash")
    recomputed = _compute_vk_hash(e)
    if current != recomputed:
        print(f"vk_hash mismatch (cache={current} recomputed={recomputed}); updating to recomputed.", file=sys.stderr)
        e["vk_hash"] = recomputed

    sig = _sign_entry(
        args.circuit_id,
        e,
        ed25519_sk=args.ed25519_secret_key,
        hmac_key=args.hmac_key,
        key_id=args.key_id or "default",
    )
    e["sig"] = sig.to_dict()
    _write_vk_cache(cache)
    print(f"Signed {args.circuit_id} with {sig.alg} (key_id={sig.key_id}).")


def cmd_verify(args: argparse.Namespace) -> None:
    cache = _load_vk_cache()
    entries = cache["entries"]

    def verify_one(cid: str) -> bool:
        e = entries.get(cid)
        if e is None:
            print(f"[{cid}] not found.", file=sys.stderr)
            return False
        # Hash check
        expected = e.get("vk_hash")
        recomputed = _compute_vk_hash(e)
        ok_hash = (expected == recomputed)
        print(f"[{cid}] hash: {'OK' if ok_hash else 'FAIL'}")
        # Signature (optional)
        ok_sig = True
        if args.ed25519_public_key or args.hmac_key:
            ok_sig = _verify_entry_signature(cid, e, ed25519_pk=args.ed25519_public_key, hmac_key=args.hmac_key)
            print(f"[{cid}] signature: {'OK' if ok_sig else 'FAIL'}")
        return ok_hash and ok_sig

    if args.circuit_id:
        ok = verify_one(args.circuit_id)
        if not ok:
            raise SystemExit(2)
    else:
        all_ok = True
        for cid in sorted(entries.keys()):
            all_ok &= verify_one(cid)
        if not all_ok:
            raise SystemExit(2)


def cmd_sync_registry(args: argparse.Namespace) -> None:
    cache = _load_vk_cache()
    e = cache["entries"].get(args.circuit_id)
    _require(e is not None, f"Entry not found: {args.circuit_id}")
    if _sync_registry_yaml(args.circuit_id, e["vk_hash"]):
        print("registry.yaml updated.")
    else:
        raise SystemExit(2)


# =============================================================================
# Argparse
# =============================================================================

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Animica VK registry manager")
    sub = p.add_subparsers(dest="cmd", required=True)

    # list
    sp = sub.add_parser("list", help="List all circuit IDs in vk_cache.json")
    sp.set_defaults(func=cmd_list)

    # show
    sp = sub.add_parser("show", help="Show a specific entry as JSON")
    sp.add_argument("--circuit-id", required=True)
    sp.set_defaults(func=cmd_show)

    # add / replace
    sp = sub.add_parser("add", help="Add or replace a VK entry")
    sp.add_argument("--circuit-id", required=True, help="e.g. counter_groth16_bn254@2")
    sp.add_argument("--kind", required=True, help="e.g. groth16_bn254, plonk_kzg_bn254, stark_fri_merkle")
    sp.add_argument("--vk-format", required=True, help="e.g. snarkjs, plonkjs, fri_params")
    sp.add_argument("--vk-file", help="Path to VK JSON (schemes with structured VKs)")
    sp.add_argument("--fri-params-file", help="Path to FRI params JSON (for STARK toy verifier)")
    sp.add_argument("--meta-file", help="Path to optional metadata JSON")
    sp.add_argument("--overwrite", action="store_true", help="Replace if exists")
    # signing
    sp.add_argument("--ed25519-secret-key", help="Hex-encoded Ed25519 secret key (requires PyNaCl)")
    sp.add_argument("--hmac-key", help="Hex-encoded secret for HMAC-SHA3-256")
    sp.add_argument("--key-id", help="Signer key id label (stored in entry.sig)")
    sp.add_argument("--sync-registry", action="store_true", help="Also update registry.yaml circuits.<id>.vk_hash")
    sp.set_defaults(func=cmd_add)

    # sign
    sp = sub.add_parser("sign", help="Sign an existing entry (stores entry.sig)")
    sp.add_argument("--circuit-id", required=True)
    sp.add_argument("--ed25519-secret-key", help="Hex-encoded Ed25519 secret key (requires PyNaCl)")
    sp.add_argument("--hmac-key", help="Hex-encoded secret for HMAC-SHA3-256")
    sp.add_argument("--key-id", help="Signer key id label")
    sp.set_defaults(func=cmd_sign)

    # verify
    sp = sub.add_parser("verify", help="Verify hash (and signature if key provided)")
    sp.add_argument("--circuit-id", help="If omitted, verifies all entries")
    sp.add_argument("--ed25519-public-key", help="Hex-encoded Ed25519 public key")
    sp.add_argument("--hmac-key", help="Hex-encoded secret for HMAC-SHA3-256")
    sp.set_defaults(func=cmd_verify)

    # sync-registry
    sp = sub.add_parser("sync-registry", help="Update registry.yaml circuits.<id>.vk_hash from cache")
    sp.add_argument("--circuit-id", required=True)
    sp.set_defaults(func=cmd_sync_registry)

    return p


def main(argv: Optional[list[str]] = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
