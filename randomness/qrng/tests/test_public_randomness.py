"""Tests for aggregation, the consumable quantum beacon, and verifiable primitives."""

from __future__ import annotations

import os

from randomness.qrng import public, aggregate
from randomness.qrng import providers, hsm_tpm, contribution as quw
from randomness.qrng.service import QuantumWorkService


# --- aggregation ---

def test_aggregate_order_independent_and_contributor_bound():
    a = (b"\x01" * 32, True, "alice")
    b = (b"\x02" * 32, True, "bob")
    r1 = aggregate.aggregate_entropy([a, b])
    r2 = aggregate.aggregate_entropy([b, a])
    assert r1.value == r2.value           # order independent
    assert r1.n_total == 2 and r1.n_attested == 2 and r1.attested
    # identical bytes from two different addresses must NOT cancel under XOR
    same = aggregate.aggregate_entropy([(b"\x09" * 32, True, "x"), (b"\x09" * 32, True, "y")])
    assert same.value != b"\x00" * 32


def test_aggregate_requires_attested_when_present():
    items = [(b"\xaa" * 32, True, "att"), (b"\xbb" * 32, False, "soft")]
    r = aggregate.aggregate_entropy(items, require_attested=True)
    assert r.n_total == 1 and r.contributors == ("att",)


# --- beacon ---

def test_beacon_build_and_verify():
    agg = aggregate.aggregate_entropy([(os.urandom(32), True, "a")])
    bc = public.build_quantum_beacon(5, b"\x00" * 32, agg)
    assert bc.attested and bc.round_id == 5
    assert public.verify_quantum_beacon(bc, aggregate_value_hex=agg.commitment)
    # wrong aggregate -> fails
    assert not public.verify_quantum_beacon(bc, aggregate_value_hex=(os.urandom(32)).hex())


# --- primitives: deterministic + verifiable ---

def test_primitives_deterministic_and_verifiable():
    beacon = os.urandom(32)
    entries = [f"p{i}" for i in range(50)]
    res = public.lottery_draw(beacon, 1, "raffle#1", entries, 5)
    assert len(res["output"]) == 5 and len(set(res["output"])) == 5  # distinct winners
    assert public.lottery_draw(beacon, 1, "raffle#1", entries, 5)["output"] == res["output"]
    assert public.verify_result(res)
    # tamper the output -> verification fails
    bad = dict(res); bad["output"] = list(reversed(res["output"]))
    assert not public.verify_result(bad)


def test_dice_range_coin_bounds():
    beacon = os.urandom(32)
    d = public.dice(beacon, 1, "g", sides=6, count=100)["output"]
    assert all(1 <= x <= 6 for x in d) and len(d) == 100
    rng = public.random_in_range(beacon, 1, "r", 10, 20, count=50)["output"]
    assert all(10 <= x <= 20 for x in rng)
    coins = public.coin_flip(beacon, 1, "c", count=20)["output"]
    assert set(coins) <= {"H", "T"}


def test_shuffle_is_permutation_and_weighted_respects_weights():
    beacon = os.urandom(32)
    items = list(range(20))
    sh = public.shuffle(beacon, 1, "s", items)["output"]
    assert sorted(sh) == items and sh != items  # permutation, (almost surely) reordered
    # weighted: a hugely dominant weight should win the vast majority of the time
    wins = {"A": 0, "B": 0}
    for i in range(200):
        out = public.weighted_choice(beacon, 1, f"w{i}", ["A", "B"], [99, 1])["output"]
        wins[out] += 1
    assert wins["A"] > wins["B"] * 5


def test_different_request_ids_independent():
    beacon = os.urandom(32)
    a = public.dice(beacon, 1, "req-a", sides=20, count=10)["output"]
    b = public.dice(beacon, 1, "req-b", sides=20, count=10)["output"]
    assert a != b  # independent streams per request_id


# --- service end-to-end: contributions -> aggregate -> beacon ---

def test_service_aggregate_and_beacon_chain():
    svc = QuantumWorkService()

    def contribute(round_id, addr):
        ch = svc.get_challenge(round_id)
        src = providers.HealthGatedSource(providers.SoftwareFallbackQRNG())
        sgn = hsm_tpm.SoftwareSelfSigner(key_dir="/tmp/claude-0/quw-test-keys")
        c = quw.build_contribution(src, sgn, round_id=round_id,
                                   nonce=bytes.fromhex(ch["nonce_hex"]), address=addr, n_bytes=2048)
        return svc.contribute(c.to_dict())

    contribute(1, "alice"); contribute(1, "bob")
    agg = svc.aggregate_for_round(1, require_attested=False)
    assert agg is not None and agg.n_total == 2
    b1 = svc.get_quantum_beacon(1)
    assert b1 is not None and b1.n_contributors == 2
    # round 2 chains off round 1's beacon value
    contribute(2, "alice")
    b2 = svc.get_quantum_beacon(2)
    assert b2.prev_hex == b1.value_hex
    # a draw off the served beacon verifies client-side
    res = public.lottery_draw(b1.value(), 1, "winners", ["x", "y", "z"], 2)
    assert public.verify_result(res)
