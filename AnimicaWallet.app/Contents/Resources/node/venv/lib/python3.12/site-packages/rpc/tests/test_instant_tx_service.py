from pathlib import Path

from rpc.instant_tx import InstantTxService


def test_emit_and_finalize_instant_receipt(tmp_path: Path):
    svc = InstantTxService(data_root=tmp_path, chain_id=1337, ttl_s=3600)
    txid = "0x" + "11" * 32
    anchor = "0x" + "22" * 32

    rec = svc.emit_local(txid=txid, anchor_hash=anchor, timestamp=2_200_000_000)
    assert rec["instant_confirmed"] is True
    assert rec["finalized_in_pow"] is False

    got = svc.get_receipt(txid)
    assert got is not None
    assert got["instant_confirmed"] is True
    assert got["anchor_hash"] == anchor

    svc.mark_finalized([txid])
    got2 = svc.get_receipt(txid)
    assert got2 is not None
    assert got2["finalized_in_pow"] is True
    assert got2["reason"] == "included_in_pow"


def test_prunes_expired_non_finalized(tmp_path: Path):
    svc = InstantTxService(data_root=tmp_path, chain_id=1337, ttl_s=10)
    txid = "0x" + "33" * 32
    anchor = "0x" + "44" * 32
    svc.emit_local(txid=txid, anchor_hash=anchor, timestamp=100)

    # trigger prune at later timestamp
    svc.emit_local(txid="0x" + "55" * 32, anchor_hash=anchor, timestamp=200)
    assert svc.get_receipt(txid) is None
