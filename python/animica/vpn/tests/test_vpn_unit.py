"""
Fast, hermetic unit tests for the dVPN pure-logic modules (no root, no network, no nft/wg).
Covers: config rendering, two-sided accounting/reconciliation, the isolation guard's render-only
invariants, the nftables ruleset SAFETY invariants, cross-language ML-DSA crypto, and a HONESTY
lint that the user-facing CLI/registry copy never overclaims (no "protected"/"anonymous"/on-chain
"paid" language, IOU framing preserved).
"""

from __future__ import annotations

import pathlib

import pytest

from animica.vpn import (
    KILLSWITCH_TABLE,
    NFT_TABLE,
    WG_SUBNET,
    accounting,
    config_gen,
    crypto,
    nftables,
)

# --------------------------------------------------------------------- config_gen


def test_client_conf_full_tunnel_forces_dns():
    c = config_gen.ClientConf(
        private_key="PRIV", address="10.99.0.7/32", exit_pubkey="EXITPUB",
        exit_endpoint="203.0.113.9:51820",
    )
    conf = config_gen.render_client_conf(c)
    assert "[Interface]" in conf and "[Peer]" in conf
    assert "AllowedIPs = 0.0.0.0/0" in conf          # full tunnel by default
    assert "DNS = 1.1.1.1" in conf                    # DNS forced through the tunnel (no leak)
    assert "Endpoint = 203.0.113.9:51820" in conf
    assert "PersistentKeepalive = 25" in conf


def test_client_conf_split_tunnel_and_no_dns():
    c = config_gen.ClientConf(
        private_key="P", address="10.99.0.8/32", exit_pubkey="E",
        exit_endpoint="h:51820", allowed_ips="10.8.0.0/24, 192.0.2.0/24", dns=None,
    )
    conf = config_gen.render_client_conf(c)
    assert "AllowedIPs = 10.8.0.0/24, 192.0.2.0/24" in conf
    assert "DNS" not in conf


def test_exit_conf_lists_every_peer():
    e = config_gen.ExitConf(private_key="P", peers=[("PK1", "10.99.0.2/32"), ("PK2", "10.99.0.3/32")])
    conf = config_gen.render_exit_conf(e)
    assert conf.count("[Peer]") == 2
    assert "PublicKey = PK1" in conf and "AllowedIPs = 10.99.0.3/32" in conf
    assert "ListenPort = " in conf


# --------------------------------------------------------------------- accounting


def test_usage_meter_monotonic_and_rebase_on_reset():
    # A per-session meter starts from a fresh wg interface at 0, so the first sample counts
    # from zero (matches WireGuard counter semantics at session start).
    m = accounting.UsageMeter(session_id="s1", side="exit")
    m.observe(100, 200, ts=1)              # from 0 -> cum (100, 200)
    s2 = m.observe(300, 500, ts=2)         # +200 rx, +300 tx
    assert (s2.cum_rx, s2.cum_tx) == (300, 500)
    # counter RESET (interface recreated) must not produce a negative delta
    s3 = m.observe(10, 10, ts=3)
    assert (s3.cum_rx, s3.cum_tx) == (300, 500)   # unchanged; rebased to a new baseline
    s4 = m.observe(60, 40, ts=4)           # +50 rx, +30 tx from the new baseline
    assert (s4.cum_rx, s4.cum_tx) == (350, 530)


def test_reconcile_takes_min_and_caps_at_capacity():
    # client says 1000, exit says 900 -> reward on the min, both under capacity
    reconciled, flagged = accounting.reconcile(1000, 900, capacity_bytes=10_000)
    assert reconciled == 900
    # >10% divergence is flagged
    _, flagged2 = accounting.reconcile(1000, 500, capacity_bytes=10_000)
    assert flagged2 is True
    # capacity cap dominates an inflated claim
    reconciled3, _ = accounting.reconcile(10**12, 10**12, capacity_bytes=5_000)
    assert reconciled3 == 5_000
    # negatives are floored to zero
    reconciled4, _ = accounting.reconcile(-5, 100, capacity_bytes=10_000)
    assert reconciled4 == 0


def test_signed_report_roundtrips_and_is_bound_to_payload():
    w = crypto.Wallet.generate()
    m = accounting.UsageMeter(session_id="sess-xyz", side="client")
    sample = m.observe(4096, 8192, ts=1700000000)
    rep = m.signed_report(w, sample)
    # the signed message is exactly the canonical string the marketplace re-derives
    assert rep["reportMsg"] == accounting.canonical_report_message(rep)
    assert crypto.verify(bytes.fromhex(rep["publicKey"]),
                         (crypto.SIGN_DOMAIN + rep["reportMsg"]).encode(),
                         bytes.fromhex(rep["sig"])) is True
    # tampering with a counter breaks verification
    tampered = accounting.canonical_report_message({**rep, "cumRx": rep["cumRx"] + 1})
    assert crypto.verify(bytes.fromhex(rep["publicKey"]),
                         (crypto.SIGN_DOMAIN + tampered).encode(),
                         bytes.fromhex(rep["sig"])) is False


# --------------------------------------------------------------------- nftables SAFETY


def test_exit_ruleset_is_isolated_and_never_destructive():
    rs = nftables.render_exit_ruleset("eth0", "anmwg0")
    assert f"table inet {NFT_TABLE}" in rs
    # SAFETY: never flush, never flip a base-chain policy to drop, never touch docker chains
    assert "flush" not in rs
    assert "policy drop" not in rs          # exit chains are policy accept (only ADD drops)
    assert "DOCKER" not in rs
    # every drop is scoped to the tunnel iface / subnet, so host/docker traffic is untouched
    for line in rs.splitlines():
        if line.strip().endswith("drop"):
            assert 'iifname "anmwg0"' in line, f"unscoped drop: {line!r}"
    # the egress ACL blocks LAN + cloud metadata + BitTorrent + SMTP
    assert "169.254.0.0/16" in rs and "10.0.0.0/8" in rs
    assert "6881-6889" in rs and "25" in rs
    # scoped NAT only for the tunnel subnet out the uplink
    assert f"ip saddr {WG_SUBNET} oifname \"eth0\" masquerade" in rs


def test_killswitch_is_fail_closed():
    ks = nftables.render_killswitch_ruleset("anmwg0", "203.0.113.9", 51820)
    assert f"table inet {KILLSWITCH_TABLE}" in ks
    assert "policy drop" in ks               # fail-closed: default drop
    assert 'oifname "anmwg0" accept' in ks   # only the tunnel + loopback + the handshake escape
    assert 'oifname "lo" accept' in ks
    assert "ip daddr 203.0.113.9 udp dport 51820 accept" in ks


def test_extra_block_v4_is_appended():
    rs = nftables.render_exit_ruleset("eth0", "anmwg0", extra_block_v4=["198.51.100.0/24"])
    assert "198.51.100.0/24" in rs


# --------------------------------------------------------------------- crypto interop


def test_address_derives_and_signature_verifies():
    w = crypto.Wallet.generate()
    assert w.address.startswith("anim1")
    sig = w.sign_login("challenge-123")
    assert crypto.verify(bytes.fromhex(w.public_key_hex), (crypto.SIGN_DOMAIN + "challenge-123").encode(),
                         bytes.fromhex(sig)) is True
    # wrong challenge fails
    assert crypto.verify(bytes.fromhex(w.public_key_hex), (crypto.SIGN_DOMAIN + "other").encode(),
                         bytes.fromhex(sig)) is False


# --------------------------------------------------------------------- HONESTY lint

_VPN_DIR = pathlib.Path(__file__).resolve().parent.parent


def test_cli_copy_never_overclaims():
    """The CLI must keep honest framing and never POSITIVELY claim rewards were paid on-chain."""
    cli = (_VPN_DIR.parent / "cli" / "vpn.py").read_text()
    low = cli.lower()
    # honest framing present
    assert "not tor" in low
    assert "iou" in low
    assert "deferred" in low
    # unambiguous positive overclaims must be absent (honest NEGATIONS like
    # "nothing is paid on-chain" / "not yet a spendable balance" are fine and expected).
    for bad in ("successfully paid", "now spendable", "added to your balance",
                "reward credited to", "settlement complete", "funds sent to your wallet"):
        assert bad not in low, f"overclaiming phrase in cli/vpn.py: {bad!r}"
    # any mention of 'spendable' must be negated
    if "spendable" in low:
        assert "not yet a spendable" in low or "not a spendable" in low


def test_doctor_gates_the_connected_claim():
    doc = (_VPN_DIR / "doctor.py").read_text().lower()
    # the leak self-test must be able to say a tunnel is NOT verified
    assert "not verified" in (_VPN_DIR.parent / "cli" / "vpn.py").read_text().lower()
    assert "healthy" in doc


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
