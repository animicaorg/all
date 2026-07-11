#!/usr/bin/env python3
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
for candidate in (REPO_ROOT, REPO_ROOT / "python"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


def _sig_roundtrip(mod, alg_label: str) -> tuple[bytes, bytes, bytes]:
    """keypair -> sign -> verify round-trip, plus negative checks.

    The negative checks guard against an accept-all verify(): a tampered message
    and a tampered signature MUST both be rejected.
    """
    sk, pk = mod.keypair()
    msg = f"animica-pq-selftest:{alg_label}".encode("utf-8")
    sig = mod.sign(sk, msg)
    if not bool(mod.verify(pk, msg, sig)):
        raise RuntimeError(f"{alg_label}: sign/verify round-trip failed")
    if bool(mod.verify(pk, msg + b"\x00", sig)):
        raise RuntimeError(f"{alg_label}: verify() accepted a tampered message")
    if sig:
        bad_sig = bytes(sig[:-1]) + bytes([sig[-1] ^ 0x01])
        if bool(mod.verify(pk, msg, bad_sig)):
            raise RuntimeError(f"{alg_label}: verify() accepted a tampered signature")
    return sk, pk, sig


def _require(alg_module_name: str, alg_label: str) -> dict[str, object]:
    """Hard build gate: this scheme MUST be present and cryptographically sound."""
    mod = importlib.import_module(alg_module_name)
    if not hasattr(mod, "is_available") or not mod.is_available():
        raise RuntimeError(f"{alg_label}: REQUIRED backend unavailable")
    sk, pk, sig = _sig_roundtrip(mod, alg_label)
    return {
        "module": getattr(mod, "__file__", None),
        "name": alg_label,
        "status": "ok",
        "pk_len": len(pk),
        "sk_len": len(sk),
        "sig_len": len(sig),
    }


def _assert_gated_off(alg_module_name: str, alg_label: str) -> dict[str, object]:
    """A deprecated/forgeable stub scheme MUST NOT be usable on mainnet.

    SPHINCS+ (scheme id 2) is a forgeable stub, disabled by default in
    coretx.schemes.CANONICAL_SCHEME_SPECS (ANM-C01/L06). Its only backend is the
    insecure pure-Python fallback, gated behind ANIMICA_ALLOW_PQ_PURE_FALLBACK=1.
    On a mainnet node (flag unset) keypair() MUST raise NotImplementedError.

    We assert that security property here instead of requiring the forgeable scheme
    to mint keys — the old self-test demanded a working SPHINCS+ keypair, which on
    mainnet could only be satisfied by enabling the *insecure* fallback on a
    production node. That is exactly what must never happen, so we verify the gate
    holds rather than punch through it. Fail-closed: only a genuine
    NotImplementedError passes; a succeeding keypair (or any other outcome) aborts.
    """
    mod = importlib.import_module(alg_module_name)
    if os.environ.get("ANIMICA_ALLOW_PQ_PURE_FALLBACK") == "1":
        # Dev/test image explicitly opted into insecure fallbacks — not a mainnet build.
        return {"name": alg_label, "status": "insecure-fallback-enabled (dev only)"}
    try:
        mod.keypair()
    except NotImplementedError:
        return {"name": alg_label, "status": "gated-off (disabled on mainnet)"}
    raise RuntimeError(
        f"{alg_label}: expected the forgeable stub to be gated off on mainnet, "
        f"but keypair() succeeded — refusing to build a node that can mint this scheme."
    )


def main() -> int:
    # ml_dsa_65 (scheme id 11) is the ONLY signature scheme enabled on mainnet
    # (coretx.schemes.CANONICAL_SCHEME_SPECS). It MUST be present and cryptographically
    # sound (keypair/sign/verify + tamper-rejection) or the node is useless — the only
    # hard build gate.
    # sphincs_shake_128s (id 2) is a forgeable stub, disabled on mainnet — assert it
    # stays gated off rather than requiring the insecure pure-Python fallback.
    # dilithium3 (id 1) is deliberately NOT tested: it is a disabled, forgeable legacy
    # scheme (coretx.schemes ANM-C01 — its verify() accepts tampered signatures), so
    # exercising it here would either fail tamper-rejection or falsely bless forgeable
    # crypto. Its rejection is enforced at consensus verify time, not by this build gate.
    results = {
        "ml_dsa_65": _require("pq.py.algs.ml_dsa_65", "ml_dsa_65"),
        "sphincs_shake_128s": _assert_gated_off(
            "pq.py.algs.sphincs_shake_128s", "sphincs_shake_128s"
        ),
    }
    print("pq-selftest-ok", json.dumps(results, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
