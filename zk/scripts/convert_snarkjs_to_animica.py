#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Convert snarkjs artifacts (vk.json, proof.json, public.json) into an Animica proof envelope.

The envelope format (JSON):

{
  "type": "zk_proof",
  "system": "groth16" | "plonk_kzg",
  "curve": "bn128" | "bls12_381" | "...",     # populated from VK when available
  "source": {"tool": "snarkjs", "version": "<unknown|from vk>", "paths": {...}},
  "meta": {
    "created_at": "YYYY-MM-DDTHH:MM:SSZ",
    "notes": ""
  },
  "public": ["<decimal strings, as provided by snarkjs>"],
  "public_hex": ["0x..."],                     # convenience hex form (same values)
  "proof": { ... snarkjs proof object ... },   # optional when proof not provided
  "vk": { ... snarkjs vk object ... },         # optional depending on --vk-embed
  "hashes": {
    "algo": "sha256" | "sha3_256",
    "public": "0x...",                         # canonical(json(public))
    "proof": "0x...",                          # canonical(json(proof))      (if present)
    "vk": "0x...",                             # canonical(json(vk))         (if present)
    "envelope": "0x..."                        # canonical(json(envelope-*)) (computed without this field first)
  }
}

By default, we embed the full VK and proof. You can change VK embedding with --vk-embed:
  - "full"  : include full VK JSON (default)
  - "hash"  : omit `vk`, keep only computed hash
  - "none"  : omit `vk` and vk hash (not recommended)

Canonical hashing uses sorted keys & compact separators to ensure stable digests across platforms.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import hashlib
except Exception as e:  # pragma: no cover
    raise SystemExit(f"hashlib is required: {e}")

# ------------- helpers -------------


def _canonical_dumps(obj: Any) -> str:
    """Deterministic, compact, sorted-key JSON with trailing newline."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n"


def _hash_bytes(data: bytes, algo: str) -> str:
    algo = algo.lower()
    if algo == "sha256":
        h = hashlib.sha256()
    elif algo == "sha3_256":
        h = hashlib.sha3_256()
    else:
        raise SystemExit(f"Unsupported hash algo: {algo}. Use sha256 or sha3_256.")
    h.update(data)
    return "0x" + h.hexdigest()


def _hash_json(obj: Any, algo: str) -> str:
    return _hash_bytes(_canonical_dumps(obj).encode("utf-8"), algo)


def _is_dir_with_artifacts(p: Path) -> bool:
    return (p / "vk.json").exists() or (p / "proof.json").exists() or (p / "public.json").exists()


def _load_json(path: Optional[Path]) -> Optional[Any]:
    if path is None:
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _to_hex_str(dec_str: str) -> str:
    # snarkjs emits decimals encoded as strings; normalize to 0x hex (no leading zeros).
    try:
        n = int(dec_str, 10)
    except ValueError as e:
        raise SystemExit(f"Public signal is not a decimal string: {dec_str!r}") from e
    return hex(n)


def _detect_system_from_vk(vk: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    # snarkjs vk often has: {"protocol":"groth16","curve":"bn128", ...}
    system = vk.get("protocol")
    curve = vk.get("curve")
    if system is None:
        # Some plonk VKs encode in nested vk; fallbacks:
        if "vk" in vk:
            inner = vk["vk"]
            system = inner.get("protocol") or "plonk_kzg"
            curve = inner.get("curve") or vk.get("curve")
    if system == "plonk":
        system = "plonk_kzg"
    return (system or "groth16", curve)


def _decide_output_path(out: Optional[Path], base_dir: Path) -> Path:
    if out:
        return out
    return base_dir / "envelope.json"


# ------------- conversion -------------


def build_envelope(
    *,
    vk: Optional[Dict[str, Any]],
    proof: Optional[Dict[str, Any]],
    public: Optional[List[str]],
    hash_algo: str,
    vk_embed: str,
    explicit_system: Optional[str] = None,
    explicit_curve: Optional[str] = None,
    src_paths: Dict[str, Optional[str]],
) -> Dict[str, Any]:
    # Public signals
    public = public or []
    if not isinstance(public, list):
        raise SystemExit("Expected 'public.json' to be a JSON array")
    public_hex = [_to_hex_str(s) for s in public]

    # System / curve detection
    if explicit_system:
        system = explicit_system
        curve = explicit_curve
    else:
        if vk:
            system, curve = _detect_system_from_vk(vk)
        else:
            system = "groth16"  # best-guess default
            curve = explicit_curve
    if explicit_curve:
        curve = explicit_curve

    # Baseline envelope (without hashes)
    env: Dict[str, Any] = {
        "type": "zk_proof",
        "system": system,
        "curve": curve or "",
        "source": {
            "tool": "snarkjs",
            "version": vk.get("snarkjsVersion") if isinstance(vk, dict) else None,
            "paths": src_paths,
        },
        "meta": {
            "created_at": _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "notes": "",
        },
        "public": public,
        "public_hex": public_hex,
    }

    # VK embedding strategy
    if vk_embed == "full":
        if vk is not None:
            env["vk"] = vk
    elif vk_embed == "hash":
        pass  # include only vk hash in `hashes` block
    elif vk_embed == "none":
        pass
    else:
        raise SystemExit(f"Unknown --vk-embed mode: {vk_embed}")

    if proof is not None:
        env["proof"] = proof

    # Compute hashes (without the 'hashes' field first)
    hashes: Dict[str, Any] = {"algo": hash_algo}
    hashes["public"] = _hash_json(public, hash_algo)
    if proof is not None:
        hashes["proof"] = _hash_json(proof, hash_algo)
    if vk is not None:
        hashes["vk"] = _hash_json(vk, hash_algo)

    # Envelope hash is computed on a shallow copy that does NOT include the 'hashes' field yet.
    env_for_digest = dict(env)  # shallow copy
    # Guarantee stable field order by dumping canonically:
    env_digest = _hash_bytes(_canonical_dumps(env_for_digest).encode("utf-8"), hash_algo)
    hashes["envelope"] = env_digest

    env["hashes"] = hashes
    return env


# ------------- CLI -------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Convert snarkjs proof/VK/public into an Animica proof envelope.")
    p.add_argument(
        "path",
        nargs="?",
        help="Directory containing vk.json/proof.json/public.json OR a file (vk/proof/public). If omitted, use --vk/--proof/--public.",
    )
    p.add_argument("--vk", type=str, help="Path to snarkjs vk.json")
    p.add_argument("--proof", type=str, help="Path to snarkjs proof.json")
    p.add_argument("--public", type=str, help="Path to snarkjs public.json")
    p.add_argument("--system", type=str, choices=["groth16", "plonk_kzg"], help="Override system (otherwise inferred)")
    p.add_argument("--curve", type=str, help="Override curve name (otherwise inferred from VK)")
    p.add_argument(
        "--vk-embed",
        type=str,
        choices=["full", "hash", "none"],
        default="full",
        help="How to include the VK in the envelope (default: full)",
    )
    p.add_argument("--hash", dest="hash_algo", type=str, default="sha256", choices=["sha256", "sha3_256"], help="Hash algorithm")
    p.add_argument("-o", "--out", type=str, help="Output JSON path (default: <dir>/envelope.json)")
    p.add_argument("--stdout", action="store_true", help="Write envelope to stdout instead of a file")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    base_dir: Optional[Path] = None
    vk_path: Optional[Path] = Path(args.vk) if args.vk else None
    proof_path: Optional[Path] = Path(args.proof) if args.proof else None
    public_path: Optional[Path] = Path(args.public) if args.public else None

    if args.path:
        p = Path(args.path)
        if p.is_dir() and _is_dir_with_artifacts(p):
            base_dir = p
            vk_path = vk_path or (p / "vk.json")
            proof_path = proof_path or (p / "proof.json")
            public_path = public_path or (p / "public.json")
        elif p.is_file():
            # If a file was passed, assign to respective slot if the extension matches
            # or guess based on filename.
            if p.name == "vk.json" or "vk" in p.stem:
                vk_path = vk_path or p
            elif p.name == "proof.json" or "proof" in p.stem:
                proof_path = proof_path or p
            elif p.name == "public.json" or "public" in p.stem:
                public_path = public_path or p
            else:
                raise SystemExit(f"Unrecognized file passed as PATH: {p}")
            base_dir = p.parent
        else:
            raise SystemExit(f"PATH not found or not a circuit directory/file: {p}")

    if base_dir is None:
        # Fall back to cwd when explicit files are provided.
        base_dir = Path.cwd()

    # Load artifacts if they exist
    vk = _load_json(vk_path) if (vk_path and vk_path.exists()) else None
    proof = _load_json(proof_path) if (proof_path and proof_path.exists()) else None
    public = _load_json(public_path) if (public_path and public_path.exists()) else []

    # Envelope construction
    src_paths = {
        "vk": str(vk_path) if vk_path else None,
        "proof": str(proof_path) if proof_path else None,
        "public": str(public_path) if public_path else None,
    }
    env = build_envelope(
        vk=vk,
        proof=proof,
        public=public,
        hash_algo=args.hash_algo,
        vk_embed=args.vk_embed,
        explicit_system=args.system,
        explicit_curve=args.curve,
        src_paths=src_paths,
    )

    # Decide output
    out_path = _decide_output_path(Path(args.out) if args.out else None, base_dir)

    payload = _canonical_dumps(env)
    if args.stdout:
        print(payload, end="")
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8")
        print(f"✓ Wrote envelope: {out_path} ({len(payload)} bytes)")

if __name__ == "__main__":
    main()
