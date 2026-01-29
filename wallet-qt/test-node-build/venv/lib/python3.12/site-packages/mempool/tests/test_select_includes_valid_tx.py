from __future__ import annotations

from core.types.tx import PqSignature, Tx, TxKind, TxTransfer, UnsignedTx
from core.utils.hash import sha3_256
from mempool.select import PendingTxEntry, select_for_block
from pq.py.address import decode_address
from pq.py.keygen import keygen_sig
from pq.py.registry import ALG_ID
from rpc.methods import tx as tx_methods


class _StateStub:
    def __init__(self, balance: int, nonce: int) -> None:
        self._balance = int(balance)
        self._nonce = int(nonce)

    def get_balance(self, _addr: bytes) -> int:
        return self._balance

    def get_nonce(self, _addr: bytes) -> int:
        return self._nonce


def test_select_for_block_includes_valid_tx() -> None:
    sender_kp = keygen_sig("dilithium3")
    sender_record = decode_address(sender_kp.address)
    sender_digest = bytes(sender_record.digest) if isinstance(sender_record.digest, list) else sender_record.digest
    sender_bytes = sender_digest[:32].ljust(32, b"\x00")
    recipient_bytes = b"\x11" * 32

    unsigned = UnsignedTx(
        chain_id=1,
        nonce=0,
        gas_price=1,
        gas_limit=21000,
        sender=sender_bytes,
        kind=TxKind.TRANSFER,
        payload=TxTransfer(to=recipient_bytes, amount=1_000_000_000, data=b""),
        access_list=(),
    )
    sig = PqSignature(
        alg_id=ALG_ID["dilithium3"],
        pubkey=sender_kp.public_key,
        sig=b"\x00" * 64,
    )
    tx = Tx(unsigned=unsigned, sigs=(sig,))
    raw = tx.to_cbor()
    tx_hash_hex = "0x" + sha3_256(raw).hex()

    entry = PendingTxEntry(hash_hex=tx_hash_hex, raw=raw, tx=None)
    state_db = _StateStub(balance=2_000_000_000, nonce=0)

    selection = select_for_block(
        head_state={"chain_id": 1},
        limits={"max_gas": 1_000_000, "max_bytes": 1_000_000, "max_txs": 10},
        pending=[entry],
        decode=tx_methods._decode_tx,
        state_db=state_db,
        policy={"min_gas_price": 0},
        tx_index=None,
        signature_validator=None,
    )

    assert len(selection.selected) == 1
