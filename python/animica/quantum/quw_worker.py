"""
animica.quantum.quw_worker
==========================

Quantum Useful Work worker: the process a "serious" QRNG node runs to earn
rewards by contributing hardware-attested quantum entropy to the chain beacon.

Loop per round:
  1. get a fresh challenge nonce (local service or node RPC),
  2. read entropy from the best available source (IDQ Quantis -> USB -> /dev/hwrng
     -> software fallback), gated by the SP 800-90B health battery,
  3. sign the transcript with a YubiHSM2 / TPM2.0 (or software self-signer),
  4. submit the attested contribution; on accept the entropy is mixed into the
     beacon and the contributor is credited under ProofType.QUANTUM.

Designed to run with zero hardware (software fallback, non-attested) so the lane
is fully exercisable; on a real node it auto-selects the attested hardware path.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Callable, Dict, Optional

from randomness.qrng import providers, hsm_tpm, health
from randomness.qrng import contribution as quw


class QuwWorker:
    def __init__(
        self,
        *,
        address: str,
        rpc_url: Optional[str] = None,
        signer_prefer: Optional[str] = None,
        n_bytes: int = 4096,
        min_entropy_per_byte: float = health.DEFAULT_MIN_ENTROPY_PER_BYTE,
        prefer_hardware: bool = True,
        max_health_retries: int = 8,
        submit_fn: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        self.address = address
        self.rpc_url = rpc_url
        self.n_bytes = int(n_bytes)
        self.min_h = float(min_entropy_per_byte)
        self.max_health_retries = int(max_health_retries)
        # Raw source (NOT health-gated here; we evaluate per-batch and retry).
        gated = providers.auto_select(prefer_hardware=prefer_hardware, health_gated=False,
                                      min_entropy_per_byte=self.min_h)
        self.source = gated
        self.signer = hsm_tpm.make_signer(prefer=signer_prefer)
        self._submit_fn = submit_fn or self._default_submit

    # --- transport ---
    def _rpc(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.rpc_url, data=body,
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec - operator URL
            out = json.loads(resp.read().decode())
        if "error" in out and out["error"]:
            raise RuntimeError(f"rpc {method} error: {out['error']}")
        return out.get("result") or {}

    def _default_submit(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if self.rpc_url:
            return self._rpc(method, params)
        # Local in-process service (single-node / testing).
        from randomness.qrng.service import get_service
        svc = get_service()
        if method == "rand.getQuantumChallenge":
            return svc.get_challenge(int(params.get("round_id", 0)))
        if method == "rand.contributeQuantumEntropy":
            return svc.contribute(params.get("contribution") or params)
        raise RuntimeError(f"unknown method {method}")

    # --- single round ---
    def run_once(self, round_id: int) -> Dict[str, Any]:
        ch = self._submit_fn("rand.getQuantumChallenge", {"round_id": int(round_id)})
        nonce = bytes.fromhex(ch["nonce_hex"])
        # Read until a batch passes the health gate (a degraded source is dropped).
        last_report = None
        data = b""
        for _ in range(self.max_health_retries):
            data = self.source.random_bytes(self.n_bytes)
            rep = health.evaluate(data, min_entropy_per_byte=self.min_h)
            last_report = rep
            if rep.passed:
                break
        if last_report is None or not last_report.passed:
            return {"round_id": round_id, "submitted": False,
                    "reason": "no healthy batch: " + "; ".join(last_report.reasons if last_report else ["?"])}
        # Build + submit (rebuild a tiny one-shot source over the chosen bytes).
        c = quw.build_contribution(_FixedSource(data, self.source.info()), self.signer,
                                   round_id=int(round_id), nonce=nonce,
                                   address=self.address, n_bytes=len(data),
                                   min_entropy_per_byte=self.min_h)
        res = self._submit_fn("rand.contributeQuantumEntropy", {"contribution": c.to_dict()})
        return {"round_id": round_id, "submitted": True,
                "source": self.source.info().as_dict(), "signer": self.signer.info().backend,
                "result": res}

    # --- continuous loop ---
    def run(self, *, round_fn: Callable[[], int], interval_s: float = 30.0,
            max_rounds: Optional[int] = None) -> None:
        seen = set()
        n = 0
        while max_rounds is None or n < max_rounds:
            rid = int(round_fn())
            if rid not in seen:
                seen.add(rid)
                try:
                    out = self.run_once(rid)
                    yield out  # type: ignore[misc]
                except Exception as e:  # keep the worker alive
                    yield {"round_id": rid, "submitted": False, "error": str(e)}
                n += 1
            time.sleep(interval_s)


class _FixedSource(providers.QuantumEntropySource):
    """Wrap an already-read, already-health-checked buffer as a one-shot source."""

    def __init__(self, data: bytes, info: providers.SourceInfo) -> None:
        self._data = data
        self._info = info

    def info(self) -> providers.SourceInfo:
        return self._info

    def random_bytes(self, n: int) -> bytes:
        if n != len(self._data):
            # Fall back to a slice/extend only if asked for a different size.
            return (self._data * ((n // len(self._data)) + 1))[:n]
        return self._data


def selftest(n_bytes: int = 4096) -> Dict[str, Any]:
    """
    Run a full local build->verify->reward roundtrip with the auto-selected source
    and signer. Returns a structured report (used by `animica quantum quw selftest`).
    """
    src = providers.auto_select(health_gated=True)
    signer = hsm_tpm.make_signer()
    import os as _os
    nonce = _os.urandom(32)
    c = quw.build_contribution(src, signer, round_id=0, nonce=nonce,
                               address="selftest", n_bytes=n_bytes)
    vr = quw.verify_contribution(c, expected_nonce=nonce)
    metrics = quw.contribution_to_quantum_metrics(c, vr)
    return {
        "source": src.info().as_dict(),
        "signer": signer.info().backend,
        "attested": vr.attested,
        "verified": vr.verified,
        "reason": vr.reason,
        "min_entropy_per_byte": round(vr.min_entropy_per_byte, 4),
        "metrics": metrics,
    }
