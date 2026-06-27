"""Tests for the quantum-beacon -> AI seed binding and unbiasable audit selection."""

from __future__ import annotations

import os

from aicf.integration import quantum_seed as qs


def test_seed_is_deterministic_and_verifiable():
    beacon = os.urandom(32)
    a = qs.quantum_seed_for(beacon_seed=beacon, beacon_round=42, job_id="ena.train.sft#1", attested=True)
    b = qs.quantum_seed_for(beacon_seed=beacon, beacon_round=42, job_id="ena.train.sft#1", attested=True)
    assert a.seed_hex == b.seed_hex  # reproducible
    assert a.attested is True
    assert qs.verify_quantum_seed(a, beacon_seed=beacon)  # verifiable from public beacon


def test_seed_changes_with_beacon_round_and_job():
    beacon = os.urandom(32)
    base = qs.quantum_seed_for(beacon_seed=beacon, beacon_round=1, job_id="j")
    assert base.seed_hex != qs.quantum_seed_for(beacon_seed=beacon, beacon_round=2, job_id="j").seed_hex
    assert base.seed_hex != qs.quantum_seed_for(beacon_seed=beacon, beacon_round=1, job_id="k").seed_hex
    assert base.seed_hex != qs.quantum_seed_for(beacon_seed=os.urandom(32), beacon_round=1, job_id="j").seed_hex


def test_forged_seed_is_rejected():
    beacon = os.urandom(32)
    good = qs.quantum_seed_for(beacon_seed=beacon, beacon_round=3, job_id="j")
    forged = qs.QuantumSeed(seed_hex=os.urandom(32).hex(), beacon_round=3, job_id="j",
                            attested=True, beacon_seed_hex=beacon.hex())
    assert not qs.verify_quantum_seed(forged, beacon_seed=beacon)


def test_seed_everything_seeds_python_random_reproducibly():
    beacon = os.urandom(32)
    q = qs.quantum_seed_for(beacon_seed=beacon, beacon_round=9, job_id="run")
    import random
    qs.seed_everything(q); a = [random.random() for _ in range(5)]
    qs.seed_everything(q); b = [random.random() for _ in range(5)]
    assert a == b  # same quantum seed -> identical stream


def test_audit_select_is_deterministic_and_unbiasable():
    beacon = os.urandom(32)
    items = [f"receipt-{i}" for i in range(100)]
    s1 = qs.audit_select(items, beacon_seed=beacon, beacon_round=5, k=10)
    s2 = qs.audit_select(items, beacon_seed=beacon, beacon_round=5, k=10)
    assert s1 == s2 and len(s1) == 10           # reproducible
    s3 = qs.audit_select(items, beacon_seed=beacon, beacon_round=6, k=10)
    assert s3 != s1                              # different round -> different audit set
    # selection is a subset of the items
    assert set(s1).issubset(set(items))
