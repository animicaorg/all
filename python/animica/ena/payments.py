"""
animica.ena.payments
====================

On-chain ANM payment verification for the ENA demand side. A requester funds a
job by sending ANM to the ENA treasury address with the job's ``job_hash`` in
the transaction ``memo``; the coordinator then verifies that payment against
the Animica node RPC before the job becomes claimable.

Binding via ``memo == job_hash`` (plus txid de-duplication in the store) means
one payment funds exactly one job and can't be replayed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional

from .errors import ENAError

ANM_DECIMALS = 9
_NANO = 10 ** ANM_DECIMALS

# Status strings (from the node's tx status RPC) that mean the tx is on-chain.
_CONFIRMED = {"confirmed", "included", "mined", "finalized", "success", "ok", "accepted"}
_PENDING = {"pending", "queued", "unknown", "replicating", "propagating"}
_FAILED = {"rejected", "failed", "invalid", "dropped", "expired"}


class PaymentError(ENAError):
    code = "ENA/PAYMENT"


# ---------------------------------------------------------------------------
# Units + address
# ---------------------------------------------------------------------------

def anm_to_nano(anm: float | int | str) -> int:
    return int(round(float(anm) * _NANO))


def nano_to_anm(nano: int | str) -> float:
    return int(nano) / _NANO


def address_to_digest_hex(addr: str) -> str:
    """Return the 32-byte address digest (hex, no 0x) for an anim1… address.

    A tx's ``to_addr`` is the 32-byte sha3 digest (the alg_id is dropped), so
    this is what we compare against.
    """
    try:
        from pq.py.address import decode_address  # type: ignore
        rec = decode_address(addr, expect_hrp="anim")
        return bytes(rec.digest).hex()
    except Exception as exc:  # noqa: BLE001
        raise PaymentError(f"cannot decode treasury address {addr!r}: {exc}") from exc


def _norm_hex(h: Any) -> str:
    s = str(h or "").lower()
    return s[2:] if s.startswith("0x") else s


# ---------------------------------------------------------------------------
# RPC client
# ---------------------------------------------------------------------------

class AnimicaRPC:
    def __init__(self, url: str, timeout: float = 12.0) -> None:
        self.url = url
        self.timeout = timeout
        self._id = 0

    def call(self, method: str, params: Any) -> Any:
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id,
                           "method": method, "params": params}).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise PaymentError(f"RPC {method} unreachable at {self.url}: {exc}") from exc
        if isinstance(payload, dict) and payload.get("error"):
            raise PaymentError(f"RPC {method} error: {payload['error']}")
        return (payload or {}).get("result")

    def get_transaction(self, txhash: str) -> Optional[dict[str, Any]]:
        # The node takes the hash positionally; tolerate a leading 0x.
        return self.call("tx.getTransaction", [txhash])

    def get_status(self, txhash: str) -> dict[str, Any]:
        try:
            res = self.call("tx.getTransactionStatus", [txhash])
        except PaymentError:
            res = None
        if isinstance(res, dict):
            return res
        return {"status": "unknown"}


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_payment(rpc: AnimicaRPC, txid: str, *, treasury_address: str,
                   min_nano: int, expect_memo: str,
                   require_confirmed: bool = True) -> dict[str, Any]:
    """Verify an ANM payment funds a specific job.

    Returns ``{ok, reason, status, value_nano, from_addr, pending}``. ``pending``
    is True when the tx exists but isn't confirmed yet (caller should retry),
    distinct from a hard failure (``ok=False, pending=False``).
    """
    out = {"ok": False, "reason": "", "status": "unknown", "value_nano": 0,
           "from_addr": None, "pending": False, "txid": _norm_hex(txid)}

    tx = rpc.get_transaction(txid)
    if not tx:
        out["reason"] = "tx_not_found"
        out["pending"] = True  # may simply not have propagated yet
        return out

    status = str(tx.get("status") or rpc.get_status(txid).get("status") or "unknown").lower()
    out["status"] = status

    to_hex = _norm_hex(tx.get("to_addr") or tx.get("to"))
    want_hex = address_to_digest_hex(treasury_address)
    if to_hex != want_hex:
        out["reason"] = "wrong_recipient"
        return out

    memo = str(tx.get("memo") or "")
    if memo != expect_memo:
        out["reason"] = "memo_mismatch"
        return out

    try:
        value_nano = int(tx.get("value"))
    except (TypeError, ValueError):
        out["reason"] = "bad_value"
        return out
    out["value_nano"] = value_nano
    out["from_addr"] = tx.get("from_addr") or tx.get("from")

    if value_nano < min_nano:
        out["reason"] = "underpaid"
        return out

    if status in _FAILED:
        out["reason"] = f"tx_{status}"
        return out
    if require_confirmed and status not in _CONFIRMED:
        # found + correct + funded, but not yet on-chain → retry later
        out["reason"] = "awaiting_confirmation"
        out["pending"] = True
        return out

    out["ok"] = True
    out["reason"] = "verified"
    return out
