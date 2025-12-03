import hashlib

import pytest

from randomness.adapters import core_db
from randomness.adapters.execution import ExecutionRandomness


def _beacon_bytes(round_id: int, salt: str = "") -> bytes:
    """Derive a deterministic 32-byte beacon payload for tests."""
    msg = f"round-{round_id}-{salt}".encode("utf-8")
    return hashlib.sha3_256(msg).digest()


def _record_block(db: core_db.RandomnessCoreDB, height: int, round_id: int, *, salt: str = "") -> bytes:
    beacon = _beacon_bytes(round_id, salt)
    db.link_round_to_block(
        height=height,
        round_id=round_id,
        beacon=beacon,
        vdf_proof=None,
        commit_count=2,
        reveal_count=1,
        vdf_verified=True,
    )
    return beacon


def test_beacon_output_changes_with_blocks():
    kv = core_db._DictKV()
    db = core_db.RandomnessCoreDB(kv)
    exec_view = ExecutionRandomness(pointer_source=db)

    first_beacon = _record_block(db, height=0, round_id=0)
    second_beacon = _record_block(db, height=1, round_id=1)

    digest0 = exec_view.beacon_digest_at_height(0)
    digest1 = exec_view.beacon_digest_at_height(1)

    assert digest0 == hashlib.sha3_256(first_beacon).digest()
    assert digest1 == hashlib.sha3_256(second_beacon).digest()
    assert digest0 != digest1, "Beacon digest should evolve as new blocks finalize"


def test_beacon_output_is_deterministic_for_block_sequence():
    kv = core_db._DictKV()
    db = core_db.RandomnessCoreDB(kv)
    exec_view = ExecutionRandomness(pointer_source=db)

    for height, rnd in enumerate(range(3)):
        _record_block(db, height=height, round_id=rnd)

    digest_sequence = [exec_view.beacon_digest_at_height(h) for h in range(3)]

    # Simulate restart by cloning the persisted KV contents into a fresh adapter
    kv2 = core_db._DictKV()
    kv2._d.update(kv._d)
    db_restarted = core_db.RandomnessCoreDB(kv2)
    exec_view_restarted = ExecutionRandomness(pointer_source=db_restarted)
    restarted_sequence = [exec_view_restarted.beacon_digest_at_height(h) for h in range(3)]

    assert digest_sequence == restarted_sequence


def test_beacon_pointer_rejects_single_block_override():
    kv = core_db._DictKV()
    db = core_db.RandomnessCoreDB(kv)
    exec_view = ExecutionRandomness(pointer_source=db)

    original_beacon = _record_block(db, height=5, round_id=2, salt="honest")
    honest_digest = exec_view.beacon_digest_at_height(5)

    with pytest.raises(ValueError):
        _record_block(db, height=5, round_id=2, salt="malicious")

    assert exec_view.beacon_digest_at_height(5) == honest_digest
    assert honest_digest == hashlib.sha3_256(original_beacon).digest()
