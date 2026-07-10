"""FORK_ADDRESS_FREEZE consensus rule + the access-policy header-spoof fix (7.0.0)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.network_params import (
    FORK_ADDRESS_FREEZE,
    get_activation_height,
    is_fork_active,
)
from execution.migrations.address_freeze_2026 import (
    frozen_digests,
    is_frozen,
)
import core.chain.block_import as bi

THIEF = bytes.fromhex("ce0c733bab5f7c86fe106f4f817892adc952ec6312c8378c6b45ebd230d4870c")
OTHER = bytes.fromhex("11" * 32)
MAIN_H = 42_000


# ----------------------------- freeze set -----------------------------

def test_frozen_set_contains_thief_only():
    assert THIEF in frozen_digests()
    assert OTHER not in frozen_digests()


def test_is_frozen_grandfathered_below_entry_height():
    assert is_frozen(THIEF, height=MAIN_H) is True
    assert is_frozen(THIEF, height=MAIN_H - 1) is False   # forward-only
    assert is_frozen(THIEF, height=None) is True          # pre-gate (unconditional)
    assert is_frozen(OTHER, height=10**9) is False
    assert is_frozen(None, height=MAIN_H) is False


# ----------------------------- fork gate ------------------------------

def test_fork_gate_mainnet_activation():
    # block_import passes chain_id ONLY (no network_name) — must still resolve.
    assert get_activation_height(FORK_ADDRESS_FREEZE, chain_id=1) == MAIN_H
    assert is_fork_active(FORK_ADDRESS_FREEZE, MAIN_H - 1, chain_id=1) is False
    assert is_fork_active(FORK_ADDRESS_FREEZE, MAIN_H, chain_id=1) is True


def test_fork_gate_env_override(monkeypatch):
    monkeypatch.setenv("ANIMICA_FORK_ADDRESS_FREEZE_HEIGHT", "99999")
    assert get_activation_height(FORK_ADDRESS_FREEZE, chain_id=1) == 99999
    assert is_fork_active(FORK_ADDRESS_FREEZE, 42000, chain_id=1) is False


# ------------------------ block-validation gate -----------------------

def _tx(sender=None, to=None, kind=0):
    payload = SimpleNamespace(to=to)
    return SimpleNamespace(unsigned=SimpleNamespace(kind=kind, sender=sender, payload=payload))


def _block(txs):
    return SimpleNamespace(txs=txs)


def test_reject_block_spending_from_frozen_sender():
    blk = _block([_tx(sender=THIEF, to=OTHER)])
    reason = bi._verify_block_frozen_addresses_gated(blk, MAIN_H, 1)
    assert reason is not None and "frozen_sender" in reason


def test_reject_block_sending_to_frozen_recipient():
    blk = _block([_tx(sender=OTHER, to=THIEF)])
    reason = bi._verify_block_frozen_addresses_gated(blk, MAIN_H, 1)
    assert reason is not None and "frozen_recipient" in reason


def test_grandfathered_below_activation_is_accepted():
    blk = _block([_tx(sender=THIEF, to=OTHER)])
    assert bi._verify_block_frozen_addresses_gated(blk, MAIN_H - 1, 1) is None


def test_non_frozen_block_accepted():
    blk = _block([_tx(sender=OTHER, to=bytes.fromhex("22" * 32))])
    assert bi._verify_block_frozen_addresses_gated(blk, MAIN_H, 1) is None


def test_coinbase_with_frozen_output_is_skipped():
    # A coinbase (kind=3) is never subject to the freeze — reward payouts must
    # never be false-rejected.
    blk = _block([_tx(sender=None, to=THIEF, kind=3)])
    assert bi._verify_block_frozen_addresses_gated(blk, MAIN_H, 1) is None


def test_no_env_shadow_valve_split_safety(monkeypatch):
    # A per-node env that suppressed rejection post-activation would let a shadow
    # node ACCEPT a frozen-spend block that enforcing nodes REJECT -> chain split.
    # The gate must ignore any such env once the fork is active.
    monkeypatch.setenv("ANIMICA_ADDRESS_FREEZE_SHADOW", "1")
    blk = _block([_tx(sender=THIEF, to=OTHER)])
    reason = bi._verify_block_frozen_addresses_gated(blk, MAIN_H, 1)
    assert reason is not None and "frozen_sender" in reason
    # And there is no shadow helper left to reintroduce the split vector.
    assert not hasattr(bi, "_address_freeze_shadow")


# --------------------- access-policy header-spoof fix -------------------

def _make_policy():
    from rpc.access_policy import AccessPolicy
    return AccessPolicy()


def test_spoofed_xff_localhost_is_not_local():
    pol = _make_policy()
    # Remote attacker through nginx spoofing X-Forwarded-For: 127.0.0.1.
    ctx = SimpleNamespace(
        headers={"x-forwarded-for": "127.0.0.1"},
        client=SimpleNamespace(host="203.0.113.7"),
    )
    assert pol._is_local_request(ctx) is False


def test_xreal_ip_presence_marks_remote_even_if_socket_is_loopback():
    pol = _make_policy()
    # Behind nginx the socket peer is loopback, but X-Real-IP presence => remote.
    ctx = SimpleNamespace(
        headers={"x-real-ip": "203.0.113.7"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    assert pol._is_local_request(ctx) is False


def test_genuine_direct_loopback_is_local():
    pol = _make_policy()
    # A real internal caller connects directly to loopback with NO proxy headers.
    ctx = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))
    assert pol._is_local_request(ctx) is True


def test_remote_direct_is_not_local():
    pol = _make_policy()
    ctx = SimpleNamespace(headers={}, client=SimpleNamespace(host="203.0.113.7"))
    assert pol._is_local_request(ctx) is False


def test_admin_allowlist_not_spoofable_via_xff():
    # An operator allowlists an office IP. A remote attacker spoofs it in
    # X-Forwarded-For. The allowlist must match the REAL socket peer, not the
    # header — so the spoof is NOT authorized.
    import ipaddress
    from rpc.access_policy import AccessPolicy

    pol = AccessPolicy(admin_allowlist=[ipaddress.ip_network("198.51.100.9/32")])
    spoof = SimpleNamespace(
        headers={"x-forwarded-for": "198.51.100.9"},
        client=SimpleNamespace(host="203.0.113.7"),  # real remote attacker
    )
    assert pol._authorized(spoof) is False
    assert pol._peer_injection_authorized(spoof, "198.51.100.9") is False
    # A genuine direct connection from the allowlisted IP still authorizes.
    genuine = SimpleNamespace(
        headers={}, client=SimpleNamespace(host="198.51.100.9")
    )
    assert pol._authorized(genuine) is True


# --------------------- PTL block-template freeze pre-gate ----------------------

def test_ptl_pregate_excludes_frozen_sender_and_recipient(monkeypatch):
    import core.ptl.selection as sel

    THIEF_HEX = THIEF  # 32-byte frozen digest

    # 1) sender fast-path (stored PtlEntry.sender field).
    e_sender = SimpleNamespace(sender=THIEF_HEX, tx_bytes=b"")
    assert sel._entry_spends_frozen(e_sender) is True

    # 2) recipient only — decoded from tx_bytes. Stub the canonical decoder so the
    #    test targets the gate logic, not the wire codec.
    def _fake_env(raw):
        return {"tx": {"from": OTHER, "payload": {"v": {"to": THIEF_HEX}}}}

    monkeypatch.setattr(sel, "_normalize_tx_envelope", _fake_env)
    e_recip = SimpleNamespace(sender=None, tx_bytes=b"\x01\x02")
    assert sel._entry_spends_frozen(e_recip) is True

    # 3) neither frozen -> kept.
    monkeypatch.setattr(
        sel, "_normalize_tx_envelope",
        lambda raw: {"tx": {"from": OTHER, "payload": {"v": {"to": OTHER}}}},
    )
    e_clean = SimpleNamespace(sender=None, tx_bytes=b"\x01\x02")
    assert sel._entry_spends_frozen(e_clean) is False

    # 4) undecodable bytes must not crash and must not false-drop (backstop covers).
    def _boom(raw):
        raise ValueError("bad cbor")

    monkeypatch.setattr(sel, "_normalize_tx_envelope", _boom)
    e_bad = SimpleNamespace(sender=None, tx_bytes=b"\xff")
    assert sel._entry_spends_frozen(e_bad) is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
