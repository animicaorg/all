from __future__ import annotations

"""
Animica core/types/tx.py
========================

Transaction model (unsigned/signed), canonical CBOR encoding per spec/tx_format.cddl,
SignBytes domain separation, and helpers to build Transfer/Deploy/Call txs.

Design highlights
-----------------
- **Kinds**: TRANSFER, DEPLOY, CALL (enum `TxKind`)
- **Encoding**: canonical CBOR; field order is irrelevant on-wire but our encoder
  (core.encoding.cbor) is deterministic.
- **SignBytes**: domain-separated bytes for PQ signing:
    sign_bytes = CanonicalSignBytes("animica/tx.sign", UnsignedTxMap)
- **TxID**: sha3_256(CBOR(SignedTxMap)), i.e., includes signatures, so two differently
  signed copies of the same unsigned payload have distinct ids (prevents malleability).
- **Address**: 32-byte raw (public key digest) here. Bech32m conversion happens at
  RPC/SDK edges; core stays binary.
- **AccessList**: optional, prepares for optimistic scheduler & DA-aware metering.

See also:
- spec/domains.yaml   (domain strings)
- spec/tx_format.cddl (wire layout)
"""

from dataclasses import dataclass, field, replace
from enum import IntEnum
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core.utils.hash import sha3_256
from core.utils.bytes import expect_len, to_hex
from core.encoding.cbor import cbor_dumps, cbor_loads
from core.encoding.canonical import signbytes_tx as canonical_sign_bytes


# ---- constants (keep in sync with spec/domains.yaml) ----

ADDRESS_LEN = 32                                  # raw address length
PUBKEY_MAX = 2048                                 # enough for Dilithium3/Sphincs variants
SIG_MAX = 4096                                    # safety cap for PQ signatures


# ---- enums & small types ----

class TxKind(IntEnum):
    TRANSFER = 0
    DEPLOY   = 1
    CALL     = 2


@dataclass(frozen=True)
class AccessEntry:
    addr: bytes
    storage_keys: Tuple[bytes, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "addr", expect_len(self.addr, ADDRESS_LEN, name="AccessEntry.addr"))
        for k in self.storage_keys:
            if not isinstance(k, (bytes, bytearray)):
                raise TypeError("AccessEntry.storage_keys must be bytes[]")

    def to_obj(self) -> Mapping[str, Any]:
        return {"addr": self.addr, "keys": list(self.storage_keys)}

    @staticmethod
    def from_obj(o: Mapping[str, Any]) -> "AccessEntry":
        return AccessEntry(
            addr=bytes(o["addr"]),
            storage_keys=tuple(bytes(x) for x in o.get("keys", [])),
        )


@dataclass(frozen=True)
class PqSignature:
    alg_id: int
    pubkey: bytes
    sig: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.alg_id, int) or self.alg_id < 0:
            raise ValueError("PqSignature.alg_id must be a non-negative int")
        if not isinstance(self.pubkey, (bytes, bytearray)):
            raise TypeError("PqSignature.pubkey must be bytes")
        if not isinstance(self.sig, (bytes, bytearray)):
            raise TypeError("PqSignature.sig must be bytes")
        if len(self.pubkey) > PUBKEY_MAX or len(self.sig) > SIG_MAX:
            raise ValueError("PqSignature.{pubkey,sig} exceed safety caps")

    def to_obj(self) -> Mapping[str, Any]:
        return {"alg": self.alg_id, "pubkey": bytes(self.pubkey), "sig": bytes(self.sig)}

    @staticmethod
    def from_obj(o: Mapping[str, Any]) -> "PqSignature":
        return PqSignature(
            alg_id=int(o["alg"]),
            pubkey=bytes(o["pubkey"]),
            sig=bytes(o["sig"]),
        )


# ---- payloads ----

@dataclass(frozen=True)
class TxTransfer:
    to: bytes
    amount: int
    data: bytes = b""

    def __post_init__(self) -> None:
        object.__setattr__(self, "to", expect_len(self.to, ADDRESS_LEN, name="TxTransfer.to"))
        if self.amount < 0:
            raise ValueError("TxTransfer.amount must be ≥ 0")
        if not isinstance(self.data, (bytes, bytearray)):
            raise TypeError("TxTransfer.data must be bytes")

    def to_obj(self) -> Mapping[str, Any]:
        return {"to": self.to, "amount": int(self.amount), "data": bytes(self.data)}

    @staticmethod
    def from_obj(o: Mapping[str, Any]) -> "TxTransfer":
        return TxTransfer(to=bytes(o["to"]), amount=int(o["amount"]), data=bytes(o.get("data", b"")))


@dataclass(frozen=True)
class TxDeploy:
    """
    Deploy Python-VM contract code with a manifest. Core treats them as opaque bytes here.
    Tools (studio/services) recompile & verify code hashes separately.
    """
    code: bytes               # source or bytecode (per spec/manifest)
    manifest: bytes           # canonical JSON bytes of manifest (ABI, caps, metadata)

    def __post_init__(self) -> None:
        if not isinstance(self.code, (bytes, bytearray)):
            raise TypeError("TxDeploy.code must be bytes")
        if not isinstance(self.manifest, (bytes, bytearray)):
            raise TypeError("TxDeploy.manifest must be bytes")
        if len(self.code) == 0 or len(self.manifest) == 0:
            raise ValueError("TxDeploy.{code,manifest} must be non-empty")

    def to_obj(self) -> Mapping[str, Any]:
        return {"code": bytes(self.code), "manifest": bytes(self.manifest)}

    @staticmethod
    def from_obj(o: Mapping[str, Any]) -> "TxDeploy":
        return TxDeploy(code=bytes(o["code"]), manifest=bytes(o["manifest"]))


@dataclass(frozen=True)
class TxCall:
    to: bytes
    data: bytes  # ABI-encoded call payload (function selector + args)

    def __post_init__(self) -> None:
        object.__setattr__(self, "to", expect_len(self.to, ADDRESS_LEN, name="TxCall.to"))
        if not isinstance(self.data, (bytes, bytearray)):
            raise TypeError("TxCall.data must be bytes")
        if len(self.data) == 0:
            raise ValueError("TxCall.data must be non-empty")

    def to_obj(self) -> Mapping[str, Any]:
        return {"to": self.to, "data": bytes(self.data)}

    @staticmethod
    def from_obj(o: Mapping[str, Any]) -> "TxCall":
        return TxCall(to=bytes(o["to"]), data=bytes(o["data"]))


TxPayload = TxTransfer | TxDeploy | TxCall


# ---- unsigned + signed tx model ----

@dataclass(frozen=True)
class UnsignedTx:
    chain_id: int
    nonce: int
    gas_price: int
    gas_limit: int
    sender: bytes
    kind: TxKind
    payload: TxPayload
    access_list: Tuple[AccessEntry, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.chain_id <= 0:
            raise ValueError("UnsignedTx.chain_id must be positive")
        if self.nonce < 0:
            raise ValueError("UnsignedTx.nonce must be ≥ 0")
        if self.gas_price < 0 or self.gas_limit <= 0:
            raise ValueError("UnsignedTx.gas_price must be ≥ 0 and gas_limit > 0")
        object.__setattr__(self, "sender", expect_len(self.sender, ADDRESS_LEN, name="UnsignedTx.sender"))
        if not isinstance(self.kind, TxKind):
            raise TypeError("UnsignedTx.kind must be TxKind")
        for ae in self.access_list:
            if not isinstance(ae, AccessEntry):
                raise TypeError("UnsignedTx.access_list elements must be AccessEntry")

    # -- canonical object (for CBOR & SignBytes) --

    def to_obj(self) -> Mapping[str, Any]:
        # Payload discriminated union
        if self.kind is TxKind.TRANSFER:
            payload_obj = {"t": int(TxKind.TRANSFER), "v": self.payload.to_obj()}  # type: ignore[union-attr]
        elif self.kind is TxKind.DEPLOY:
            payload_obj = {"t": int(TxKind.DEPLOY), "v": self.payload.to_obj()}    # type: ignore[union-attr]
        elif self.kind is TxKind.CALL:
            payload_obj = {"t": int(TxKind.CALL), "v": self.payload.to_obj()}      # type: ignore[union-attr]
        else:  # pragma: no cover
            raise ValueError("unknown tx kind")

        return {
            "v": 1,  # tx version
            "chainId": self.chain_id,
            "from": self.sender,
            "nonce": self.nonce,
            "gas": {"price": self.gas_price, "limit": self.gas_limit},
            "payload": payload_obj,
            "accessList": [ae.to_obj() for ae in self.access_list],
        }

    def to_cbor(self) -> bytes:
        return cbor_dumps(self.to_obj())

    def sign_bytes(self) -> bytes:
        """
        Domain-separated canonical bytes for PQ signing.
        """
        return canonical_sign_bytes(self.to_obj(), self.chain_id)

    @staticmethod
    def from_obj(o: Mapping[str, Any]) -> "UnsignedTx":
        if int(o.get("v", 1)) != 1:
            raise ValueError("Unsupported tx version")
        chain_id = int(o["chainId"])
        sender = bytes(o["from"])
        nonce = int(o["nonce"])
        gas = o["gas"]
        gas_price = int(gas["price"])
        gas_limit = int(gas["limit"])

        payload_tag = int(o["payload"]["t"])
        payload_val = o["payload"]["v"]
        if payload_tag == int(TxKind.TRANSFER):
            payload = TxTransfer.from_obj(payload_val)
            kind = TxKind.TRANSFER
        elif payload_tag == int(TxKind.DEPLOY):
            payload = TxDeploy.from_obj(payload_val)
            kind = TxKind.DEPLOY
        elif payload_tag == int(TxKind.CALL):
            payload = TxCall.from_obj(payload_val)
            kind = TxKind.CALL
        else:
            raise ValueError("Unknown payload tag")

        al = tuple(AccessEntry.from_obj(x) for x in o.get("accessList", []))

        return UnsignedTx(
            chain_id=chain_id,
            nonce=nonce,
            gas_price=gas_price,
            gas_limit=gas_limit,
            sender=sender,
            kind=kind,
            payload=payload,
            access_list=al,
        )

    @staticmethod
    def from_cbor(b: bytes) -> "UnsignedTx":
        return UnsignedTx.from_obj(cbor_loads(b))

    # convenience builders
    @staticmethod
    def build_transfer(
        *, chain_id: int, sender: bytes, nonce: int, gas_price: int, gas_limit: int,
        to: bytes, amount: int, data: bytes = b"", access_list: Optional[List[AccessEntry]] = None
    ) -> "UnsignedTx":
        return UnsignedTx(
            chain_id=chain_id,
            sender=sender,
            nonce=nonce,
            gas_price=gas_price,
            gas_limit=gas_limit,
            kind=TxKind.TRANSFER,
            payload=TxTransfer(to=to, amount=amount, data=data),
            access_list=tuple(access_list or ()),
        )

    @staticmethod
    def build_deploy(
        *, chain_id: int, sender: bytes, nonce: int, gas_price: int, gas_limit: int,
        code: bytes, manifest: bytes, access_list: Optional[List[AccessEntry]] = None
    ) -> "UnsignedTx":
        return UnsignedTx(
            chain_id=chain_id,
            sender=sender,
            nonce=nonce,
            gas_price=gas_price,
            gas_limit=gas_limit,
            kind=TxKind.DEPLOY,
            payload=TxDeploy(code=code, manifest=manifest),
            access_list=tuple(access_list or ()),
        )

    @staticmethod
    def build_call(
        *, chain_id: int, sender: bytes, nonce: int, gas_price: int, gas_limit: int,
        to: bytes, data: bytes, access_list: Optional[List[AccessEntry]] = None
    ) -> "UnsignedTx":
        return UnsignedTx(
            chain_id=chain_id,
            sender=sender,
            nonce=nonce,
            gas_price=gas_price,
            gas_limit=gas_limit,
            kind=TxKind.CALL,
            payload=TxCall(to=to, data=data),
            access_list=tuple(access_list or ()),
        )


@dataclass(frozen=True)
class Tx:
    """
    Signed transaction.
    """
    unsigned: UnsignedTx
    sigs: Tuple[PqSignature, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # basic sanity
        for s in self.sigs:
            if not isinstance(s, PqSignature):
                raise TypeError("Tx.sigs must contain PqSignature")
        if len(self.sigs) == 0:
            # Allowed in mempool precheck flows but block import will require ≥1
            pass

    # encoding

    def to_obj(self) -> Mapping[str, Any]:
        return {"tx": self.unsigned.to_obj(), "sigs": [s.to_obj() for s in self.sigs]}

    def to_cbor(self) -> bytes:
        return cbor_dumps(self.to_obj())

    @staticmethod
    def from_obj(o: Mapping[str, Any]) -> "Tx":
        return Tx(
            unsigned=UnsignedTx.from_obj(o["tx"]),
            sigs=tuple(PqSignature.from_obj(s) for s in o.get("sigs", [])),
        )

    @staticmethod
    def from_cbor(b: bytes) -> "Tx":
        return Tx.from_obj(cbor_loads(b))

    # hashing

    def txid(self) -> bytes:
        """Hash of the **signed** tx (CBOR of full object)."""
        return sha3_256(self.to_cbor())

    def unsigned_hash(self) -> bytes:
        """Hash of the unsigned part (for dedupe / mempool)."""
        return sha3_256(self.unsigned.to_cbor())

    # construction

    def with_signature(self, sig: PqSignature) -> "Tx":
        return replace(self, sigs=self.sigs + (sig,))

    def require_min_sigs(self, n: int = 1) -> None:
        if len(self.sigs) < n:
            raise ValueError(f"Tx requires at least {n} signature(s)")

    # human helpers

    def __str__(self) -> str:
        kind = self.unsigned.kind.name
        txid_hex = to_hex(self.txid())
        return f"Tx<{kind} {txid_hex[:10]}… nonce={self.unsigned.nonce} gas={self.unsigned.gas_limit}@{self.unsigned.gas_price}>"

    def summary(self) -> Mapping[str, Any]:
        u = self.unsigned
        base = {
            "txid": to_hex(self.txid()),
            "kind": u.kind.name,
            "chainId": u.chain_id,
            "from": to_hex(u.sender),
            "nonce": u.nonce,
            "gasLimit": u.gas_limit,
            "gasPrice": u.gas_price,
            "sigs": [{"alg": s.alg_id, "pubkey": to_hex(s.pubkey)} for s in self.sigs],
        }
        # enrich payload
        if u.kind is TxKind.TRANSFER:
            p: TxTransfer = u.payload  # type: ignore[assignment]
            base["to"] = to_hex(p.to)
            base["amount"] = p.amount
        elif u.kind is TxKind.DEPLOY:
            p: TxDeploy = u.payload  # type: ignore[assignment]
            base["codeHash"] = to_hex(sha3_256(p.code))
            base["manifestHash"] = to_hex(sha3_256(p.manifest))
        elif u.kind is TxKind.CALL:
            p: TxCall = u.payload  # type: ignore[assignment]
            base["to"] = to_hex(p.to)
            base["dataLen"] = len(p.data)
        return base


# ---- simple unit-testable self-check (optional) ----
if __name__ == "__main__":  # pragma: no cover
    import os
    import json
    import secrets

    def rand_addr() -> bytes:
        return secrets.token_bytes(ADDRESS_LEN)

    u = UnsignedTx.build_transfer(
        chain_id=1,
        sender=rand_addr(),
        nonce=0,
        gas_price=1_000,
        gas_limit=50_000,
        to=rand_addr(),
        amount=123456789,
    )
    tx = Tx(unsigned=u)
    print("Unsigned SignBytes(hex):", to_hex(u.sign_bytes()))
    print("Unsigned hash:", to_hex(tx.unsigned_hash()))
    print("Signed (0 sigs) txid:", to_hex(tx.txid()))
    # Fake signature just to validate round-trip:
    tx2 = tx.with_signature(PqSignature(alg_id=1, pubkey=b"pk", sig=b"sig"))
    enc = tx2.to_cbor()
    dec = Tx.from_cbor(enc)
    assert dec.to_obj() == tx2.to_obj()
    print(json.dumps(dec.summary(), indent=2))
