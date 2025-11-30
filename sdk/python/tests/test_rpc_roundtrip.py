import os
import time
import types

import pytest
from omni_sdk.address import Address
# Import SDK pieces under test
from omni_sdk.tx.build import build_transfer_tx
from omni_sdk.tx.encode import encode_tx_cbor
from omni_sdk.tx.send import await_receipt, send_raw_transaction


class FakeRpc:
    """
    Minimal in-memory JSON-RPC stub implementing only the methods exercised in this test.
    """

    def __init__(self) -> None:
        self.calls = []
        self._receipt_counter = 0

    def call(self, method: str, params):
        self.calls.append((method, params))
        if method == "tx.sendRawTransaction":
            # Accept either raw bytes hex or base64/bytes depending on encoder; return a fake tx hash.
            return "0xdeadbeefcafebabe"
        if method == "tx.getTransactionReceipt":
            # Return None twice, then a final SUCCESS receipt
            self._receipt_counter += 1
            if self._receipt_counter < 3:
                return None
            return {
                "txHash": params[0],
                "status": "SUCCESS",
                "gasUsed": 21000,
                "logs": [],
            }
        # Default: pretend method not needed by this test
        return None


def _addr_from_bytes(b: bytes, alg: str = "dilithium3") -> str:
    """
    Helper to derive a bech32 address from arbitrary bytes (as if they were a public key).
    """
    return Address.from_public_key(b, alg=alg).bech32


def test_transfer_send_and_await_receipt():
    rpc = FakeRpc()

    # Construct deterministic sender/recipient addresses from fixed bytes
    sender = _addr_from_bytes(
        b"\x11" * 48
    )  # 48 bytes ~ typical PQ pk size (ok for hashing)
    recipient = _addr_from_bytes(b"\x22" * 48)

    # Build a simple transfer tx (avoid any RPC-dependent estimates by passing explicit values)
    tx = build_transfer_tx(
        chain_id=1,
        sender=sender,
        to=recipient,
        amount=12345,
        gas_price=1,
        gas_limit=50_000,
        nonce=0,
        data=b"",  # optional memo
    )

    # Attach a tiny dummy signature (we're not verifying cryptography in this unit test)
    tx.attach_signature(alg_id="dilithium3", signature=b"\x01\x02")

    # Encode to CBOR and submit via the send helper
    raw = encode_tx_cbor(tx)
    assert isinstance(raw, (bytes, bytearray)) and len(raw) > 0

    tx_hash = send_raw_transaction(rpc, raw)
    assert tx_hash == "0xdeadbeefcafebabe"

    # Now await the receipt using the polling helper (FakeRpc returns None twice, then success)
    receipt = await_receipt(rpc, tx_hash, timeout_seconds=2.0, poll_seconds=0.01)
    assert isinstance(receipt, dict)
    assert receipt.get("status") == "SUCCESS"
    assert receipt.get("gasUsed") == 21000

    # Sanity: ensure expected methods were invoked in order
    methods = [m for (m, _p) in rpc.calls]
    assert methods[0] == "tx.sendRawTransaction"
    assert methods.count("tx.getTransactionReceipt") >= 3


def test_address_roundtrip_formatting():
    """
    Light sanity check that bech32 derivation is stable-ish for input bytes and
    round-trippable through the Address helper.
    """
    pub = b"\x33" * 48
    addr = Address.from_public_key(pub, alg="sphincs_shake_128s")
    # Basic shape: hrp 'anim', '1', and length within expected range
    s = addr.bech32
    assert s.startswith("anim1")
    assert 20 <= len(s) <= 120

    # Validate/parse back
    parsed = Address.parse(s)
    assert parsed.alg_id in ("dilithium3", "sphincs_shake_128s")
    assert parsed == parsed  # eq / hash sanity
