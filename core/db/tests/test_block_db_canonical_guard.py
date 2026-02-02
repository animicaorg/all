import pytest

from core.db.block_db import BlockDB
from core.db.sqlite import SQLiteKV


def test_set_canonical_rejects_overwrite() -> None:
    kv = SQLiteKV(":memory:")
    db = BlockDB(kv)
    h1 = b"\x01" * 32
    h2 = b"\x02" * 32

    db.set_canonical(1, h1)
    with pytest.raises(ValueError):
        db.set_canonical(1, h2)


def test_set_canonical_allows_explicit_overwrite() -> None:
    kv = SQLiteKV(":memory:")
    db = BlockDB(kv)
    h1 = b"\x01" * 32
    h2 = b"\x02" * 32

    db.set_canonical(1, h1)
    db.set_canonical(1, h2, allow_overwrite=True)
    assert db.get_canonical_hash(1) == h2
