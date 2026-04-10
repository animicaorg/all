import itertools
import os
import random

import pytest

import randomness.commit_reveal.aggregate as agg_mod  # type: ignore
# Expected API (best-effort across plausible names).
# The aggregator module should expose a function that combines a list of digests
# (32B each) into a single 32B beacon candidate using a hash–xor-style fold.
from randomness.utils.hash import \
    sha3_256  # deterministic, domain-separated in module


def _find_aggregate_func():
    """
    Try a few common symbols; return a callable that accepts List[bytes] -> bytes.
    If only a pairwise combiner exists, adapt it.
    """
    candidates = [
        "aggregate_digests",
        "aggregate_reveals",  # many impls just hash inputs internally; passing digests still OK
        "aggregate",
        "fold_hash_xor",
        "fold_xor",
    ]
    for name in candidates:
        f = getattr(agg_mod, name, None)
        if callable(f):
            return f

    # Try to build from a pairwise combiner
    for name in ("combine_pair", "combine", "mix_pair"):
        f = getattr(agg_mod, name, None)
        if callable(f):

            def fold(digests: list[bytes]) -> bytes:
                if not digests:
                    # Conventional identity: 32 zero bytes
                    return b"\x00" * 32
                acc = digests[0]
                for d in digests[1:]:
                    acc = f(acc, d)
                return acc

            return fold

    pytest.skip(
        "No compatible aggregate function found in randomness.commit_reveal.aggregate"
    )


AGG = _find_aggregate_func()


def digest(i: int) -> bytes:
    # Deterministic pseudo-reveals → 32B digests
    return sha3_256(f"animica.reveal.{i}".encode("utf-8"))


def digest_set(start: int, n: int) -> list[bytes]:
    return [digest(i) for i in range(start, start + n)]


def is_32_or_64(b: bytes) -> bool:
    return isinstance(b, (bytes, bytearray)) and len(b) in (32, 48, 64) or len(b) == 32


def test_permutation_invariance():
    vals = digest_set(0, 8)
    a = AGG(vals)
    b = AGG(list(reversed(vals)))
    c = AGG(vals[2:] + vals[:2])  # rotation
    assert is_32_or_64(a)
    assert a == b == c, "Aggregation must be invariant under permutation/shuffle"


def test_different_sets_produce_different_outputs():
    # Two disjoint sets should almost certainly yield different aggregates.
    a = AGG(digest_set(0, 16))
    b = AGG(digest_set(1000, 16))
    assert a != b, "Distinct reveal-sets should not collide under aggregate combiner"


def test_additional_reveal_changes_output():
    base = digest_set(10, 12)
    out1 = AGG(base)
    out2 = AGG(base + [digest(999)])
    assert (
        out1 != out2
    ), "Including an additional valid reveal must change the aggregate"


def test_chunking_associativity_like_property_when_exposed():
    """
    If the implementation exposes a low-level XOR fold over per-item digests,
    then chunking should be invariant:

      agg(A + B) == agg(A) XOR agg(B)     (possibly with the same 'finalize' function)

    Because some implementations apply a final hash after XOR, we relax the check:
    - We compute agg(A+B) and compare against agg(shuffle(A+B)) (always true).
    - If a helper 'xor_bytes' is exported, verify XOR-decomposability.
    """
    vals = digest_set(200, 15)
    out_all = AGG(vals)
    out_shuffled = AGG(sorted(vals))  # lexicographic order ≈ another permutation
    assert out_all == out_shuffled

    xor_fn = getattr(agg_mod, "xor_bytes", None)
    if callable(xor_fn):
        A, B = vals[:7], vals[7:]
        out_A = AGG(A)
        out_B = AGG(B)
        combined = xor_fn(out_A, out_B)
        # If finalize-after-XOR is used internally, there may be a finalize() exported.
        finalize = getattr(agg_mod, "finalize", None)
        if callable(finalize):
            combined = finalize(combined)
        assert (
            combined == out_all
        ), "Aggregator should be decomposable via XOR when helpers are present"
    else:
        pytest.skip("xor_bytes helper not exposed; associativity-like check skipped")


def test_duplicate_handling_idempotence_when_deduper_present():
    """
    Some implementations deduplicate identical reveals (by commitment/identity).
    If a 'aggregate_digests_dedup' (or similar) is present, verify idempotence under duplicates.
    Otherwise, skip (plain XOR would flip with duplicates).
    """
    dedup = getattr(agg_mod, "aggregate_digests_dedup", None)
    if not callable(dedup):
        pytest.skip(
            "No dedup aggregator exposed; skipping idempotence-under-duplicates test"
        )

    vals = digest_set(42, 6)
    out1 = dedup(vals)
    out2 = dedup(vals + [vals[0], vals[2], vals[0]])  # inject duplicates
    assert (
        out1 == out2
    ), "Dedup-enabled aggregator must be idempotent under duplicate reveals"


def test_empty_input_convention():
    """
    Define/verify behavior for empty input: common conventions are zero-bytes or a fixed domain tag hash.
    Accept either, but enforce determinism and stability across calls.
    """
    try:
        a = AGG([])
        b = AGG([])
        assert a == b
        assert is_32_or_64(a)
    except Exception:
        # Also acceptable: raising a well-typed error to forbid empty rounds (policy-level).
        pytest.skip(
            "Aggregator forbids empty input (policy-level); skipping convention check"
        )


def test_bit_diffusion_sanity():
    """
    Sanity check: changing one digest should change ~half the bits after aggregation when a
    hash is part of the combiner; for a pure XOR fold, the hamming distance equals the changed digest's distance.
    We only assert non-trivial change.
    """
    base = digest_set(300, 20)
    out1 = AGG(base)

    # Flip one underlying digest to a very different value (by using a far i)
    mutated = base.copy()
    mutated[7] = digest(999999)
    out2 = AGG(mutated)

    # Hamming distance > 0
    x = bytes(x ^ y for x, y in zip(out1, out2))
    changed_bits = sum(bin(b).count("1") for b in x)
    assert changed_bits > 0, "Aggregate should change when a single digest changes"
