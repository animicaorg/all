#!/usr/bin/env python3
from __future__ import annotations

"""
omni pq handshake demo — two-party Kyber768 KEM handshake with HKDF key schedule,
transcript hash, and (optional) PQ identity signatures for mutual auth.

This runs a complete initiator↔responder flow **in-process** and prints a human or JSON
summary that mirrors the Animica P2P handshake design (Kyber768 + HKDF-SHA3-256).

Usage:
  # Default: Dilithium3 identities, human-readable summary
  python -m pq.cli.pq_handshake_demo

  # JSON output (good for test scripts)
  python -m pq.cli.pq_handshake_demo --json

  # Use SPHINCS+ identities instead of Dilithium3
  python -m pq.cli.pq_handshake_demo --sig-alg sphincs-shake-128s

  # Skip identity signatures and just derive AEAD keys (unauthenticated demo)
  python -m pq.cli.pq_handshake_demo --no-sign

  # Save artifacts (pubkeys, ciphertext, transcript hash) to a directory
  python -m pq.cli.pq_handshake_demo --out ./out

What you’ll see:
  • Static Kyber keypairs for Initiator (I) and Responder (R)
  • Encapsulation (I → R): ciphertext ct and shared secret ss
  • Decapsulation (R): shared secret ss' (checked to be equal to Initiator’s)
  • Transcript hash TH = H("animica|p2p|handshake|" || pkI || pkR || ct)
  • HKDF-SHA3-256 key schedule (salted by transcript) to derive AEAD keys:
        I.tx_key == R.rx_key  and  I.rx_key == R.tx_key
  • Optional PQ identity signatures (each side signs TH; the other verifies)

All hashing is SHA3-256 unless otherwise noted.

Security notes:
  • This demo is non-interactive and keeps all secrets in-memory.
  • It’s intended for developer intuition and test harnesses, *not* production ops.
"""

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

# --- Local PQ modules ---------------------------------------------------------

try:
    # Hash/HKDF utilities
    # Algorithm registry (for friendly names/ids)
    from pq.py import registry as pq_registry
    # KEM (Kyber-768) wrappers
    from pq.py.algs.kyber768 import decapsulate as kyber_decapsulate
    from pq.py.algs.kyber768 import encapsulate as kyber_encapsulate
    from pq.py.algs.kyber768 import keygen as kyber_keygen
    # Identity keygen/sign/verify (Dilithium3 or SPHINCS+)
    from pq.py.keygen import generate_keypair
    from pq.py.sign import sign_detached
    from pq.py.utils.hash import sha3_256
    from pq.py.utils.hkdf import hkdf_sha3_256
    from pq.py.verify import verify_detached
except Exception as e:  # pragma: no cover
    raise SystemExit(
        f"FATAL: missing pq package modules: {e}\n"
        "Hint: run from the repo root or add it to PYTHONPATH."
    )


# --- Helpers -----------------------------------------------------------------


def _hx(b: bytes) -> str:
    return b.hex()


def _tag(label: str) -> bytes:
    return sha3_256(("animica|p2p|" + label).encode("utf-8"))


def _transcript_hash(pk_i: bytes, pk_r: bytes, ct: bytes) -> bytes:
    return sha3_256(b"animica|p2p|handshake|" + pk_i + pk_r + ct)


def _derive_keys(ss: bytes, pk_i: bytes, pk_r: bytes, ct: bytes) -> Dict[str, bytes]:
    """
    HKDF-SHA3-256 with transcript-derived salt.
      salt = H("animica|p2p|salt|" || pkI || pkR || ct)
      I.tx_key = HKDF(ss, salt, info="aead:i->r", 32)
      I.rx_key = HKDF(ss, salt, info="aead:r->i", 32)
      R.tx_key = HKDF(ss, salt, info="aead:r->i", 32)  # mirror directions
      R.rx_key = HKDF(ss, salt, info="aead:i->r", 32)
    """
    salt = sha3_256(b"animica|p2p|salt|" + pk_i + pk_r + ct)
    i_tx = hkdf_sha3_256(ikm=ss, salt=salt, info=b"aead:i->r", length=32)
    i_rx = hkdf_sha3_256(ikm=ss, salt=salt, info=b"aead:r->i", length=32)
    # directions invert for responder
    r_tx, r_rx = i_rx, i_tx
    return {"i_tx": i_tx, "i_rx": i_rx, "r_tx": r_tx, "r_rx": r_rx}


# --- Data structures ----------------------------------------------------------


@dataclass
class PartyKeys:
    kyber_pk: bytes
    kyber_sk: bytes
    id_alg: Optional[str] = None
    id_pk: Optional[bytes] = None
    id_sk: Optional[bytes] = None


@dataclass
class HandshakeArtifacts:
    initiator: PartyKeys
    responder: PartyKeys
    ct: bytes
    ss_initiator: bytes
    ss_responder: bytes
    transcript_hash: bytes
    keys: Dict[str, bytes]  # i_tx, i_rx, r_tx, r_rx
    sign_initiator: Optional[bytes] = None
    sign_responder: Optional[bytes] = None
    verify_initiator_ok: Optional[bool] = None
    verify_responder_ok: Optional[bool] = None

    def to_jsonable(self) -> Dict[str, Any]:
        def bx(x: Optional[bytes]) -> Optional[str]:
            return x.hex() if isinstance(x, (bytes, bytearray)) else None

        def party_to_jsonable(p: PartyKeys) -> Dict[str, Any]:
            return {
                "kyber_pk": bx(p.kyber_pk),
                "kyber_sk": bx(p.kyber_sk),
                "id_alg": p.id_alg,
                "id_pk": bx(p.id_pk),
                "id_sk": bx(p.id_sk),
            }

        return {
            "initiator": party_to_jsonable(self.initiator),
            "responder": party_to_jsonable(self.responder),
            "ct": bx(self.ct),
            "ss_initiator": bx(self.ss_initiator),
            "ss_responder": bx(self.ss_responder),
            "transcript_hash": bx(self.transcript_hash),
            "keys": {k: v.hex() for k, v in self.keys.items()},
            "sign_initiator": bx(self.sign_initiator),
            "sign_responder": bx(self.sign_responder),
            "verify_initiator_ok": self.verify_initiator_ok,
            "verify_responder_ok": self.verify_responder_ok,
            "checks": {
                "ss_equal": self.ss_initiator == self.ss_responder,
                "key_dir_match": (
                    self.keys["i_tx"] == self.keys["r_rx"]
                    and self.keys["i_rx"] == self.keys["r_tx"]
                ),
            },
        }


# --- Main logic ---------------------------------------------------------------


def run_demo(
    sig_alg: Optional[str] = "dilithium3", do_sign: bool = True
) -> HandshakeArtifacts:
    """
    Build two Kyber keypairs, perform Kyber encaps/decaps, derive transcript & AEAD keys,
    and (optionally) generate/verify identity signatures over the transcript hash.
    """
    # Static Kyber-768 identities
    sk_i, pk_i = kyber_keygen()
    sk_r, pk_r = kyber_keygen()

    # Encapsulation (Initiator → Responder)
    ct, ss_i = kyber_encapsulate(pk_r)

    # Decapsulation (Responder)
    ss_r = kyber_decapsulate(sk_r, ct)
    if ss_i != ss_r:
        # This should not happen; if it does, abort with context
        raise RuntimeError("Shared secret mismatch (encaps/decaps failed)")

    # Transcript & keys
    th = _transcript_hash(pk_i, pk_r, ct)
    keys = _derive_keys(ss_i, pk_i, pk_r, ct)

    # Identity signatures (optional)
    sign_i = sign_r = None
    v_ok_i = v_ok_r = None
    initiator = PartyKeys(kyber_pk=pk_i, kyber_sk=sk_i)
    responder = PartyKeys(kyber_pk=pk_r, kyber_sk=sk_r)

    if do_sign:
        if sig_alg not in ("dilithium3", "sphincs-shake-128s"):
            raise ValueError("sig_alg must be 'dilithium3' or 'sphincs-shake-128s'")

        initiator.id_alg = sig_alg
        responder.id_alg = sig_alg

        sk_id_i, pk_id_i = generate_keypair(sig_alg)
        sk_id_r, pk_id_r = generate_keypair(sig_alg)

        initiator.id_sk, initiator.id_pk = sk_id_i, pk_id_i
        responder.id_sk, responder.id_pk = sk_id_r, pk_id_r

        domain = _tag("handshake-auth")
        sign_i = sign_detached(sig_alg, sk_id_i, th, domain=domain)
        sign_r = sign_detached(sig_alg, sk_id_r, th, domain=domain)

        v_ok_i = verify_detached(sig_alg, pk_id_i, th, sign_i, domain=domain)
        v_ok_r = verify_detached(sig_alg, pk_id_r, th, sign_r, domain=domain)

    return HandshakeArtifacts(
        initiator=initiator,
        responder=responder,
        ct=ct,
        ss_initiator=ss_i,
        ss_responder=ss_r,
        transcript_hash=th,
        keys=keys,
        sign_initiator=sign_i,
        sign_responder=sign_r,
        verify_initiator_ok=v_ok_i,
        verify_responder_ok=v_ok_r,
    )


def _print_human(art: HandshakeArtifacts) -> None:
    print("Animica PQ Handshake Demo (Kyber768 + HKDF-SHA3-256)\n")
    print("Identities (Kyber KEM):")
    print(f"  I.pk = { _hx(art.initiator.kyber_pk) }")
    print(f"  R.pk = { _hx(art.responder.kyber_pk) }")
    print()
    print("Encapsulation (Initiator → Responder):")
    print(f"  ct   = { _hx(art.ct) }")
    print(f"  ss_i = { _hx(art.ss_initiator) }")
    print(f"  ss_r = { _hx(art.ss_responder) }")
    print(f"  ✓ secrets equal? {art.ss_initiator == art.ss_responder}")
    print()
    print("Transcript & Keys:")
    print(f"  TH         = { _hx(art.transcript_hash) }")
    print(f"  I.tx_key   = { _hx(art.keys['i_tx']) }")
    print(f"  I.rx_key   = { _hx(art.keys['i_rx']) }")
    print(f"  R.tx_key   = { _hx(art.keys['r_tx']) }")
    print(f"  R.rx_key   = { _hx(art.keys['r_rx']) }")
    print(
        f"  ✓ dir match? I.tx==R.rx and I.rx==R.tx → "
        f"{art.keys['i_tx']==art.keys['r_rx'] and art.keys['i_rx']==art.keys['r_tx']}"
    )
    print()
    if art.sign_initiator is not None:
        print(f"Identity signatures ({art.initiator.id_alg}):")
        print(
            f"  sig(I) = { _hx(art.sign_initiator) }  → verify={art.verify_initiator_ok}"
        )
        print(
            f"  sig(R) = { _hx(art.sign_responder) }  → verify={art.verify_responder_ok}"
        )
        print()


def _write_out(out_dir: Path, art: HandshakeArtifacts) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "handshake.json").write_text(
        json.dumps(art.to_jsonable(), indent=2), encoding="utf-8"
    )
    (out_dir / "ct.hex").write_text(_hx(art.ct) + "\n", encoding="utf-8")
    (out_dir / "transcript_hash.hex").write_text(
        _hx(art.transcript_hash) + "\n", encoding="utf-8"
    )
    (out_dir / "keys.txt").write_text(
        "\n".join(
            [
                f"I.tx_key={_hx(art.keys['i_tx'])}",
                f"I.rx_key={_hx(art.keys['i_rx'])}",
                f"R.tx_key={_hx(art.keys['r_tx'])}",
                f"R.rx_key={_hx(art.keys['r_rx'])}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Two-party Kyber768 handshake & transcript demo."
    )
    ap.add_argument(
        "--sig-alg",
        default="dilithium3",
        help="PQ identity signature algorithm: dilithium3 | sphincs-shake-128s (default: dilithium3)",
    )
    ap.add_argument(
        "--no-sign",
        action="store_true",
        help="Skip identity signatures (unauthenticated demo).",
    )
    ap.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable text."
    )
    ap.add_argument("--out", type=Path, help="Optional output directory for artifacts.")
    args = ap.parse_args(argv)

    do_sign = not args.no_sign
    art = run_demo(sig_alg=args.sig_alg, do_sign=do_sign)

    if args.json:
        print(json.dumps(art.to_jsonable(), indent=2))
    else:
        _print_human(art)

    if args.out:
        _write_out(args.out, art)

    # sanity: return nonzero if checks fail
    ok = (
        (art.ss_initiator == art.ss_responder)
        and (art.keys["i_tx"] == art.keys["r_rx"])
        and (art.keys["i_rx"] == art.keys["r_tx"])
    )
    if do_sign:
        ok = ok and bool(art.verify_initiator_ok) and bool(art.verify_responder_ok)

    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
