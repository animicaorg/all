from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from web3 import Web3

from .enums import BridgeDirection


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SignatureChallenge:
    challenge_id: str
    nonce: str
    typed_data: dict[str, Any]
    text_fallback: str
    challenge_hash: str


def build_order_typed_data(
    *,
    order_id: str,
    direction: BridgeDirection,
    source_chain: str,
    destination_chain: str,
    source_address: str,
    destination_address: str,
    exact_amount: int,
    chain_id: int,
    verifying_contract: str,
    nonce: str,
    expires_at: datetime,
) -> dict[str, Any]:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "BridgeOrder": [
                {"name": "orderId", "type": "string"},
                {"name": "direction", "type": "string"},
                {"name": "sourceChain", "type": "string"},
                {"name": "destinationChain", "type": "string"},
                {"name": "sourceAddress", "type": "string"},
                {"name": "destinationAddress", "type": "string"},
                {"name": "exactAmount", "type": "uint256"},
                {"name": "nonce", "type": "string"},
                {"name": "expiresAt", "type": "string"},
            ],
        },
        "domain": {
            "name": "Animica BANM Bridge",
            "version": "1",
            "chainId": chain_id,
            "verifyingContract": Web3.to_checksum_address(verifying_contract),
        },
        "primaryType": "BridgeOrder",
        "message": {
            "orderId": order_id,
            "direction": direction.value,
            "sourceChain": source_chain,
            "destinationChain": destination_chain,
            "sourceAddress": source_address,
            "destinationAddress": destination_address,
            "exactAmount": int(exact_amount),
            "nonce": nonce,
            "expiresAt": _iso(expires_at),
        },
    }


def build_signature_challenge(
    *,
    order_id: str,
    direction: BridgeDirection,
    source_chain: str,
    destination_chain: str,
    source_address: str,
    destination_address: str,
    exact_amount: int,
    chain_id: int,
    verifying_contract: str,
    expires_at: datetime,
) -> SignatureChallenge:
    nonce = secrets.token_hex(16)
    typed_data = build_order_typed_data(
        order_id=order_id,
        direction=direction,
        source_chain=source_chain,
        destination_chain=destination_chain,
        source_address=source_address,
        destination_address=destination_address,
        exact_amount=exact_amount,
        chain_id=chain_id,
        verifying_contract=verifying_contract,
        nonce=nonce,
        expires_at=expires_at,
    )
    text_fallback = (
        "Animica BANM Bridge Order Binding\n"
        f"Order: {order_id}\n"
        f"Direction: {direction.value}\n"
        f"Source: {source_chain}:{source_address}\n"
        f"Destination: {destination_chain}:{destination_address}\n"
        f"Exact Amount: {exact_amount}\n"
        f"Nonce: {nonce}\n"
        f"ExpiresAt: {_iso(expires_at)}"
    )
    digest = hashlib.sha256(
        json.dumps(
            {"typedData": typed_data, "fallback": text_fallback},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return SignatureChallenge(
        challenge_id=f"sig_{order_id}",
        nonce=nonce,
        typed_data=typed_data,
        text_fallback=text_fallback,
        challenge_hash=f"sha256:{digest}",
    )


def verify_order_signature(
    *,
    signature: str,
    expected_signer: str,
    typed_data: dict[str, Any],
    fallback_message: str,
    signature_type: str = "EIP712",
) -> str:
    normalized_expected = Web3.to_checksum_address(expected_signer)
    kind = signature_type.upper().strip()
    if kind == "EIP712":
        signable = encode_typed_data(full_message=typed_data)
    else:
        signable = encode_defunct(text=fallback_message)
    recovered = Account.recover_message(signable, signature=signature)
    recovered_checksum = Web3.to_checksum_address(recovered)
    if recovered_checksum != normalized_expected:
        raise ValueError(
            f"signature signer mismatch: expected={normalized_expected} recovered={recovered_checksum}"
        )
    return recovered_checksum

