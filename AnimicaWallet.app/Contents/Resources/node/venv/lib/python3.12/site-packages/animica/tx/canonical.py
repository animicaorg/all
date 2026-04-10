from __future__ import annotations

import hashlib
import json

from .types import TxBody


def canonical_sign_bytes(*, body: TxBody, chain_id: int, network_id: str, version: int = 1, domain: str = "tx") -> bytes:
    payload = {
        "chain_id": int(chain_id),
        "domain": str(domain),
        "memo": body.memo,
        "network_id": network_id,
        "nonce": int(body.nonce),
        "to": body.to_addr,
        "from": body.from_addr,
        "value": int(body.value),
        "fee": int(body.fee),
        "valid_after": int(body.valid_after),
        "valid_until": int(body.valid_until),
        "version": int(version),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_sign_hash(*, sign_bytes: bytes) -> bytes:
    return hashlib.sha3_512(sign_bytes).digest()
