# Animica dVPN

A decentralized VPN built into the Animica node. Anyone can run an **exit** and (once
settlement lands) earn ANM for the bandwidth they carry; anyone can be a **client** and
route their traffic through a chosen exit. Two ways to use it:

| | System VPN (CLI) | Browser proxy (extension) |
|---|---|---|
| Scope | Whole device | One browser only |
| Transport | WireGuard tunnel (`animica vpn up`) | HTTP CONNECT proxy (`chrome.proxy`) |
| Protects from | ISP / LAN sees only the tunnel | ISP / LAN sees only the proxy, for that browser |
| Install | `pip install -U animica` | Load the unpacked extension from animica.dev/browser |

## Honest scope — read this first

- **Single-hop, not Tor.** Your traffic is hidden from your ISP and LAN, but the **exit
  operator can see any non-HTTPS traffic** and knows your source IP. Use HTTPS. If you need
  anonymity from the exit itself, this is not the tool.
- **The browser extension is a proxy, not a system VPN.** It only moves traffic for the
  browser it's installed in. It never claims to protect your whole device.
- **Rewards are IOUs today.** Bandwidth is metered and signed by both sides, but nothing is
  paid on-chain yet — settlement is deferred (see *Block rewards* below). Do not treat dVPN
  earnings as a spendable balance.
- **Running an exit egresses third-party traffic from your IP.** It is **off by default**,
  opt-in, and gated behind an on-screen Terms-of-Service acceptance you must sign with your
  wallet. You accept the liability that comes with being an egress point.

## Client — route your device

```bash
animica vpn exits                 # list available exit locations (you pick; server only ranks)
animica vpn up --region eu        # bring up a WireGuard tunnel through an EU exit
animica vpn up <exit-id>          # or pick a specific exit
animica vpn status                # tunnel state, transfer counters, apparent IP
animica vpn doctor                # leak self-test — refuses to say "protected" until checks pass
animica vpn down                  # tear down, remove killswitch, report final usage
```

`up` installs a **fail-closed killswitch** by default (`--no-killswitch` to disable): if the
tunnel drops, traffic is dropped rather than leaking to your real IP. `doctor` verifies the
handshake is live, the apparent IP actually changed, there's no IPv6 route bypassing the
tunnel, and the killswitch is present — and **gates the "connected" claim** on all of them.

Location selection is **client-side**: `animica vpn exits` and the extension picker both let
*you* choose the exit. The registry only ranks by load/reputation/RTT; it never forces one.

## Browser proxy — route just one browser

Install the extension (animica.dev/browser or `/extension`), open it, and switch to the
**VPN** tab. It lists online exit locations by country; click **Route** to send that browser
through one. A **VPN** badge shows on the toolbar icon while active; click **Stop** to clear.

- Base install asks only for the `proxy` and `storage` permissions.
- Exits that require an access token request the `webRequest` permission **at the moment you
  pick one** — an optional permission, not forced on every user.
- Local, `.anm`, and `animica.dev` traffic bypasses the proxy so name resolution keeps working.

## Exit operator — run a relay, earn ANM

```bash
animica vpn exit register --region eu --country de --city frankfurt \
    --i-accept-exit-tos                       # sign the ToS once (wallet-signed record)
animica vpn exit serve --region eu --country de --city frankfurt \
    --browser-proxy                           # run the exit (foreground); add HTTP proxy for the extension
```

The exit daemon:

- refuses to start on a validator/consensus host unless you pass `--i-am-not-the-validator`
  (a guard so an exit's egress firewall never touches a node's consensus networking);
- installs an **isolated** nftables table (`inet animica_vpn`) that MASQUERADEs tunnel
  traffic and **blocks** RFC1918 / loopback / link-local / cloud-metadata (169.254.169.254)
  and abuse ports (SMTP, NetBIOS, SMB, RDP, BitTorrent). It never flushes or changes your
  host firewall policy;
- meters bytes per peer and posts **two-sided signed** usage reports; the registry reconciles
  the client's and the exit's counts (takes the min, flags >10% divergence) so neither side
  can inflate its reward.

## Block rewards (consensus) — activates at height 50,000

`FORK_VPN_RELAY_REWARDS` is a forward-only, height-gated consensus fork scheduled for
**mainnet block 50,000**. When live, a capped slice of each block's subsidy is paid straight
from the coinbase to relay operators, carved out of the miner's share (emission-conserving —
it never mints new ANM above the schedule):

- **Never more than 50 ANM per block** to the whole relay pool.
- The cap **decays with the halving schedule** (≤50 ANM at epoch 0, ≤25 at epoch 1, …), in
  proportion to the block subsidy.
- Relay operators are **node operators**: the reward is paid by the same coinbase that pays
  mining, to node operators running relays.

The fork ships **inert**: the on-chain distribution source (`sealed_relay_distribution`)
returns an empty set in this release, so at and after height 50,000 the block reward is
byte-identical to no-fork — verified by three independent adversarial reviews (mint / split /
inertness). Turning the mechanism live requires sealing a relay-contribution root and a fresh
height-gated activation behind its own review. Until then, dVPN rewards remain off-chain IOUs.

## Security model

- **Keys / auth.** ML-DSA-65 (FIPS 204, algId 0x1003) — the same post-quantum scheme as
  Animica wallets. Registry auth is challenge → wallet-signed response → bearer key. Exit
  descriptors and usage reports are wallet-signed.
- **Egress ACL** is enforced identically on the WireGuard path (nftables) and the browser
  proxy (in-process check), so neither can reach your LAN or a cloud metadata endpoint.
- **No plaintext interception.** The HTTP proxy CONNECT-tunnels HTTPS end-to-end; it never
  terminates TLS. Plain-HTTP requests are forwarded but are, by nature, visible to the exit.
- **Isolation.** All firewall state lives in dedicated nftables tables; teardown removes only
  those. The netns end-to-end test (`python -m animica.vpn.tests.smoke_netns`) exercises the
  real tunnel + ACL with zero impact on the host namespace.
