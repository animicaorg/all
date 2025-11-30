from core.chain.head import read_head
from core.db import open_kv
from core.db.block_db import BlockDB


def test_read_head_prefers_canonical_tip_when_head_stale() -> None:
    kv = open_kv("sqlite:///:memory:")
    bdb = BlockDB(kv)

    # Canonical index contains newer heights than the stored head pointer.
    bdb.set_canonical(0, b"\x00" * 32)
    bdb.set_canonical(1, b"\x01" * 32)
    bdb.set_canonical(2, b"\x02" * 32)
    bdb.set_canonical(3, b"\x03" * 32)

    # Head pointer was not updated after import.
    bdb.set_head(0, b"\x00" * 32)

    height, hh = read_head(bdb)
    assert height == 3
    assert hh == b"\x03" * 32
