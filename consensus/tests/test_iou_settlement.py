# SPDX-License-Identifier: Apache-2.0
"""
FORK_IOU_SETTLEMENT (9.0.0) — on-chain IOU settlement via treasury-signed anchors.

These tests pin the consensus-critical properties of consensus/iou_settlement.py:
  * parse_anchor_payload is STRICT (fail-closed: any malformed anchor is ignored
    whole, never partially interpreted) and deterministic (duplicate addresses
    aggregate in first-occurrence order);
  * encode_anchor_payload round-trips through the strict parser and raises on
    every input the parser would reject (the CLI must never post a dud anchor);
  * scale_settlement_outputs never exceeds min(cap, miner_subsidy, requested),
    pays in full when requested <= cap, floors proportionally otherwise, and the
    rounding remainder stays with the miner (conservation — never mints);
  * settlement_pool_cap is 50 ANM at the current epoch, halves exactly on the
    emission schedule, and NEVER exceeds 20% of the block subsidy (operator's
    hard ceiling) at any height including every halving boundary and the tail;
  * extract_settlement_distribution binds anchors to the code-committed
    authority (declared sender AND signature pubkey), verifies signatures,
    ignores bad anchors whole, caps anchors per block, and RAISES
    SettlementUnavailable (fail closed) when the verify backend is missing
    while a candidate anchor exists;
  * the fork is inert below mainnet height 50,000 (grandfathered), with the
    ANIMICA_FORK_VPN_RELAY_REWARDS_HEIGHT env override retuning the boundary.
"""

from __future__ import annotations

import json

import pytest

from consensus.iou_settlement import (
    FORK_IOU_SETTLEMENT,
    MAX_ANCHORS_PER_BLOCK,
    MAX_ENTRIES_PER_ANCHOR,
    SETTLEMENT_AUTHORITY_ADDRESSES,
    SETTLEMENT_MAGIC,
    SettlementUnavailable,
    encode_anchor_payload,
    extract_settlement_distribution,
    parse_anchor_payload,
    scale_settlement_outputs,
    settlement_pool_cap,
)
from consensus.rewards import (
    COIN,
    MAX_MONEY,
    VPN_RELAY_REWARD_CAP,
    compute_subsidy_for_height,
    parse_emission_schedule,
)
from core.chain.block_import import _load_full_params_dict
from core.network_params import FORK_VPN_RELAY_REWARDS, is_fork_active
from core.utils.address import address_to_bytes

MAINNET = 1
H = 50_000  # FORK_VPN_RELAY_REWARDS / FORK_IOU_SETTLEMENT mainnet activation height
EPOCH = 1_350_000  # mainnet halving epoch length (spec/params.yaml)

AUTHORITY = SETTLEMENT_AUTHORITY_ADDRESSES[0]
AUTHORITY_KEY = address_to_bytes(AUTHORITY)


def _addr(i: int) -> str:
    """Deterministic VALID bech32m payout address #i (ml_dsa_65 alg id 0x1003)."""
    from pq.py.address import address_from_pubkey

    return address_from_pubkey(bytes([i % 256]) * 32, 0x1003)


def _key(i: int) -> bytes:
    return address_to_bytes(_addr(i))


def _payload(pay, v=1, extra=None, magic=SETTLEMENT_MAGIC) -> bytes:
    obj = {"v": v, "pay": pay}
    if extra:
        obj.update(extra)
    return magic + json.dumps(obj, separators=(",", ":")).encode("utf-8")


@pytest.fixture(scope="module")
def mparams():
    p = _load_full_params_dict(MAINNET)
    assert p, "mainnet params must load from spec/params.yaml"
    return p


@pytest.fixture(scope="module")
def mschedule(mparams):
    return parse_emission_schedule(mparams)


# ---------------------------------------------------------------------------
# (a) parse_anchor_payload — strict parse matrix
# ---------------------------------------------------------------------------


def test_parse_valid_single_entry():
    got = parse_anchor_payload(_payload([[_addr(1), 5 * COIN]]))
    assert got == [(_key(1), 5 * COIN)]


def test_parse_valid_multi_entry_preserves_order():
    got = parse_anchor_payload(
        _payload([[_addr(3), 7], [_addr(1), 5], [_addr(2), 9]])
    )
    assert got == [(_key(3), 7), (_key(1), 5), (_key(2), 9)]


def test_parse_duplicate_addresses_aggregate_first_occurrence_order():
    got = parse_anchor_payload(
        _payload([[_addr(1), 10], [_addr(2), 20], [_addr(1), 5], [_addr(2), 1]])
    )
    assert got == [(_key(1), 15), (_key(2), 21)]


def test_parse_rejects_wrong_magic():
    assert parse_anchor_payload(_payload([[_addr(1), 1]], magic=b"ANMSETL2")) is None
    assert parse_anchor_payload(_payload([[_addr(1), 1]], magic=b"XXMSETL1")) is None
    assert parse_anchor_payload(b"") is None


def test_parse_rejects_extra_json_keys():
    assert parse_anchor_payload(_payload([[_addr(1), 1]], extra={"memo": "x"})) is None


def test_parse_rejects_missing_keys():
    assert parse_anchor_payload(SETTLEMENT_MAGIC + b'{"v":1}') is None
    assert parse_anchor_payload(SETTLEMENT_MAGIC + b'{"pay":[]}') is None
    assert parse_anchor_payload(SETTLEMENT_MAGIC + b"[]") is None
    assert parse_anchor_payload(SETTLEMENT_MAGIC + b"null") is None


@pytest.mark.parametrize("v", [0, 2, -1, "1", 1.0, None, True])
def test_parse_rejects_v_not_int_1(v):
    # v=True is the sharp edge: True == 1 in Python but type(True) is bool, and
    # bool versions must be rejected exactly like bool amounts.
    payload = SETTLEMENT_MAGIC + json.dumps(
        {"v": v, "pay": [[_addr(1), 1]]}, separators=(",", ":")
    ).encode()
    assert parse_anchor_payload(payload) is None


@pytest.mark.parametrize("amt", [True, False])
def test_parse_rejects_bool_amounts(amt):
    # bool is an int subclass — must be rejected explicitly.
    assert parse_anchor_payload(_payload([[_addr(1), amt]])) is None


@pytest.mark.parametrize("amt", [0, -1, -5 * COIN, MAX_MONEY + 1, 1.5, "5", None])
def test_parse_rejects_bad_amounts(amt):
    assert parse_anchor_payload(_payload([[_addr(1), amt]])) is None


def test_parse_accepts_amount_exactly_max_money():
    got = parse_anchor_payload(_payload([[_addr(1), MAX_MONEY]]))
    assert got == [(_key(1), MAX_MONEY)]


def test_parse_rejects_total_over_max_money():
    # Each entry is <= MAX_MONEY but the SUM overflows — rejected whole.
    pay = [[_addr(1), MAX_MONEY], [_addr(2), 1]]
    assert parse_anchor_payload(_payload(pay)) is None


def test_parse_rejects_too_many_entries():
    pay = [[_addr(1), 1] for _ in range(MAX_ENTRIES_PER_ANCHOR + 1)]
    assert parse_anchor_payload(_payload(pay)) is None


def test_parse_accepts_exactly_max_entries():
    # MAX_ENTRIES distinct-ish entries (duplicates aggregate but count is on the
    # raw list) — the boundary itself must be valid.
    pay = [[_addr(1), 1] for _ in range(MAX_ENTRIES_PER_ANCHOR)]
    got = parse_anchor_payload(_payload(pay))
    assert got == [(_key(1), MAX_ENTRIES_PER_ANCHOR)]


def test_parse_rejects_empty_pay():
    assert parse_anchor_payload(_payload([])) is None


@pytest.mark.parametrize(
    "addr",
    [
        "anim1invalidchecksum0000000000000000000000000000000000000000000000",
        "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",  # wrong HRP
        "",
        "not-an-address",
        123,
        None,
        "anim1" + "q" * 200,  # over length bound
    ],
)
def test_parse_rejects_bad_addresses(addr):
    assert parse_anchor_payload(_payload([[addr, 1]])) is None


@pytest.mark.parametrize(
    "entry",
    [
        "not-a-list",
        [],
        ["anim1only"],
        [[1, 2], 3],  # 3-element via nesting trick below is separate; this is bad addr type
    ],
)
def test_parse_rejects_malformed_entries(entry):
    assert parse_anchor_payload(_payload([entry])) is None


def test_parse_rejects_three_element_entry():
    assert parse_anchor_payload(_payload([[_addr(1), 1, "x"]])) is None


def test_parse_rejects_non_utf8():
    assert parse_anchor_payload(SETTLEMENT_MAGIC + b"\xff\xfe{}") is None


def test_parse_rejects_non_bytes_input():
    assert parse_anchor_payload("ANMSETL1{}") is None  # type: ignore[arg-type]
    assert parse_anchor_payload(None) is None  # type: ignore[arg-type]
    assert parse_anchor_payload(12345) is None  # type: ignore[arg-type]


def test_parse_accepts_bytearray():
    got = parse_anchor_payload(bytearray(_payload([[_addr(1), 3]])))
    assert got == [(_key(1), 3)]


def test_parse_rejects_trailing_garbage_json():
    assert parse_anchor_payload(_payload([[_addr(1), 1]]) + b"garbage") is None


# ---------------------------------------------------------------------------
# (b) encode_anchor_payload — round-trip + raises on invalid input
# ---------------------------------------------------------------------------


def test_encode_round_trips_through_strict_parse():
    entries = [(_addr(1), 5 * COIN), (_addr(2), 7), (_addr(3), 123_456_789)]
    payload = encode_anchor_payload(entries)
    assert payload.startswith(SETTLEMENT_MAGIC)
    got = parse_anchor_payload(payload)
    assert got == [(_key(1), 5 * COIN), (_key(2), 7), (_key(3), 123_456_789)]


def test_encode_round_trip_duplicates_aggregate_on_parse():
    payload = encode_anchor_payload([(_addr(1), 10), (_addr(2), 1), (_addr(1), 5)])
    assert parse_anchor_payload(payload) == [(_key(1), 15), (_key(2), 1)]


def test_encode_round_trip_max_money_boundary():
    payload = encode_anchor_payload([(_addr(1), MAX_MONEY)])
    assert parse_anchor_payload(payload) == [(_key(1), MAX_MONEY)]


def test_encode_raises_on_empty():
    with pytest.raises(ValueError):
        encode_anchor_payload([])


def test_encode_raises_on_too_many_entries():
    entries = [(_addr(1), 1)] * (MAX_ENTRIES_PER_ANCHOR + 1)
    with pytest.raises(ValueError):
        encode_anchor_payload(entries)


@pytest.mark.parametrize("amt", [0, -1, -5 * COIN])
def test_encode_raises_on_non_positive_amount(amt):
    with pytest.raises(ValueError):
        encode_anchor_payload([(_addr(1), amt)])


def test_encode_raises_on_invalid_address():
    with pytest.raises(ValueError):
        encode_anchor_payload([("not-an-address", 1)])
    with pytest.raises(ValueError):
        encode_anchor_payload([("anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqbadsum", 1)])


def test_encode_raises_on_total_over_max_money():
    with pytest.raises(ValueError):
        encode_anchor_payload([(_addr(1), MAX_MONEY), (_addr(2), 1)])


# ---------------------------------------------------------------------------
# (c) scale_settlement_outputs — cap/affordability invariants
# ---------------------------------------------------------------------------


def test_scale_exact_full_payment_when_requested_under_cap():
    dist = [(_key(1), 10 * COIN), (_key(2), 5 * COIN)]
    out = scale_settlement_outputs(dist, pool_cap=50 * COIN, miner_subsidy=300 * COIN)
    assert out == dist  # requested <= cap and <= subsidy: paid in full, exactly


def test_scale_exact_full_payment_at_cap_boundary():
    dist = [(_key(1), 30 * COIN), (_key(2), 20 * COIN)]
    out = scale_settlement_outputs(dist, pool_cap=50 * COIN, miner_subsidy=300 * COIN)
    assert out == dist


@pytest.mark.parametrize(
    "amounts,cap,subsidy",
    [
        ([70 * COIN, 30 * COIN], 50 * COIN, 300 * COIN),  # cap binds
        ([70 * COIN, 30 * COIN], 50 * COIN, 20 * COIN),  # subsidy binds
        ([7, 3, 11], 5, 1_000),  # tiny integers, floor rounding
        ([1, 1, 1], 2, 2),  # affordable < n entries
        ([123_456_789, 987_654_321, 555], 100_000_000, 10**12),
        ([50 * COIN], 50 * COIN, 255 * COIN),  # exactly affordable
    ],
)
def test_scale_invariants_floor_and_conservation(amounts, cap, subsidy):
    dist = [(_key(i + 1), a) for i, a in enumerate(amounts)]
    requested = sum(amounts)
    affordable = min(requested, cap, subsidy)
    out = scale_settlement_outputs(dist, cap, subsidy)
    out_map = dict(out)
    total_out = sum(a for _, a in out)
    # never exceeds min(cap, subsidy, requested)
    assert total_out <= affordable
    assert total_out <= cap
    assert total_out <= subsidy
    assert total_out <= requested
    # each share is EXACTLY the floor formula (deterministic, integer-only) —
    # the rounding remainder stays with the miner (conservation, never mints)
    for (key, amt) in dist:
        share = (affordable * amt) // requested
        if share > 0:
            assert out_map[key] == share
        else:
            assert key not in out_map


def test_scale_zero_cases():
    dist = [(_key(1), 10)]
    assert scale_settlement_outputs([], 50, 300) == []
    assert scale_settlement_outputs(dist, 0, 300) == []
    assert scale_settlement_outputs(dist, 50, 0) == []
    assert scale_settlement_outputs(dist, -1, 300) == []
    assert scale_settlement_outputs(dist, 50, -1) == []
    assert scale_settlement_outputs([(_key(1), 0)], 50, 300) == []
    assert scale_settlement_outputs([(_key(1), -5)], 50, 300) == []


def test_scale_negative_entries_never_pay():
    out = scale_settlement_outputs([(_key(1), -5), (_key(2), 10)], 50, 300)
    assert out == [(_key(2), 10)]


# ---------------------------------------------------------------------------
# (d) settlement_pool_cap — 50 ANM, halving-decayed, NEVER >20% of the block
# ---------------------------------------------------------------------------


def test_cap_is_50_anm_at_current_epoch(mparams):
    assert settlement_pool_cap(H, mparams) == 50 * COIN
    assert settlement_pool_cap(1, mparams) == 50 * COIN
    assert settlement_pool_cap(EPOCH, mparams) == 50 * COIN  # last block of epoch 0


def test_cap_halves_exactly_with_subsidy_schedule(mparams, mschedule):
    # First halving: 50 -> 25 ANM at the same boundary the subsidy halves.
    assert settlement_pool_cap(EPOCH + 1, mparams) == 25 * COIN
    # Second halving: 12.5 ANM.
    assert settlement_pool_cap(2 * EPOCH + 1, mparams) == 12_500_000_000
    # Generally: cap == (50 ANM * current_total_subsidy) // start, min 50 ANM.
    start = mschedule["start_nANM_per_block"]
    for h in [1, H, EPOCH, EPOCH + 1, 3 * EPOCH + 1, 7 * EPOCH + 1, 64 * EPOCH + 5]:
        total = sum(compute_subsidy_for_height(h, mschedule))
        expected = min((VPN_RELAY_REWARD_CAP * total) // start, VPN_RELAY_REWARD_CAP)
        assert settlement_pool_cap(h, mparams) == expected


def test_cap_20pct_ceiling_invariant_across_halvings_and_tail(mparams, mschedule):
    # THE OPERATOR'S HARD REQUIREMENT: the settlement pool is never more than
    # 20% of the block subsidy — cap * 5 <= total_subsidy — at EVERY height,
    # including both sides of every halving boundary and deep into the tail.
    heights = [1, 2, H - 1, H, H + 1]
    max_halvings = mschedule["max_halvings"]
    for k in range(1, max_halvings + 2):
        heights.append(k * EPOCH)      # last block of epoch k-1
        heights.append(k * EPOCH + 1)  # first block of epoch k
    heights += [
        max_halvings * EPOCH + 12_345,   # clamped-epoch region
        100_000_000,                      # deep tail
        2_000_000_000,                    # deeper tail
    ]
    for h in heights:
        cap = settlement_pool_cap(h, mparams)
        total = sum(compute_subsidy_for_height(h, mschedule))
        assert cap * 5 <= total, (
            f"20% ceiling violated at height {h}: cap={cap} total_subsidy={total}"
        )
        assert cap <= VPN_RELAY_REWARD_CAP  # absolute 50 ANM bound too


def test_cap_zero_when_schedule_unavailable():
    assert settlement_pool_cap(H, {}) == 0
    assert settlement_pool_cap(H, None) == 0
    assert settlement_pool_cap(H, {"monetary": {}}) == 0


# ---------------------------------------------------------------------------
# (e) extract_settlement_distribution — authority binding + fail-closed
# ---------------------------------------------------------------------------


class _FakeUnsigned:
    def __init__(self, kind, data: bytes, sender: bytes):
        class _P:
            pass

        self.kind = kind
        self.payload = _P()
        self.payload.data = data
        self.sender = sender


class _FakeTx:
    """Block tx stand-in: carries an unsigned view + unique raw CBOR token."""

    _n = 0

    def __init__(self, data: bytes, sender: bytes, kind=None):
        from core.types.tx import TxKind

        _FakeTx._n += 1
        self.unsigned = _FakeUnsigned(
            TxKind.TRANSFER if kind is None else kind, data, sender
        )
        self._raw = b"RAWTX:" + str(_FakeTx._n).encode() + b":" + data[:16]

    def to_cbor(self) -> bytes:
        return self._raw


@pytest.fixture()
def stubbed_tx_methods(monkeypatch):
    """Stub the rpc.methods.tx seams the extractor uses, the way other
    consensus tests stub verification: _decode_tx maps raw->(tx, obj),
    _extract_sender_address reads obj, _verify_pq_signature raises on demand."""
    import rpc.methods.tx as tx_methods

    registry: dict[bytes, tuple[object, dict]] = {}

    def register(tx: _FakeTx, *, sig_addr, bad_sig: bool = False) -> _FakeTx:
        registry[tx.to_cbor()] = (tx, {"sig_addr": sig_addr, "bad_sig": bad_sig})
        return tx

    def fake_decode(raw: bytes):
        if bytes(raw) not in registry:
            raise ValueError("unknown raw tx in test registry")
        return registry[bytes(raw)]

    def fake_extract_sender(obj: dict):
        return obj.get("sig_addr")

    def fake_verify(tx_like, obj, *, chain_id: int):
        if obj.get("bad_sig"):
            raise ValueError("bad signature (test stub)")

    monkeypatch.setattr(tx_methods, "_decode_tx", fake_decode)
    monkeypatch.setattr(tx_methods, "_extract_sender_address", fake_extract_sender)
    monkeypatch.setattr(tx_methods, "_verify_pq_signature", fake_verify)
    return register


def _anchor_tx(register, entries, *, sender=AUTHORITY_KEY, sig_addr=AUTHORITY,
               bad_sig=False, data=None):
    payload = data if data is not None else encode_anchor_payload(entries)
    return register(_FakeTx(payload, sender), sig_addr=sig_addr, bad_sig=bad_sig)


def test_extract_no_candidates_fast_path_returns_empty():
    # No magic anywhere: [] without ever consulting the verify backend.
    assert extract_settlement_distribution([], chain_id=MAINNET) == []
    assert extract_settlement_distribution(None, chain_id=MAINNET) == []
    plain = _FakeTx(b"just a memo", AUTHORITY_KEY)
    assert extract_settlement_distribution([plain], chain_id=MAINNET) == []


def test_extract_no_candidates_does_not_raise_even_without_backend(monkeypatch):
    import rpc.methods.tx as tx_methods

    monkeypatch.setattr(tx_methods, "_pq_verify", None)
    monkeypatch.setattr(tx_methods, "_pq_verify_tx", None)
    plain = _FakeTx(b"no anchors here", AUTHORITY_KEY)
    assert extract_settlement_distribution([plain], chain_id=MAINNET) == []


def test_extract_raises_settlement_unavailable_when_backend_absent(monkeypatch, stubbed_tx_methods):
    import rpc.methods.tx as tx_methods

    monkeypatch.setattr(tx_methods, "_pq_verify", None)
    monkeypatch.setattr(tx_methods, "_pq_verify_tx", None)
    tx = _anchor_tx(stubbed_tx_methods, [(_addr(1), 5)])
    with pytest.raises(SettlementUnavailable):
        extract_settlement_distribution([tx], chain_id=MAINNET)


def test_extract_valid_anchor(stubbed_tx_methods):
    tx = _anchor_tx(stubbed_tx_methods, [(_addr(1), 5 * COIN), (_addr(2), 1)])
    got = extract_settlement_distribution([tx], chain_id=MAINNET)
    assert got == [(_key(1), 5 * COIN), (_key(2), 1)]


def test_extract_non_authority_sender_ignored(stubbed_tx_methods):
    tx = _anchor_tx(
        stubbed_tx_methods, [(_addr(1), 5)], sender=b"\x99" * 32, sig_addr=AUTHORITY
    )
    assert extract_settlement_distribution([tx], chain_id=MAINNET) == []


def test_extract_sig_pubkey_mismatch_ignored(stubbed_tx_methods):
    # Declared sender IS the authority, but the signature pubkey derives to a
    # different (valid) address — the declared field alone is never trusted.
    tx = _anchor_tx(
        stubbed_tx_methods, [(_addr(1), 5)], sender=AUTHORITY_KEY, sig_addr=_addr(7)
    )
    assert extract_settlement_distribution([tx], chain_id=MAINNET) == []


def test_extract_missing_sig_address_ignored(stubbed_tx_methods):
    tx = _anchor_tx(
        stubbed_tx_methods, [(_addr(1), 5)], sender=AUTHORITY_KEY, sig_addr=None
    )
    assert extract_settlement_distribution([tx], chain_id=MAINNET) == []


def test_extract_bad_signature_ignores_anchor_whole_but_keeps_good_ones(stubbed_tx_methods):
    bad = _anchor_tx(
        stubbed_tx_methods, [(_addr(1), 100), (_addr(2), 200)], bad_sig=True
    )
    good = _anchor_tx(stubbed_tx_methods, [(_addr(3), 7)])
    got = extract_settlement_distribution([bad, good], chain_id=MAINNET)
    assert got == [(_key(3), 7)]  # bad anchor ignored WHOLE, not partially


def test_extract_unparseable_authority_anchor_ignored(stubbed_tx_methods):
    # Authority-signed but strict-parse fails (extra key) — ignored whole.
    raw_payload = SETTLEMENT_MAGIC + json.dumps(
        {"v": 1, "pay": [[_addr(1), 5]], "memo": "hi"}, separators=(",", ":")
    ).encode()
    bad = _anchor_tx(stubbed_tx_methods, None, data=raw_payload)
    good = _anchor_tx(stubbed_tx_methods, [(_addr(2), 9)])
    got = extract_settlement_distribution([bad, good], chain_id=MAINNET)
    assert got == [(_key(2), 9)]


def test_extract_non_transfer_kind_ignored(stubbed_tx_methods):
    from core.types.tx import TxKind

    payload = encode_anchor_payload([(_addr(1), 5)])
    tx = _FakeTx(payload, AUTHORITY_KEY, kind=TxKind.CALL)
    stubbed_tx_methods(tx, sig_addr=AUTHORITY)
    assert extract_settlement_distribution([tx], chain_id=MAINNET) == []


def test_extract_anchor_limit_per_block_in_order(stubbed_tx_methods):
    txs = [
        _anchor_tx(stubbed_tx_methods, [(_addr(10 + i), 100 + i)])
        for i in range(MAX_ANCHORS_PER_BLOCK + 2)
    ]
    got = extract_settlement_distribution(txs, chain_id=MAINNET)
    # only the FIRST MAX_ANCHORS_PER_BLOCK valid anchors count, in block order
    assert got == [
        (_key(10 + i), 100 + i) for i in range(MAX_ANCHORS_PER_BLOCK)
    ]


def test_extract_invalid_anchors_do_not_consume_the_anchor_budget(stubbed_tx_methods):
    # A rejected anchor must not count toward MAX_ANCHORS_PER_BLOCK.
    bad = _anchor_tx(stubbed_tx_methods, [(_addr(1), 1)], bad_sig=True)
    goods = [
        _anchor_tx(stubbed_tx_methods, [(_addr(20 + i), 10 + i)])
        for i in range(MAX_ANCHORS_PER_BLOCK)
    ]
    got = extract_settlement_distribution([bad] + goods, chain_id=MAINNET)
    assert got == [(_key(20 + i), 10 + i) for i in range(MAX_ANCHORS_PER_BLOCK)]


def test_extract_aggregates_across_anchors_first_occurrence_order(stubbed_tx_methods):
    a = _anchor_tx(stubbed_tx_methods, [(_addr(1), 10), (_addr(2), 1)])
    b = _anchor_tx(stubbed_tx_methods, [(_addr(2), 2), (_addr(3), 5)])
    got = extract_settlement_distribution([a, b], chain_id=MAINNET)
    assert got == [(_key(1), 10), (_key(2), 3), (_key(3), 5)]


# ---------------------------------------------------------------------------
# (f) pre-fork inertness + env override (mirrors test_foundation_split)
# ---------------------------------------------------------------------------


def test_fork_key_alias():
    assert FORK_IOU_SETTLEMENT == FORK_VPN_RELAY_REWARDS == "vpn_relay_rewards"


@pytest.mark.parametrize("height", [0, 1, 42_001, 44_444, 49_999])
def test_fork_inactive_below_50000_on_mainnet(height, monkeypatch):
    monkeypatch.delenv("ANIMICA_FORK_VPN_RELAY_REWARDS_HEIGHT", raising=False)
    assert not is_fork_active(FORK_IOU_SETTLEMENT, height, chain_id=MAINNET)


@pytest.mark.parametrize("height", [50_000, 50_001, 1_350_001])
def test_fork_active_at_and_after_50000_on_mainnet(height, monkeypatch):
    monkeypatch.delenv("ANIMICA_FORK_VPN_RELAY_REWARDS_HEIGHT", raising=False)
    assert is_fork_active(FORK_IOU_SETTLEMENT, height, chain_id=MAINNET)


def test_fork_env_override_retunes_boundary(monkeypatch):
    monkeypatch.setenv("ANIMICA_FORK_VPN_RELAY_REWARDS_HEIGHT", "60000")
    assert not is_fork_active(FORK_IOU_SETTLEMENT, 50_000, chain_id=MAINNET)
    assert not is_fork_active(FORK_IOU_SETTLEMENT, 59_999, chain_id=MAINNET)
    assert is_fork_active(FORK_IOU_SETTLEMENT, 60_000, chain_id=MAINNET)
