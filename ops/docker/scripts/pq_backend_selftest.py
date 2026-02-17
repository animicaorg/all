#!/usr/bin/env python3
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
for candidate in (REPO_ROOT, REPO_ROOT / "python"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


def _self_test(alg_module_name: str, alg_label: str) -> dict[str, object]:
    mod = importlib.import_module(alg_module_name)
    if not hasattr(mod, "is_available") or not mod.is_available():
        raise RuntimeError(f"{alg_label}: backend unavailable")
    sk, pk = mod.keypair()
    msg = f"animica-pq-selftest:{alg_label}".encode("utf-8")
    sig = mod.sign(sk, msg)
    ok = bool(mod.verify(pk, msg, sig))
    if not ok:
        raise RuntimeError(f"{alg_label}: sign/verify failed")
    return {
        "module": getattr(mod, "__file__", None),
        "name": alg_label,
        "verify": ok,
        "pk_len": len(pk),
        "sk_len": len(sk),
        "sig_len": len(sig),
    }


def main() -> int:
    results = {
        "dilithium3": _self_test("pq.py.algs.dilithium3", "dilithium3"),
        "sphincs_shake_128s": _self_test("pq.py.algs.sphincs_shake_128s", "sphincs_shake_128s"),
    }
    print("pq-selftest-ok", json.dumps(results, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
