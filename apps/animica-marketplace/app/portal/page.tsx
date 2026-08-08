import type { Metadata } from 'next';
import { prisma } from '@/lib/db';
import BrowserSearch from '../browser/BrowserSearch';
import DeployStudio from './DeployStudio';

// The mobile-friendly portal to the Animica Internet: search + directory of every .anm site, get the
// browser extension, and a real deploy flow so anyone can publish their own native site. This is the
// page animica.anm points at, and the one animica.net features.

export const metadata: Metadata = {
  title: 'Animica Internet — portal',
  description:
    'The portal to the Animica Internet: search every .anm site, get the browser, and deploy your own native site in minutes.',
  alternates: { canonical: 'https://animica.dev/portal' },
  openGraph: {
    // Next replaces the parent's whole `openGraph` object when a page declares its
    // own, so the layout's default image does NOT survive here — it has to be named.
    images: ['/og.png'],
    title: 'Animica Internet — portal',
    description: 'Search the .anm web, get the browser, and deploy your own native site.',
    url: 'https://animica.dev/portal',
    siteName: 'Animica',
    type: 'website',
  },
};

export const dynamic = 'force-dynamic';

async function load(): Promise<{ rows: any[]; total: number }> {
  try {
    const [rows, total] = await Promise.all([
      prisma.anmDomain.findMany({
        where: { status: 'ACTIVE' },
        select: { name: true, kind: true, recordsJson: true, contentCid: true },
        orderBy: { name: 'asc' }, take: 48,
      }),
      prisma.anmDomain.count({ where: { status: 'ACTIVE' } }),
    ]);
    return { rows, total };
  } catch {
    return { rows: [], total: 0 };
  }
}

function rec(r: any): any { try { return JSON.parse(r.recordsJson) || {}; } catch { return {}; } }
function avatarOf(r: any): string { return rec(r).avatar || (r.kind === 'agent' ? '🤖' : r.kind === 'ai' ? '✨' : '🌐'); }
function descOf(r: any): string { return rec(r).description || `A ${r.kind || 'site'} on the Animica Internet.`; }

const lessons: [string, JSX.Element][] = [
  ['Get an ANM wallet', <>You publish and own names with a post-quantum ANM wallet. Open <a className="inline" href="/anm/wallet">wallet.anm</a> — keys are generated in your browser and never leave it.</>],
  ['Register a name', <>Claim <code className="inline">yours.anm</code> — there&apos;s no registrar. Use the studio below, or <code className="inline">POST /api/mkt/v1/names</code> with your key. It&apos;s yours until it expires.</>],
  ['Write one HTML file', <>A native site is a single self-contained <code className="inline">.html</code> (≤ 2 MB): inline your CSS &amp; JS, embed images as data URIs. External requests are blocked by the sandbox, so keep it self-contained.</>],
  ['Publish it', <>Publish below, or <code className="inline">POST /api/mkt/v1/names/&lt;name&gt;/publish</code>. The network stores it by content hash and serves it, hash-verified, at <code className="inline">animica.dev/anm/&lt;name&gt;</code>.</>],
];

export default async function PortalPage() {
  const { rows, total } = await load();

  return (
    <div className="wrap" style={{ paddingTop: 40 }}>
      {/* hero */}
      <div style={{ textAlign: 'center', maxWidth: 720, margin: '0 auto' }}>
        <div className="pill" style={{ marginBottom: 14 }}>🌐 the Animica Internet · {total} sites live</div>
        <h1 style={{ fontSize: 44, letterSpacing: '-0.035em', lineHeight: 1.05 }}>
          The portal to the{' '}
          <span style={{ background: 'linear-gradient(120deg,var(--accent-2),var(--accent))', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' }}>.anm</span>{' '}
          web
        </h1>
        <p className="muted" style={{ fontSize: 17, marginTop: 12 }}>
          Search every name, app, agent and AI service on the Animica network — owned by wallets, resolved by
          nodes, no registrar. Then <b>deploy your own</b> in minutes.
        </p>
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 12, marginTop: 22 }}>
          <a className="btn primary" href="/anm/animica">Open animica.anm →</a>
          <a className="btn" href="#deploy">Deploy your own site</a>
          <a className="btn" href="/browser">Get the browser</a>
        </div>
      </div>

      {/* live search + directory */}
      <BrowserSearch initial={rows} total={total} />

      <section className="section" id="explore">
        <h2>Everything on the Animica Internet</h2>
        <div className="grid">
          {rows.map((r) => (
            <a className="card" key={r.name} href={`/anm/${r.name}`}>
              <div className="top">
                <div className="ico">{avatarOf(r)}</div>
                <div>
                  <h3 className="mono" style={{ color: 'var(--accent)' }}>{r.name}.anm</h3>
                </div>
                {r.contentCid ? <span className="pill" style={{ marginLeft: 'auto', color: 'var(--accent)' }}>native</span> : null}
              </div>
              <p>{descOf(r)}</p>
            </a>
          ))}
          {rows.length === 0 && <div className="empty">The index is warming up…</div>}
        </div>
      </section>

      {/* browser extension */}
      <section className="section">
        <h2>Browse it your way</h2>
        <div className="grid">
          <div className="card"><div className="top"><div className="ico">🧭</div><div><h3>Zero install</h3></div></div>
            <p>Every <code className="inline">.anm</code> name already works at <code className="inline">animica.dev/anm/&lt;name&gt;</code> in any browser — nothing to install.</p></div>
          <div className="card"><div className="top"><div className="ico">🧩</div><div><h3>Address-bar extension</h3></div></div>
            <p>Add the Chromium extension to type <b>name.anm</b> straight in the address bar and search the whole index. <a className="inline" href="/browser">Install →</a></p></div>
          <div className="card"><div className="top"><div className="ico">🛡️</div><div><h3>Private browsing</h3></div></div>
            <p>The extension can also route your browser through a <a className="inline" href="/anm/vpn">dVPN</a> exit you pick — relays earn ANM.</p></div>
          <div className="card"><div className="top"><div className="ico">🤖</div><div><h3>Agent-native</h3></div></div>
            <p>Every resolution is a public API call, so autonomous <a className="inline" href="/anm/agents">agents</a> roam the same internet you do.</p></div>
        </div>
      </section>

      {/* deploy your own */}
      <section className="section" id="deploy">
        <h2>Deploy your own .anm site</h2>
        <p className="muted" style={{ marginTop: -6, marginBottom: 14, fontSize: 13.5 }}>
          A native site is one self-contained HTML file the network serves for you, hash-verified. Follow the four
          steps, then publish it right here. Prefer the CLI or an agent? See the <a className="inline" href="/docs#deploy">docs</a>.
        </p>
        <div className="grid" style={{ marginBottom: 14 }}>
          {lessons.map(([t, d], i) => (
            <div className="card" key={t}>
              <div className="top"><div className="ico">{i + 1}</div><div><h3>{t}</h3></div></div>
              <p>{d}</p>
            </div>
          ))}
        </div>
        <DeployStudio />
      </section>

      <section className="section" style={{ textAlign: 'center' }}>
        <p className="muted" style={{ fontSize: 13.5 }}>
          New here? Start at <a className="inline" href="/docs">the docs</a> · get a <a className="inline" href="/anm/wallet">wallet</a> ·
          browse the <a className="inline" href="/marketplace">marketplace</a> · read about the <a className="inline" href="/anm/vpn">dVPN</a>.
        </p>
      </section>
    </div>
  );
}
