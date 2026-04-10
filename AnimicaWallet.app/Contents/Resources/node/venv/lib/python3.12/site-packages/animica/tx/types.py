from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

TxId = NewType("TxId", str)


@dataclass(frozen=True)
class TxBody:
    chain_id: int
    nonce: int
    from_addr: str
    to_addr: str
    value: int
    fee: int
    memo: str = ""
    valid_after: int = 0
    valid_until: int = 0


@dataclass(frozen=True)
class TxAuth:
    alg_id: int
    pubkey: bytes
    signature: bytes


@dataclass(frozen=True)
class TxEnvelope:
    version: int
    network_id: str
    body: TxBody
    auth: TxAuth


@dataclass(frozen=True)
class TxReceipt:
    tx_id: TxId
    status: str
    gas_used: int
