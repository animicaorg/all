"""
RPC models: typed JSON shapes returned/accepted by the FastAPI JSON-RPC and REST.
These are *views* over core dataclasses/records and keep field names stable for
clients and SDKs.

Includes:
- JSON-RPC 2.0 envelopes (request/response/error)
- Chain head snapshot (Head)
- Header/Block views
- Transaction and Receipt views
- Log/event view

Validation:
- Hex strings are 0x-prefixed & even-length.
- 32-byte hashes enforced where applicable.
- Bech32m addresses checked for checksum & payload length (1+32 bytes).
"""

from __future__ import annotations

from typing import Any, List, Literal, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .types import (Address, HashHex32, HexStr, decode_address,
                    ensure_hash32_hex, ensure_hex, is_address)

# -----------------------------------------------------------------------------
# JSON-RPC envelopes
# -----------------------------------------------------------------------------


class JsonRpcRequest(BaseModel):
    model_config = ConfigDict(frozen=True, str_max_length=1_000_000)
    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: Optional[Union[list[Any], dict[str, Any]]] = None
    id: Optional[Union[int, str]] = None


class JsonRpcError(BaseModel):
    model_config = ConfigDict(frozen=True)
    code: int
    message: str
    data: Optional[Any] = None


class JsonRpcResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    jsonrpc: Literal["2.0"] = "2.0"
    result: Optional[Any] = None
    error: Optional[JsonRpcError] = None
    id: Optional[Union[int, str]] = None


# -----------------------------------------------------------------------------
# Common sub-views
# -----------------------------------------------------------------------------


class LogView(BaseModel):
    """
    Contract/event log entry.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    address: Address
    topics: List[HashHex32]
    data: HexStr = Field(alias="data")

    @field_validator("address")
    @classmethod
    def _addr_ok(cls, v: str) -> Address:
        if not is_address(v):
            raise ValueError("invalid bech32m address")
        return Address(v)

    @field_validator("topics")
    @classmethod
    def _topics_ok(cls, v: Sequence[str]) -> List[HashHex32]:
        return [ensure_hash32_hex(t) for t in v]

    @field_validator("data")
    @classmethod
    def _data_hex(cls, v: str) -> HexStr:
        return ensure_hex(v)


# -----------------------------------------------------------------------------
# Head & Header
# -----------------------------------------------------------------------------


class Head(BaseModel):
    """
    Lightweight chain head snapshot.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        frozen=True,
        json_schema_extra={
            "examples": [
                {
                    "chainId": 1,
                    "number": 1024,
                    "hash": "0x" + "11" * 32,
                    "parentHash": "0x" + "22" * 32,
                    "timestamp": 1_700_000_000,
                    "thetaMicro": 1_500_000,  # micro-nats threshold
                }
            ]
        },
    )
    chain_id: int = Field(alias="chainId")
    number: int = Field(alias="number")
    hash: HashHex32 = Field(alias="hash")
    parent_hash: HashHex32 = Field(alias="parentHash")
    timestamp: int = Field(alias="timestamp")
    theta_micro: int = Field(alias="thetaMicro")

    @field_validator("hash", "parent_hash")
    @classmethod
    def _hash32(cls, v: str) -> HashHex32:
        return ensure_hash32_hex(v)


class HeaderView(BaseModel):
    """
    Full block header view (mirrors spec/header_format.cddl field names).
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    chain_id: int = Field(alias="chainId")
    number: int = Field(alias="number")
    parent_hash: HashHex32 = Field(alias="parentHash")
    timestamp: int = Field(alias="timestamp")

    state_root: HashHex32 = Field(alias="stateRoot")
    txs_root: HashHex32 = Field(alias="txsRoot")
    receipts_root: HashHex32 = Field(alias="receiptsRoot")
    proofs_root: HashHex32 = Field(alias="proofsRoot")
    da_root: HashHex32 = Field(alias="daRoot")

    alg_policy_root: HashHex32 = Field(alias="algPolicyRoot")
    poies_policy_root: HashHex32 = Field(alias="poiesPolicyRoot")

    gas_limit: int = Field(alias="gasLimit")
    gas_used: int = Field(alias="gasUsed")

    theta_micro: int = Field(alias="thetaMicro")
    mix_seed: HexStr = Field(alias="mixSeed")
    nonce: HexStr = Field(alias="nonce")

    @field_validator(
        "parent_hash",
        "state_root",
        "txs_root",
        "receipts_root",
        "proofs_root",
        "da_root",
        "alg_policy_root",
        "poies_policy_root",
    )
    @classmethod
    def _hashes_ok(cls, v: str) -> HashHex32:
        return ensure_hash32_hex(v)

    @field_validator("mix_seed", "nonce")
    @classmethod
    def _hex_ok(cls, v: str) -> HexStr:
        return ensure_hex(v)


# -----------------------------------------------------------------------------
# Transactions & Receipts
# -----------------------------------------------------------------------------


class AccessListItem(BaseModel):
    """
    Optional access-list element for deterministic access predeclaration.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)
    address: Address
    storage_keys: List[HashHex32] = Field(alias="storageKeys")

    @field_validator("address")
    @classmethod
    def _addr_ok(cls, v: str) -> Address:
        if not is_address(v):
            raise ValueError("invalid bech32m address")
        return Address(v)

    @field_validator("storage_keys")
    @classmethod
    def _sk_hashes_ok(cls, v: Sequence[str]) -> List[HashHex32]:
        return [ensure_hash32_hex(h) for h in v]


class TxView(BaseModel):
    """
    Transaction view suitable for REST/JSON-RPC responses.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        frozen=True,
        json_schema_extra={
            "examples": [
                {
                    "hash": "0x" + "aa" * 32,
                    "chainId": 1,
                    "from": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqz3y9k8",
                    "to": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqm2k8sy",
                    "nonce": 7,
                    "gasLimit": 120000,
                    "gasPrice": 1,
                    "value": "0x00",
                    "type": "transfer",
                    "data": "0x",
                    "accessList": [],
                    "signature": {"algId": 0x31, "sig": "0x" + "bb" * 96},  # Dilithium3
                }
            ]
        },
    )

    # identity & meta
    hash: HashHex32 = Field(alias="hash")
    chain_id: int = Field(alias="chainId")

    # sender/recipient
    from_addr: Address = Field(alias="from")
    to_addr: Optional[Address] = Field(default=None, alias="to")  # None for deploy

    # economics & execution
    nonce: int = Field(alias="nonce")
    gas_limit: int = Field(alias="gasLimit")
    gas_price: int = Field(
        alias="gasPrice"
    )  # simple model; base/tip split lives server-side
    value: HexStr = Field(default="0x00", alias="value")

    # kind & payload
    type: Literal["transfer", "deploy", "call"] = Field(alias="type")
    data: HexStr = Field(default="0x", alias="data")
    access_list: Optional[List[AccessListItem]] = Field(
        default=None, alias="accessList"
    )

    # signature (PQ)
    alg_id: int = Field(alias="algId")
    sig: HexStr = Field(alias="sig")

    # inclusion (optional when pending)
    block_hash: Optional[HashHex32] = Field(default=None, alias="blockHash")
    block_number: Optional[int] = Field(default=None, alias="blockNumber")
    tx_index: Optional[int] = Field(default=None, alias="transactionIndex")

    @field_validator("hash", "block_hash")
    @classmethod
    def _hash_ok(cls, v: Optional[str]) -> Optional[HashHex32]:
        if v is None:
            return None
        return ensure_hash32_hex(v)

    @field_validator("from_addr", "to_addr")
    @classmethod
    def _addr_ok(cls, v: Optional[str]) -> Optional[Address]:
        if v is None:
            return None
        if not is_address(v):
            raise ValueError("invalid bech32m address")
        return Address(v)

    @field_validator("data", "sig", "value")
    @classmethod
    def _hex_ok(cls, v: str) -> HexStr:
        return ensure_hex(v)


class ReceiptView(BaseModel):
    """
    Transaction receipt (post-execution summary).
    """

    model_config = ConfigDict(
        populate_by_name=True,
        frozen=True,
        json_schema_extra={
            "examples": [
                {
                    "status": "SUCCESS",
                    "gasUsed": 52311,
                    "cumulativeGasUsed": 52311,
                    "logs": [],
                    "txHash": "0x" + "aa" * 32,
                    "transactionIndex": 0,
                    "blockHash": "0x" + "bb" * 32,
                    "blockNumber": 1024,
                    "contractAddress": None,
                }
            ]
        },
    )

    status: Literal["SUCCESS", "REVERT", "OOG"] = Field(alias="status")
    gas_used: int = Field(alias="gasUsed")
    cumulative_gas_used: int = Field(alias="cumulativeGasUsed")
    logs: List[LogView] = Field(default_factory=list, alias="logs")

    tx_hash: HashHex32 = Field(alias="txHash")
    tx_index: int = Field(alias="transactionIndex")
    block_hash: HashHex32 = Field(alias="blockHash")
    block_number: int = Field(alias="blockNumber")
    contract_address: Optional[Address] = Field(default=None, alias="contractAddress")

    @field_validator("tx_hash", "block_hash")
    @classmethod
    def _hash_ok(cls, v: str) -> HashHex32:
        return ensure_hash32_hex(v)

    @field_validator("contract_address")
    @classmethod
    def _addr_ok(cls, v: Optional[str]) -> Optional[Address]:
        if v is None:
            return None
        if not is_address(v):
            raise ValueError("invalid bech32m address")
        return Address(v)


# -----------------------------------------------------------------------------
# Blocks
# -----------------------------------------------------------------------------


class BlockView(BaseModel):
    """
    Block view. `transactions` can be either full TxView objects *or* just tx-hash strings,
    depending on RPC method parameters.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    hash: HashHex32 = Field(alias="hash")
    size_bytes: int = Field(alias="sizeBytes")
    header: HeaderView = Field(alias="header")

    # Either a list of TxView or a list of tx-hash strings (HashHex32).
    transactions: List[Union[TxView, HashHex32]] = Field(alias="transactions")

    receipts: Optional[List[ReceiptView]] = Field(default=None, alias="receipts")

    @field_validator("hash")
    @classmethod
    def _hash_ok(cls, v: str) -> HashHex32:
        return ensure_hash32_hex(v)

    @field_validator("transactions")
    @classmethod
    def _txs_ok(cls, v: Sequence[Union[TxView, str]]) -> List[Union[TxView, HashHex32]]:
        """
        Enforce homogeneity: either all strings (hashes) or all objects.
        Also validate hex for the hash case.
        """
        v_list = list(v)
        if not v_list:
            return []
        all_str = all(isinstance(t, str) for t in v_list)
        all_obj = all(isinstance(t, TxView) for t in v_list)
        if not (all_str or all_obj):
            raise ValueError(
                "transactions must be either all hashes or all TxView objects"
            )
        if all_str:
            return [ensure_hash32_hex(t) for t in v_list]  # type: ignore[return-value]
        return v_list  # type: ignore[return-value]


# -----------------------------------------------------------------------------
# Helper: decoding-friendly aliases to map from core objects (optional)
# -----------------------------------------------------------------------------


def address_hrp(addr: Address) -> str:
    """
    Extract HRP from a bech32m address (useful for UI/debug).
    """
    hrp, _, _ = decode_address(addr)
    return hrp


__all__ = [
    # JSON-RPC
    "JsonRpcRequest",
    "JsonRpcError",
    "JsonRpcResponse",
    # Views
    "Head",
    "HeaderView",
    "BlockView",
    "TxView",
    "ReceiptView",
    "LogView",
    "AccessListItem",
    # Utils
    "address_hrp",
]
