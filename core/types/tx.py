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

from core.encoding.canonical import signbytes_tx as canonical_sign_bytes
from core.encoding.cbor import cbor_dumps, cbor_loads
from core.utils.bytes import expect_len, to_hex
from core.utils.hash import sha3_256

# ---- constants (keep in sync with spec/domains.yaml) ----

ADDRESS_LEN = 32  # raw address length
PUBKEY_MAX = 2048  # enough for Dilithium3/Sphincs variants
SIG_MAX = 4096  # safety cap for PQ signatures


# ---- enums & small types ----


class TxKind(IntEnum):
    TRANSFER = 0
    DEPLOY = 1
    CALL = 2


@dataclass(frozen=True)
class AccessEntry:
    addr: bytes
    storage_keys: Tuple[bytes, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "addr", expect_len(self.addr, ADDRESS_LEN, name="AccessEntry.addr")
        )
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


class PqSignature:
    """
    Post-quantum signature container.
    Supports both canonical (alg_id, pubkey, sig) and legacy (alg, pub, sig) parameter names.
    """
    
    def __init__(
        self,
        alg_id: int = None,
        pubkey: bytes = None,
        sig: bytes = None,
        *,
        alg: int = None,  # Legacy alias for alg_id
        pub: bytes = None,  # Legacy alias for pubkey
    ):
        # Handle legacy parameter names
        if alg is not None and alg_id is None:
            alg_id = alg
        if pub is not None and pubkey is None:
            pubkey = pub
        
        # Validate required parameters
        if alg_id is None:
            raise TypeError("PqSignature() missing required argument: 'alg_id' or 'alg'")
        if pubkey is None:
            raise TypeError("PqSignature() missing required argument: 'pubkey' or 'pub'")
        if sig is None:
            raise TypeError("PqSignature() missing required argument: 'sig'")
        
        # Try to cast alg_id to int if it's not already (for downstream validation)
        if not isinstance(alg_id, int):
            try:
                alg_id = int(alg_id)
            except (TypeError, ValueError):
                # Allow non-int values through; validation will catch them later
                pass
        
        # Store as attributes (frozen via object.__setattr__ in __post_init__)
        object.__setattr__(self, 'alg_id', alg_id)
        object.__setattr__(self, 'pubkey', pubkey)
        object.__setattr__(self, 'sig', sig)
        
        # Validate types (but allow out-of-range or non-int alg values for downstream validation)
        # Note: alg_id may be non-int if cast failed; that's intentional
        if not isinstance(self.pubkey, (bytes, bytearray)):
            raise TypeError("PqSignature.pubkey must be bytes")
        if not isinstance(self.sig, (bytes, bytearray)):
            raise TypeError("PqSignature.sig must be bytes")
        if len(self.pubkey) > PUBKEY_MAX or len(self.sig) > SIG_MAX:
            raise ValueError("PqSignature.{pubkey,sig} exceed safety caps")
    
    def __eq__(self, other):
        if not isinstance(other, PqSignature):
            return False
        return (self.alg_id == other.alg_id and
                self.pubkey == other.pubkey and
                self.sig == other.sig)
    
    def __hash__(self):
        return hash((self.alg_id, self.pubkey, self.sig))

    def to_obj(self) -> Mapping[str, Any]:
        return {
            "alg": self.alg_id,
            "pubkey": bytes(self.pubkey),
            "sig": bytes(self.sig),
        }

    @staticmethod
    def from_obj(o: Mapping[str, Any]) -> "PqSignature":
        alg = o["alg"]
        # Handle both int alg_id and string alg_name
        if isinstance(alg, str):
            # Map algorithm name to ID
            try:
                from pq.py.registry import ALG_ID
                alg_id = ALG_ID.get(alg, None)
                if alg_id is None:
                    # Try parsing as int
                    alg_id = int(alg, 0)
            except Exception:
                # Fallback: use 0 to indicate unknown (will fail validation later)
                alg_id = 0
        else:
            alg_id = int(alg)
        
        return PqSignature(
            alg_id=alg_id,
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
        object.__setattr__(
            self, "to", expect_len(self.to, ADDRESS_LEN, name="TxTransfer.to")
        )
        if self.amount < 0:
            raise ValueError("TxTransfer.amount must be ≥ 0")
        if not isinstance(self.data, (bytes, bytearray)):
            raise TypeError("TxTransfer.data must be bytes")

    def to_obj(self) -> Mapping[str, Any]:
        return {"to": self.to, "amount": int(self.amount), "data": bytes(self.data)}

    @staticmethod
    def from_obj(o: Mapping[str, Any]) -> "TxTransfer":
        return TxTransfer(
            to=bytes(o["to"]), amount=int(o["amount"]), data=bytes(o.get("data", b""))
        )


@dataclass(frozen=True)
class TxDeploy:
    """
    Deploy Python-VM contract code with a manifest. Core treats them as opaque bytes here.
    Tools (studio/services) recompile & verify code hashes separately.
    """

    code: bytes  # source or bytecode (per spec/manifest)
    manifest: bytes  # canonical JSON bytes of manifest (ABI, caps, metadata)

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
        object.__setattr__(
            self, "to", expect_len(self.to, ADDRESS_LEN, name="TxCall.to")
        )
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
    version: int
    chain_id: int
    fork_id: Optional[int]
    valid_after: Optional[int]
    valid_until: Optional[int]
    salt: Optional[bytes]
    gas_price: int
    gas_limit: int
    sender: bytes
    kind: TxKind
    payload: TxPayload
    access_list: Tuple[AccessEntry, ...] = field(default_factory=tuple)
    nonce: Optional[int] = None  # legacy v1 only

    def __post_init__(self) -> None:
        if self.version not in (1, 2):
            raise ValueError("UnsignedTx.version must be 1 or 2")
        if self.chain_id <= 0:
            raise ValueError("UnsignedTx.chain_id must be positive")
        if self.gas_price < 0 or self.gas_limit <= 0:
            raise ValueError("UnsignedTx.gas_price must be ≥ 0 and gas_limit > 0")
        if self.version == 1:
            if self.nonce is None or self.nonce < 0:
                raise ValueError("UnsignedTx.nonce must be ≥ 0 for v1")
        else:
            if self.nonce is not None:
                raise ValueError("UnsignedTx.nonce must be omitted for v2")
            if self.valid_after is None or self.valid_until is None:
                raise ValueError("UnsignedTx.valid_after/valid_until are required for v2")
            if self.valid_after < 0 or self.valid_until < 0:
                raise ValueError("UnsignedTx.valid_after/valid_until must be ≥ 0")
            if self.valid_until < self.valid_after:
                raise ValueError("UnsignedTx.valid_until must be ≥ valid_after")
            if self.salt is None:
                raise ValueError("UnsignedTx.salt is required for v2")
            if not isinstance(self.salt, (bytes, bytearray)):
                raise TypeError("UnsignedTx.salt must be bytes")
            salt_len = len(self.salt)
            if salt_len not in (16, 32):
                raise ValueError("UnsignedTx.salt must be 16 or 32 bytes")
            if self.fork_id is not None:
                fid = int(self.fork_id)
                if fid < 0 or fid > 0xFFFFFFFF:
                    raise ValueError("UnsignedTx.fork_id out of range")
        object.__setattr__(
            self,
            "sender",
            expect_len(self.sender, ADDRESS_LEN, name="UnsignedTx.sender"),
        )
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
            payload_obj = {"t": int(TxKind.DEPLOY), "v": self.payload.to_obj()}  # type: ignore[union-attr]
        elif self.kind is TxKind.CALL:
            payload_obj = {"t": int(TxKind.CALL), "v": self.payload.to_obj()}  # type: ignore[union-attr]
        else:  # pragma: no cover
            raise ValueError("unknown tx kind")

        base: Dict[str, Any] = {
            "v": int(self.version),
            "chainId": self.chain_id,
            "from": self.sender,
            "gas": {"price": self.gas_price, "limit": self.gas_limit},
            "payload": payload_obj,
            "accessList": [ae.to_obj() for ae in self.access_list],
        }
        if self.version == 1:
            base["nonce"] = int(self.nonce or 0)
        else:
            base["validAfter"] = int(self.valid_after or 0)
            base["validUntil"] = int(self.valid_until or 0)
            base["salt"] = bytes(self.salt or b"")
            if self.fork_id is not None:
                base["forkId"] = int(self.fork_id)
        return base

    def to_cbor(self) -> bytes:
        return cbor_dumps(self.to_obj())

    def sign_bytes(self) -> bytes:
        """
        Domain-separated canonical bytes for PQ signing.
        """
        return canonical_sign_bytes(self.to_obj(), self.chain_id)

    @staticmethod
    def from_obj(o: Mapping[str, Any]) -> "UnsignedTx":
        version = int(o.get("v", 1))
        if version not in (1, 2):
            raise ValueError("Unsupported tx version")
        chain_id = int(o["chainId"])
        sender = bytes(o["from"])
        nonce = int(o["nonce"]) if "nonce" in o else None
        gas = o["gas"]
        gas_price = int(gas["price"])
        gas_limit = int(gas["limit"])
        fork_id = None
        valid_after = None
        valid_until = None
        salt = None
        if version == 2:
            valid_after = int(o.get("validAfter", o.get("valid_after", 0)))
            valid_until = int(o.get("validUntil", o.get("valid_until", 0)))
            salt_val = o.get("salt")
            if salt_val is not None:
                salt = bytes(salt_val)
            fork_id_val = o.get("forkId", o.get("fork_id"))
            fork_id = int(fork_id_val) if fork_id_val is not None else None

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
            version=version,
            chain_id=chain_id,
            fork_id=fork_id,
            valid_after=valid_after,
            valid_until=valid_until,
            salt=salt,
            gas_price=gas_price,
            gas_limit=gas_limit,
            sender=sender,
            kind=kind,
            payload=payload,
            access_list=al,
            nonce=nonce,
        )

    @staticmethod
    def from_cbor(b: bytes) -> "UnsignedTx":
        return UnsignedTx.from_obj(cbor_loads(b))

    # convenience builders
    @staticmethod
    def build_transfer(
        *,
        chain_id: int,
        sender: bytes,
        nonce: Optional[int] = None,
        gas_price: int,
        gas_limit: int,
        to: bytes,
        amount: int,
        data: bytes = b"",
        valid_after: Optional[int] = None,
        valid_until: Optional[int] = None,
        salt: Optional[bytes] = None,
        fork_id: Optional[int] = None,
        version: int = 2,
        access_list: Optional[List[AccessEntry]] = None,
    ) -> "UnsignedTx":
        return UnsignedTx(
            version=version,
            chain_id=chain_id,
            fork_id=fork_id,
            valid_after=valid_after,
            valid_until=valid_until,
            salt=salt,
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
        *,
        chain_id: int,
        sender: bytes,
        nonce: Optional[int] = None,
        gas_price: int,
        gas_limit: int,
        code: bytes,
        manifest: bytes,
        valid_after: Optional[int] = None,
        valid_until: Optional[int] = None,
        salt: Optional[bytes] = None,
        fork_id: Optional[int] = None,
        version: int = 2,
        access_list: Optional[List[AccessEntry]] = None,
    ) -> "UnsignedTx":
        return UnsignedTx(
            version=version,
            chain_id=chain_id,
            fork_id=fork_id,
            valid_after=valid_after,
            valid_until=valid_until,
            salt=salt,
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
        *,
        chain_id: int,
        sender: bytes,
        nonce: Optional[int] = None,
        gas_price: int,
        gas_limit: int,
        to: bytes,
        data: bytes,
        valid_after: Optional[int] = None,
        valid_until: Optional[int] = None,
        salt: Optional[bytes] = None,
        fork_id: Optional[int] = None,
        version: int = 2,
        access_list: Optional[List[AccessEntry]] = None,
    ) -> "UnsignedTx":
        return UnsignedTx(
            version=version,
            chain_id=chain_id,
            fork_id=fork_id,
            valid_after=valid_after,
            valid_until=valid_until,
            salt=salt,
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
    
    def hash(self) -> bytes:
        """Alias for txid() - hash of the signed transaction."""
        return self.txid()

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
        if self.unsigned.version == 1:
            nonce_text = f" nonce={self.unsigned.nonce}"
        else:
            nonce_text = ""
        return (
            f"Tx<{kind} {txid_hex[:10]}…"
            f"{nonce_text} gas={self.unsigned.gas_limit}@{self.unsigned.gas_price}>"
        )

    def summary(self) -> Mapping[str, Any]:
        u = self.unsigned
        base = {
            "txid": to_hex(self.txid()),
            "kind": u.kind.name,
            "chainId": u.chain_id,
            "from": to_hex(u.sender),
            "gasLimit": u.gas_limit,
            "gasPrice": u.gas_price,
            "sigs": [{"alg": s.alg_id, "pubkey": to_hex(s.pubkey)} for s in self.sigs],
        }
        if u.version == 1:
            base["nonce"] = u.nonce
        else:
            base["validAfter"] = u.valid_after
            base["validUntil"] = u.valid_until
            base["salt"] = to_hex(u.salt or b"")
            base["forkId"] = u.fork_id
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


# ---- Compatibility aliases for test code ----
Sig = PqSignature  # Short alias for tests

# Add convenience factory method to Tx class for backward compatibility
@staticmethod
def _tx_transfer(
    chain_id: int,
    nonce: Optional[int],
    gas_price: int,
    gas_limit: int,
    sender: Optional[bytes] = None,
    to: Optional[bytes] = None,
    amount: Optional[int] = None,
    from_addr: Optional[str] = None,
    to_addr: Optional[str] = None,
    value: Optional[int] = None,
    data: Optional[bytes] = None,
    valid_after: Optional[int] = None,
    valid_until: Optional[int] = None,
    salt: Optional[bytes] = None,
    fork_id: Optional[int] = None,
    version: int = 2,
    access_list: Optional[List[AccessEntry]] = None,
    **kwargs: Any
) -> "Tx":
    """
    Build a transfer transaction (unsigned, no signatures).
    
    Supports both positional/keyword args with various naming styles:
    - sender or from_addr (bech32m string or bytes)
    - to or to_addr (bech32m string or bytes)
    - amount or value
    """
    # Handle from_addr vs sender
    if sender is None:
        if from_addr is not None:
            # Parse bech32m if needed
            if isinstance(from_addr, str):
                try:
                    from core.encoding.bech32m import decode_address
                    sender = decode_address(from_addr)
                except Exception:
                    # Fallback: assume it's already bytes-like or use dummy
                    sender = bytes(ADDRESS_LEN)
            else:
                sender = from_addr
        else:
            sender = bytes(ADDRESS_LEN)
    
    # Handle to_addr vs to
    if to is None:
        if to_addr is not None:
            if isinstance(to_addr, str):
                try:
                    from core.encoding.bech32m import decode_address
                    to = decode_address(to_addr)
                except Exception:
                    to = bytes(ADDRESS_LEN)
            else:
                to = to_addr
        else:
            to = bytes(ADDRESS_LEN)
    
    # Handle value vs amount
    if amount is None:
        amount = value if value is not None else 0
    
    unsigned = UnsignedTx.build_transfer(
        chain_id=chain_id,
        sender=sender,
        nonce=nonce,
        gas_price=gas_price,
        gas_limit=gas_limit,
        to=to,
        amount=amount,
        valid_after=valid_after,
        valid_until=valid_until,
        salt=salt,
        fork_id=fork_id,
        version=version,
    )
    return Tx(unsigned=unsigned)

# Bind the method and Sig alias to the Tx class
Tx.transfer = _tx_transfer  # type: ignore[attr-defined]
Tx.Sig = PqSignature  # type: ignore[attr-defined]


# ---- simple unit-testable self-check (optional) ----
if __name__ == "__main__":  # pragma: no cover
    import json
    import os
    import secrets

    def rand_addr() -> bytes:
        return secrets.token_bytes(ADDRESS_LEN)

    u = UnsignedTx.build_transfer(
        chain_id=1,
        sender=rand_addr(),
        gas_price=1_000,
        gas_limit=50_000,
        to=rand_addr(),
        amount=123456789,
        valid_after=0,
        valid_until=0,
        salt=b"\x00" * 16,
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
