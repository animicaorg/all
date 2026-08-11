"""FORK_QUANTUM_BEACON (9.5.0) — dormant-by-design binding to attested quantum randomness.

The property that matters most here is a NEGATIVE one: no block may ever be rejected for
NOT carrying a beacon, at any height, forever. That is what makes a QRNG outage unable to
halt the chain — not a fail-open branch that could be got wrong, but the absence of any
path that depends on availability. Most of this file exists to pin that.
"""

from __future__ import annotations

import cbor2
import pytest

from consensus.quantum_beacon import (
    BEACON_DOMAIN,
    BeaconError,
    beacon_binding_active,
    check_commitment,
    compute_beacon_value,
    extract_commitment,
    validate_header_beacon,
)

BELOW, AT = 74_999, 75_000
PREV = bytes(range(32))
AGG = bytes(reversed(range(32)))


def _commit(round_id=1, prev=PREV, aggregate=AGG, value=None):
    return {
        "round": round_id,
        "prev": prev,
        "aggregate": aggregate,
        "value": value if value is not None else compute_beacon_value(round_id, prev, aggregate),
    }


def _extra(commitment=None, **rest):
    d = {"coinbase": b"\x01" * 32, **rest}
    if commitment is not None:
        d["beacon"] = commitment
    return cbor2.dumps(d)


# --- the domain must not drift from the real beacon builder ------------------

def test_the_domain_matches_the_real_beacon_builder():
    """The constant is duplicated so consensus need not import the randomness package at
    validation time. This test is the guard against the two drifting — and it has already
    earned its place: the first version of the constant was wrong
    (animica/qrng/beacon vs .../v1), which would have rejected every genuine beacon."""
    import randomness.qrng.public as P

    assert BEACON_DOMAIN == P._BEACON_DOMAIN


def test_recomputation_matches_build_quantum_beacon_exactly():
    import types

    import randomness.qrng.public as P

    agg = types.SimpleNamespace(value=AGG, attested=True, n_total=3, n_attested=3,
                                commitment=AGG.hex())
    built = P.build_quantum_beacon(7, PREV, agg)
    assert compute_beacon_value(7, PREV, AGG).hex() == built.value_hex


# --- the liveness guarantee -------------------------------------------------

@pytest.mark.parametrize("height", [0, BELOW, AT, 75_001, 10**6])
def test_a_block_with_no_beacon_is_valid_at_every_height(height):
    """A QRNG outage must be incapable of affecting the chain. There is no path that
    rejects for absence, so there is nothing to fail open."""
    assert validate_header_beacon(_extra(), height) is None
    assert validate_header_beacon(b"", height) is None
    assert validate_header_beacon(None, height) is None


def test_an_extra_field_this_rule_does_not_own_is_never_a_rejection():
    """`extra` is shared with coinbase/instant_block. Undecodable or foreign content must
    read as "no commitment", never as an error."""
    assert validate_header_beacon(b"\xff\xff not cbor", AT) is None
    assert validate_header_beacon(cbor2.dumps({"instant_block": True}), AT) is None
    assert validate_header_beacon(cbor2.dumps({"beacon": "not-a-map"}), AT) is None
    assert extract_commitment(cbor2.dumps({"beacon": 42})) is None


# --- when a commitment IS present ------------------------------------------

def test_a_valid_commitment_is_accepted_and_returns_its_value():
    c = _commit()
    got = validate_header_beacon(_extra(c), AT)
    assert got == c["value"]


def test_a_tampered_value_is_rejected():
    bad = _commit(value=b"\xaa" * 32)
    with pytest.raises(BeaconError) as exc:
        validate_header_beacon(_extra(bad), AT)
    assert exc.value.code == "BEACON_VALUE_MISMATCH"


def test_a_tampered_aggregate_is_rejected():
    c = _commit()
    c["aggregate"] = b"\x99" * 32          # value no longer recomputes
    with pytest.raises(BeaconError) as exc:
        validate_header_beacon(_extra(c), AT)
    assert exc.value.code == "BEACON_VALUE_MISMATCH"


@pytest.mark.parametrize("bad", [
    {"prev": PREV, "aggregate": AGG, "value": b"\x00" * 32},          # no round
    {"round": -1, "prev": PREV, "aggregate": AGG, "value": b"\x00" * 32},
    {"round": 1, "aggregate": AGG, "value": b"\x00" * 32},            # no prev
    {"round": 1, "prev": b"\x01" * 8, "aggregate": AGG, "value": b"\x00" * 32},  # short prev
    {"round": 1, "prev": PREV, "aggregate": AGG, "value": b"\x00" * 8},          # short value
])
def test_malformed_commitments_are_rejected_not_ignored(bad):
    """Present-but-broken must fail closed. Ignoring it would let a miner claim a beacon
    commitment while committing to nothing."""
    with pytest.raises(BeaconError):
        validate_header_beacon(_extra(bad), AT)


# --- continuity: the CHAIN commits, not just a block -----------------------

def test_the_prev_must_chain_to_the_ancestor_commitment():
    parent = _commit(round_id=1)
    child = _commit(round_id=2, prev=parent["value"])
    assert validate_header_beacon(_extra(child), AT, parent_extra=_extra(parent)) == child["value"]


def test_an_unrelated_beacon_cannot_be_grafted_on():
    """Recomputation alone would let every block invent its own beacon; the prev-chain is
    what makes the canonical chain commit to the randomness."""
    parent = _commit(round_id=1)
    orphan = _commit(round_id=2, prev=b"\x77" * 32)   # internally consistent, wrong chain
    with pytest.raises(BeaconError) as exc:
        validate_header_beacon(_extra(orphan), AT, parent_extra=_extra(parent))
    assert exc.value.code == "BEACON_CHAIN_BREAK"


def test_the_round_must_strictly_increase():
    parent = _commit(round_id=5)
    replay = _commit(round_id=5, prev=parent["value"])
    with pytest.raises(BeaconError) as exc:
        validate_header_beacon(_extra(replay), AT, parent_extra=_extra(parent))
    assert exc.value.code == "BEACON_ROUND_REGRESSION"


def test_the_first_committing_block_needs_no_ancestor():
    """Starting the chain of beacons must be possible — otherwise it could never begin."""
    first = _commit(round_id=1)
    assert validate_header_beacon(_extra(first), AT, parent_extra=_extra(None)) == first["value"]


# --- height gating ---------------------------------------------------------

def test_below_the_fork_a_commitment_is_ignored_entirely():
    """Forward-only: a historical block carrying a malformed commitment stays valid."""
    junk = {"round": 1, "prev": b"\x01" * 8, "aggregate": AGG, "value": b"\x00" * 4}
    assert validate_header_beacon(_extra(junk), BELOW) is None


def test_the_boundary_is_exactly_75000():
    assert beacon_binding_active(74_999) is False
    assert beacon_binding_active(75_000) is True


def test_testnet_and_devnet_have_it_from_genesis():
    for chain_id in (2, 1337):
        assert beacon_binding_active(0, chain_id=chain_id) is True, chain_id
