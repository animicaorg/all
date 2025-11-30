import os
import random
from typing import List, Optional, Tuple

import pytest

# test utilities (provided in native/tests_py/__init__.py)
from . import SKIP_HEAVY, TEST_SEED, is_ci, native  # type: ignore

# --------- Helpers ---------


def _rng() -> random.Random:
    return random.Random(TEST_SEED)


def _rand_bytes(n: int, r: Optional[random.Random] = None) -> bytes:
    r = r or _rng()
    return bytes(r.getrandbits(8) for _ in range(n))


def _join_data(shards: List[bytes], k: int, original_len: int) -> bytes:
    """Reassemble original data from first k shards and trim to original length."""
    # Some implementations may return memoryview/bytearray - normalize to bytes
    b = b"".join(bytes(s) for s in shards[:k])
    return b[:original_len]


def _lose(shards: List[bytes], loss_idx: List[int]) -> List[Optional[bytes]]:
    s: List[Optional[bytes]] = list(shards)
    for i in loss_idx:
        s[i] = None
    return s


def _rs_encode(data: bytes, k: int, m: int) -> List[bytes]:
    # Adapter/wrapper in case implementations return tuples/arrays
    out = native.rs_encode(data, k, m)
    if isinstance(out, (list, tuple)):
        return [bytes(x) for x in out]
    raise TypeError("native.rs_encode returned unexpected type")


def _rs_reconstruct(shards_opt: List[Optional[bytes]]) -> List[bytes]:
    """
    Adapter for different reconstruct contracts:
    - return List[bytes]
    - or mutate in-place and return True/False/None
    """
    s: List[Optional[bytes]] = list(shards_opt)
    result = native.rs_reconstruct(s)
    if isinstance(result, list):
        return [bytes(x) for x in result]  # already repaired
    # In-place: ensure no None remains
    repaired = [bytes(x) for x in s]  # type: ignore[arg-type]
    if any(x is None for x in s):
        raise AssertionError("reconstruct reported success but shards still missing")
    return repaired


# --------- Parameter spaces ---------

BASIC_KM = [(4, 2), (6, 3), (8, 4)]
HEAVY_KM = [(10, 10), (12, 4), (16, 8)]

DATA_LENS = [0, 1, 7, 64, 255, 256, 1000, 4096, 8192 + 13]
if not (SKIP_HEAVY or is_ci()):
    DATA_LENS += [131_071, 262_144 + 17]  # ~128KB and ~256KB + tail


# --------- Tests ---------


@pytest.mark.parametrize("k,m", BASIC_KM)
@pytest.mark.parametrize("nbytes", DATA_LENS)
def test_encode_deterministic(k: int, m: int, nbytes: int):
    data = _rand_bytes(nbytes)
    s1 = _rs_encode(data, k, m)
    s2 = _rs_encode(bytes(data), k, m)
    assert len(s1) == len(s2) == k + m
    # shard sizes are implementation-defined (padding), but bytes must match
    for a, b in zip(s1, s2):
        assert bytes(a) == bytes(b)
    # Reassembly of first k shards recovers the original data (trim padding)
    assert _join_data(s1, k, len(data)) == data


@pytest.mark.parametrize("k,m", BASIC_KM + ([] if SKIP_HEAVY or is_ci() else HEAVY_KM))
@pytest.mark.parametrize("losses", [0, 1, 2, "max"])
@pytest.mark.parametrize("nbytes", [0, 1, 137, 4097])
def test_encode_reconstruct_roundtrip(k: int, m: int, losses, nbytes: int):
    r = _rng()
    data = _rand_bytes(nbytes, r)
    shards = _rs_encode(data, k, m)
    total = k + m

    max_losses = m
    loss_cnt = max_losses if losses == "max" else int(losses)
    assert loss_cnt <= m

    # choose distinct indices to drop across data+parity; ensure we don't drop all shards
    candidates = list(range(total))
    r.shuffle(candidates)
    drop = sorted(candidates[:loss_cnt])
    damaged = _lose(shards, drop)

    repaired = _rs_reconstruct(damaged)
    assert len(repaired) == total
    recovered = _join_data(repaired, k, len(data))
    assert (
        recovered == data
    ), f"recovered data mismatch (k={k}, m={m}, losses={loss_cnt}, drop={drop})"


@pytest.mark.parametrize("k,m", [(4, 2), (8, 4)])
@pytest.mark.parametrize("nbytes", [0, 73, 4096])
def test_reconstruct_fails_beyond_parity(k: int, m: int, nbytes: int):
    r = _rng()
    data = _rand_bytes(nbytes, r)
    shards = _rs_encode(data, k, m)
    total = k + m

    # Drop m+1 shards (beyond correction capability)
    drop = list(range(m + 1))
    damaged = _lose(shards, drop)

    with pytest.raises(Exception):
        _ = _rs_reconstruct(damaged)


@pytest.mark.parametrize("k,m", [(4, 0), (6, 0)])
@pytest.mark.parametrize("nbytes", [0, 17, 1024])
def test_zero_parity_behaves_as_chunking(k: int, m: int, nbytes: int):
    data = _rand_bytes(nbytes)
    shards = _rs_encode(data, k, m)
    assert len(shards) == k  # only data shards
    assert _join_data(shards, k, len(data)) == data


@pytest.mark.parametrize("k,m", [(0, 2), (-1, 2), (4, -1)])
def test_invalid_params_raise(k: int, m: int):
    with pytest.raises(Exception):
        _ = native.rs_encode(b"hello", k, m)


@pytest.mark.parametrize("factory", [bytes, bytearray, memoryview])
def test_input_bytes_like(factory):
    data = factory(b"The quick brown fox jumps over the lazy dog")
    shards = _rs_encode(bytes(data), 4, 2)
    reassembled = _join_data(shards, 4, len(data))
    assert reassembled == bytes(data)


@pytest.mark.skipif(SKIP_HEAVY or is_ci(), reason="heavy randomized soak")
def test_randomized_many_patterns():
    r = _rng()
    for _ in range(200):
        k = r.choice([3, 4, 6, 8, 10])
        m = r.choice([1, 2, 3, 4, 6])
        n = r.randrange(0, 50_000)
        data = _rand_bytes(n, r)
        shards = _rs_encode(data, k, m)
        total = k + m
        # Random loss count up to m
        loss_cnt = r.randrange(0, m + 1)
        drop = sorted(r.sample(range(total), loss_cnt))
        repaired = _rs_reconstruct(_lose(shards, drop))
        assert (
            _join_data(repaired, k, len(data)) == data
        ), f"failed recovery k={k} m={m} n={n} drop={drop}"
