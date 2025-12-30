from core.types.tx import Tx

from rpc.mempool_service import MempoolService


def _build_transfer_tx() -> tuple[Tx, bytes]:
    sender = b"\x11" * 32
    to = b"\x22" * 32
    tx = Tx.transfer(
        chain_id=1,
        nonce=0,
        gas_price=1,
        gas_limit=21_000,
        sender=sender,
        to=to,
        amount=1,
    )
    return tx, tx.to_cbor()


def test_mempool_persists_and_restores(tmp_path):
    tx, raw = _build_transfer_tx()
    service = MempoolService.create(
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        data_dir=str(tmp_path),
    )
    tx_hash = service.submit(tx=tx, raw=raw, local=True)
    persist_path = tmp_path / "mempool" / "pending.jsonl"
    assert persist_path.exists()

    restored = MempoolService.create(
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        data_dir=str(tmp_path),
    )
    assert restored.has_hash(tx_hash)
    assert restored.get_raw(tx_hash) == raw
