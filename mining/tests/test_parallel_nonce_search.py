from __future__ import annotations

from mining.parallel_nonce_search import (
    iter_stride,
    parallel_nonce_search,
    resolve_worker_count,
)


def toy_check_modulo(nonce: int, target: int) -> tuple[bool, int | None]:
    return (nonce % target == 0, nonce if nonce % target == 0 else None)


def toy_check_exact(nonce: int, target_nonce: int) -> tuple[bool, int | None]:
    return (nonce == target_nonce, nonce if nonce == target_nonce else None)


def test_iter_stride_partitions_nonce_space():
    nonces = {
        0: list(iter_stride(0, 10, 0, 3)),
        1: list(iter_stride(0, 10, 1, 3)),
        2: list(iter_stride(0, 10, 2, 3)),
    }
    assert nonces[0] == [0, 3, 6, 9]
    assert nonces[1] == [1, 4, 7]
    assert nonces[2] == [2, 5, 8]


def test_parallel_search_finds_same_nonce_as_single_worker():
    single = parallel_nonce_search(toy_check_modulo, (17,), 0, 200, workers=1)
    parallel = parallel_nonce_search(toy_check_modulo, (17,), 0, 200, workers=4)

    assert single is not None
    assert parallel is not None
    assert single.nonce == parallel.nonce


def test_parallel_search_early_stop_and_worker_id():
    result = parallel_nonce_search(toy_check_exact, (7,), 0, 50, workers=4)

    assert result is not None
    assert result.nonce == 7
    assert result.worker_id == 7 % 4


def test_parallel_search_restarts_on_worker_crash():
    result = parallel_nonce_search(
        toy_check_exact,
        (5,),
        0,
        50,
        workers=2,
        max_restarts=2,
        crash_after_by_worker={0: 2},
    )

    assert result is not None
    assert result.nonce == 5
    assert result.restarts >= 1


def test_resolve_worker_count_clamps_and_autos():
    assert resolve_worker_count(0) >= 1
    assert resolve_worker_count(9999) <= 256
