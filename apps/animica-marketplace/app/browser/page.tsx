import type { Metadata } from 'next';
import { prisma } from '@/lib/db';
import BrowserSearch from './BrowserSearch';

// The Animica Browser landing: leads with the ZERO-INSTALL gateway path (every .anm name already
// works at animica.dev/anm/<name>), then offers the optional extension for in-address-bar resolution
// + index search. Server-rendered so the name count is real on first paint and the page has its own
// share metadata.

export const metadata: Metadata = {
  title: 'Animica Browser — browse the .anm internet',
  description:
    'Open any .anm name instantly at animica.dev/anm/<name> — no install needed. Or add the optional Chromium extension to resolve *.anm and search the whole index from your address bar.',
  alternates: { canonical: 'https://animica.dev/browser' },
  openGraph: {
    // Next replaces the parent's whole `openGraph` object when a page declares its
    // own, so the layout's default image does NOT survive here — it has to be named.
    images: ['/og.png'],
    title: 'Animica Browser — browse the .anm internet',
    description:
      'Every .anm name works right now, no install. Optional extension resolves *.anm and searches the index from your address bar.',
    url: 'https://animica.dev/browser',
    siteName: 'Animica',
    type: 'website',
  },
  twitter: { card: 'summary_large_image', title: 'Animica Browser — browse the .anm internet' },
};

export const dynamic = 'force-dynamic';

async function initialNames(): Promise<{ rows: any[]; total: number }> {
  try {
    const [rows, total] = await Promise.all([
      prisma.anmDomain.findMany({ where: { status: 'ACTIVE' }, select: { name: true, kind: true, recordsJson: true }, take: 24 }),
      prisma.anmDomain.count({ where: { status: 'ACTIVE' } }),
    ]);
    return { rows, total };
  } catch {
    return { rows: [], total: 0 };
  }
}

const steps: [string, JSX.Element][] = [
  ['Download & unzip', <>Download the package, then <b>right-click → Extract All</b> (Windows) or <b>double-click</b> (Mac). You must extract it — Chrome cannot load a <code className="inline">.zip</code>. Put the folder somewhere permanent (e.g. Documents), <b>not</b> Downloads.</>],
  ['Open extensions', <>Copy <code className="inline">chrome://extensions</code> and paste it into your address bar (browsers block links to it), press Enter, then turn on <b>Developer mode</b> — the toggle is at the top-right.</>],
  ['Load unpacked', <>Click <b>Load unpacked</b> and select the <b>unzipped folder</b> (not the zip). Keep that folder — Chrome loads the extension directly from it, so moving or deleting it breaks the extension.</>],
  ['Browse .anm', <>Type any <b>name.anm</b> in your address bar to visit it. To search the whole index, type <code className="inline">anm</code> then press <b>Tab</b>, then your query.</>],
];

export default async function BrowserPage() {
  const { rows, total } = await initialNames();

  return (
    <div className="wrap" style={{ paddingTop: 44 }}>
      <div style={{ textAlign: 'center', maxWidth: 720, margin: '0 auto' }}>
        <div className="pill" style={{ marginBottom: 14 }}>🧭 Animica Internet · the .anm namespace</div>
        <h1 style={{ fontSize: 44, letterSpacing: '-0.035em', lineHeight: 1.05 }}>
          Browse the <span style={{ background: 'linear-gradient(120deg,var(--accent-2),var(--accent))', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' }}>.anm</span> internet
        </h1>
        <p className="muted" style={{ fontSize: 17, marginTop: 12 }}>
          <b>No install needed</b> — every <code className="inline">.anm</code> name already works right now at{' '}
          <code className="inline">animica.dev/anm/&lt;name&gt;</code>. Want it in your address bar? Add the optional
          extension to resolve <b>*.anm</b> and search the whole index like Google. <b>{total}</b> names online.
        </p>
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 12, marginTop: 22 }}>
          <a className="btn primary" href="/anm/market">Open market.anm now →</a>
          <a className="btn" href="/api/mkt/v1/browser/download">↓ Get the extension (optional)</a>
        </div>
        <div className="muted" style={{ fontSize: 12.5, marginTop: 10 }}>
          Zero-install gateway works in any browser. Extension: Chrome · Edge · Brave · any Chromium browser.{' '}
          <b>Firefox &amp; Safari:</b> the extension isn&apos;t supported yet — but you don&apos;t need it; the gateway links work today.
        </div>
      </div>

      {/* Live search demo (server-seeded, no empty flash) */}
      <BrowserSearch initial={rows} total={total} />

      {/* Install steps */}
      <section className="section">
        <h2>Add the extension (optional) — 4 steps</h2>
        <p className="muted" style={{ marginTop: -6, marginBottom: 14, fontSize: 13.5 }}>
          The extension isn&apos;t in the Chrome Web Store yet, so it installs in Developer mode. Chrome shows a
          &quot;disable developer mode extensions&quot; notice on startup — that&apos;s expected, and you can dismiss it.
        </p>
        <div className="grid">
          {steps.map(([t, d], i) => (
            <div className="card" key={t}>
              <div className="top">
                <div className="ico">{i + 1}</div>
                <div><h3>{t}</h3></div>
              </div>
              <p>{d}</p>
            </div>
          ))}
        </div>
      </section>

      {/* What it does */}
      <section className="section">
        <h2>What it does</h2>
        <div className="grid">
          <div className="card"><div className="top"><div className="ico">🌐</div><div><h3>Resolve .anm</h3></div></div><p>Typing <b>name.anm</b> in the address bar opens it via the Animica gateway — hosted sites, agent endpoints, and AI services, all under one sovereign namespace. No extension? The same names open at <code className="inline">animica.dev/anm/&lt;name&gt;</code>.</p></div>
          <div className="card"><div className="top"><div className="ico">🔎</div><div><h3>Search like Google</h3></div></div><p>The <b>anm</b> omnibox keyword and popup search the full index of registered names, agents, and services — ranked and instant.</p></div>
          <div className="card"><div className="top"><div className="ico">🔒</div><div><h3>Safe by design</h3></div></div><p>Hosted content is address-verified against its content hash and served in an isolated sandbox, so a <b>.anm</b> site can never touch your Animica session.</p></div>
          <div className="card"><div className="top"><div className="ico">🤖</div><div><h3>Agent-native</h3></div></div><p>Every resolution is a public API call, so autonomous agents roam the same internet you do — registering names, hosting content, and discovering each other.</p></div>
        </div>
      </section>
    </div>
  );
}
