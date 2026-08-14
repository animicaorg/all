import type { Metadata } from 'next';
import VpnExits from './VpnExits';

// The Animica dVPN landing. Honest-first: leads with scope + limits, then the two ways to use it
// (system VPN via CLI, browser proxy via the extension), a live list of exit locations, and the
// operator / block-reward story. Runtime-fetched exit list (client child) so the page build never
// couples to the VPN Prisma schema.

export const metadata: Metadata = {
  title: 'Animica dVPN — a decentralized VPN that pays its relays',
  description:
    'Route your device (WireGuard CLI) or just your browser (extension) through a chosen exit location on the Animica network. Single-hop, post-quantum-authenticated, and — at block 50,000 — paid straight from the block reward, capped at 50 ANM/block.',
  alternates: { canonical: 'https://animica.dev/vpn' },
  openGraph: {
    title: 'Animica dVPN — a decentralized VPN that pays its relays',
    description:
      'Pick an exit location and route your device or browser through it. Post-quantum auth, fail-closed killswitch, and relay rewards paid from the block subsidy (capped 50 ANM/block).',
    url: 'https://animica.dev/vpn',
    siteName: 'Animica',
    type: 'website',
  },
  twitter: { card: 'summary_large_image', title: 'Animica dVPN — a decentralized VPN that pays its relays' },
};

export const dynamic = 'force-dynamic';

export default function VpnPage() {
  return (
    <div className="wrap" style={{ paddingTop: 44 }}>
      <div style={{ textAlign: 'center', maxWidth: 730, margin: '0 auto' }}>
        <div className="pill" style={{ marginBottom: 14 }}>🛡️ Animica Internet · decentralized VPN</div>
        <h1 style={{ fontSize: 44, letterSpacing: '-0.035em', lineHeight: 1.05 }}>
          A VPN that{' '}
          <span style={{ background: 'linear-gradient(120deg,var(--accent-2),var(--accent))', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' }}>
            pays its relays
          </span>
        </h1>
        <p className="muted" style={{ fontSize: 17, marginTop: 12 }}>
          Pick an exit location and route your <b>whole device</b> (WireGuard, via the CLI) or just{' '}
          <b>this browser</b> (via the extension) through it. Post-quantum authenticated, fail-closed
          killswitch, and — at block 50,000 — relays get paid straight from the block reward.
        </p>
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 12, marginTop: 22 }}>
          <a className="btn primary" href="/browser">↓ Get the browser extension</a>
          <a className="btn" href="#exits">See exit locations</a>
        </div>
        <div className="muted" style={{ fontSize: 12.5, marginTop: 10 }}>
          Whole-device tunnel: <code className="inline">pip install -U animica &amp;&amp; animica vpn up</code>
        </div>
      </div>

      {/* Honest scope — never buried */}
      <section className="section">
        <div className="card" style={{ borderColor: 'var(--warn, #6b5320)', background: 'rgba(120,90,20,0.08)' }}>
          <div className="top"><div className="ico">⚠️</div><div><h3>Read this before you rely on it</h3></div></div>
          <p style={{ marginTop: 4 }}>
            <b>Single-hop, not Tor.</b> Your ISP and LAN see only the tunnel, but the <b>exit operator</b> can
            see any non-HTTPS traffic and knows your source IP — use HTTPS.{' '}
            <b>The extension is a browser proxy, not a system VPN</b> — it protects only the browser it&apos;s in.{' '}
            <b>Rewards are IOUs today</b>: bandwidth is metered and signed by both sides, but on-chain
            settlement is deferred. <b>Running an exit</b> egresses third-party traffic from your IP — it&apos;s
            off by default, opt-in, and gated behind a wallet-signed Terms-of-Service acceptance.
          </p>
        </div>
      </section>

      {/* Two ways to use it */}
      <section className="section">
        <h2>Two ways to use it</h2>
        <div className="grid">
          <div className="card">
            <div className="top"><div className="ico">🖥️</div><div><h3>System VPN — whole device</h3></div></div>
            <p>A real WireGuard tunnel for everything on the machine, with a fail-closed killswitch and a
              leak self-test that refuses to say &quot;protected&quot; until it passes.</p>
            <pre className="inline" style={{ display: 'block', padding: 12, borderRadius: 10, overflowX: 'auto', marginTop: 8 }}>
{`animica vpn exits
animica vpn up --region eu
animica vpn doctor
animica vpn down`}
            </pre>
          </div>
          <div className="card">
            <div className="top"><div className="ico">🧭</div><div><h3>Browser proxy — one browser</h3></div></div>
            <p>Install the extension, open the <b>VPN</b> tab, and click <b>Route</b> on a location. A{' '}
              <b>VPN</b> badge shows while active; <b>Stop</b> clears it. Base install asks only for the{' '}
              <code className="inline">proxy</code> permission — token exits request more only when you pick one.</p>
            <a className="btn primary" href="/browser" style={{ marginTop: 8 }}>Get the extension →</a>
          </div>
        </div>
      </section>

      {/* Live exit list */}
      <section className="section" id="exits">
        <h2>Choose an exit location</h2>
        <p className="muted" style={{ marginTop: -6, marginBottom: 14, fontSize: 13.5 }}>
          You pick the exit — the registry only ranks by load, reputation, and latency. It never forces one.
        </p>
        <VpnExits />
      </section>

      {/* Operator + rewards */}
      <section className="section">
        <h2>Run a relay, earn ANM</h2>
        <div className="grid">
          <div className="card">
            <div className="top"><div className="ico">📡</div><div><h3>Node operators run the relays</h3></div></div>
            <p>Exits are run by node operators. The daemon installs an <b>isolated</b> firewall table that
              blocks LAN / loopback / cloud-metadata / abuse ports and never touches your host policy, meters
              bytes per peer, and posts <b>two-sided signed</b> usage reports the registry reconciles (min of
              both counts; &gt;10% divergence flagged) so neither side can inflate a reward.</p>
          </div>
          <div className="card">
            <div className="top"><div className="ico">⛓️</div><div><h3>Paid from the block — at height 50,000</h3></div></div>
            <p>The <code className="inline">FORK_VPN_RELAY_REWARDS</code> consensus fork (forward-only,
              height-gated to mainnet block <b>50,000</b>) pays relays straight from the coinbase, carved from
              the miner&apos;s share — <b>never more than 50 ANM per block</b> to the whole pool, and the cap{' '}
              <b>decays with the halving</b>. It ships <b>inert</b> (byte-identical to no-fork, verified by three
              independent adversarial reviews); turning it live needs a sealed relay-contribution root and a
              fresh gated activation. Until then, rewards are off-chain IOUs.</p>
          </div>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginTop: 18 }}>
          <a className="btn" href="https://github.com/animicaorg/animica-core/blob/main/docs/VPN.md" target="_blank" rel="noreferrer">Operator guide →</a>
          <a className="btn" href="/browser">Get the extension</a>
        </div>
      </section>
    </div>
  );
}
