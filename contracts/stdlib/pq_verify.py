"""Mock PQ verify helper duplicated into contracts' stdlib for contract import.

Contracts can `from stdlib import pq_verify` and call `pq_verify.verify(...)`.
This mirrors `vm_py/stdlib/pq_verify.py` and is intended for development only.
"""

from __future__ import annotations

import hashlib
import hmac


def verify(pubkey_hex: bytes | str, message: bytes, sig_hex: bytes | str) -> bool:
    # Same tolerant parsing as the VM-side helper
    if isinstance(pubkey_hex, bytes):
        try:
            pubkey = bytes.fromhex(
                pubkey_hex.decode()
                if all(32 <= b <= 127 for b in pubkey_hex)
                else pubkey_hex.decode("utf8")
            )
        except Exception:
            pubkey = pubkey_hex
    else:
        pubkey = bytes.fromhex(pubkey_hex)

    if isinstance(sig_hex, bytes):
        sig = bytes.fromhex(
            sig_hex.decode()
            if all(32 <= b <= 127 for b in sig_hex)
            else sig_hex.decode("utf8")
        )
    else:
        sig = bytes.fromhex(sig_hex)

    expected = hmac.new(pubkey, message, hashlib.sha256).digest()
    return hmac.compare_digest(expected, sig)
