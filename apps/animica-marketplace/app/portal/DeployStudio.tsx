'use client';
import { useState } from 'react';
import PlanUpsell, { planLimitOf, type PlanLimitDetails } from '@/components/PlanUpsell';

// A real, end-to-end deploy flow for a native .anm site. It talks to the owner-authenticated API:
//   POST /api/mkt/v1/names            { name, years }         -> register (needs `names` scope)
//   POST /api/mkt/v1/names/<n>/publish { html }               -> publish native HTML (owner only)
// Auth is either the same-origin marketplace session (if the visitor is signed in) or a pasted
// anm_mkt_ API key with the `names` scope. We send credentials so a logged-in user needs no key.

const STARTER = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>my site on the Animica Internet</title>
  <style>
    body{margin:0;font:16px/1.6 system-ui,sans-serif;background:#0b1224;color:#e8ecf6;
      display:grid;place-items:center;min-height:100vh;text-align:center}
    h1{font-size:clamp(28px,6vw,44px);margin:.2em 0}
    .g{background:linear-gradient(110deg,#37e0d8,#6d5ef6);-webkit-background-clip:text;
      background-clip:text;color:transparent}
    a{color:#37e0d8}
  </style>
</head>
<body>
  <main>
    <h1>hello from <span class="g">the .anm web</span></h1>
    <p>This page is served from its content hash by the Animica network.</p>
    <p><a href="https://animica.dev/anm/animica">← back to animica.anm</a></p>
  </main>
</body>
</html>
`;

type Result =
  | { ok: true; fqdn: string; cid: string; size: number; gateway: string }
  // plan_limit (402) is a plan gate, NOT insufficient funds — it renders the upgrade CTA
  // instead of the generic error card (which would read like a deposit prompt).
  | { ok: false; error: string; planLimit?: { message: string; details: PlanLimitDetails } };

export default function DeployStudio() {
  const [name, setName] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [html, setHtml] = useState(STARTER);
  const [busy, setBusy] = useState<'' | 'register' | 'publish'>('');
  const [result, setResult] = useState<Result | null>(null);

  const clean = name.trim().toLowerCase().replace(/\.anm$/, '');
  const validName = /^[a-z0-9-]{2,63}$/.test(clean);

  function headers(json = true): Record<string, string> {
    const h: Record<string, string> = {};
    if (json) h['content-type'] = 'application/json';
    if (apiKey.trim()) h['authorization'] = `Bearer ${apiKey.trim()}`;
    return h;
  }

  async function call(path: string, body: any): Promise<any> {
    const r = await fetch(path, {
      method: 'POST',
      headers: headers(),
      credentials: 'include', // use the marketplace session if the visitor is signed in
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      // Keep the envelope's code/details on the thrown error so plan_limit (402) can render
      // an upgrade CTA instead of being mistaken for an insufficient-funds deposit prompt.
      const e: any = new Error(data?.error?.message || `${r.status} ${r.statusText}`);
      e.status = r.status;
      e.code = data?.error?.code;
      e.details = data?.error?.details;
      throw e;
    }
    return data;
  }

  function fail(e: any) {
    const pl = planLimitOf(e);
    setResult(pl ? { ok: false, error: pl.message, planLimit: pl } : { ok: false, error: e.message });
  }

  async function register() {
    if (!validName) return;
    setBusy('register'); setResult(null);
    try {
      await call('/api/mkt/v1/names', { name: clean, years: 1 });
      setResult({ ok: false, error: `Registered ${clean}.anm — now click Publish.` });
    } catch (e: any) {
      fail(e);
    } finally { setBusy(''); }
  }

  async function publish() {
    if (!validName) return;
    setBusy('publish'); setResult(null);
    try {
      const d = await call(`/api/mkt/v1/names/${encodeURIComponent(clean)}/publish`, { html });
      setResult({ ok: true, fqdn: d.fqdn, cid: d.cid, size: d.size, gateway: d.gateway || `/anm/${clean}` });
    } catch (e: any) {
      fail(e);
    } finally { setBusy(''); }
  }

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div style={{ display: 'grid', gap: 12 }}>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <label style={{ flex: '1 1 200px', display: 'grid', gap: 4 }}>
            <span className="muted" style={{ fontSize: 12.5 }}>Name</span>
            <div className="search" style={{ margin: 0 }}>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="mysite" aria-label=".anm name" />
              <span className="mono muted" style={{ fontSize: 13 }}>.anm</span>
            </div>
          </label>
          <label style={{ flex: '1 1 240px', display: 'grid', gap: 4 }}>
            <span className="muted" style={{ fontSize: 12.5 }}>API key (or leave blank if signed in)</span>
            <div className="search" style={{ margin: 0 }}>
              <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="anm_mkt_…" aria-label="API key"
                type="password" autoComplete="off" spellCheck={false} />
            </div>
          </label>
        </div>

        <label style={{ display: 'grid', gap: 4 }}>
          <span className="muted" style={{ fontSize: 12.5 }}>Your site — a single self-contained HTML file (≤ 2 MB)</span>
          <textarea value={html} onChange={(e) => setHtml(e.target.value)} spellCheck={false}
            style={{
              width: '100%', minHeight: 260, resize: 'vertical', background: '#060b18', color: '#cdd6ee',
              border: '1px solid var(--line, #1b2748)', borderRadius: 10, padding: 12,
              font: '12.5px/1.55 ui-monospace,Menlo,Consolas,monospace',
            }} />
        </label>

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <button className="btn" onClick={register} disabled={!validName || busy !== ''}>
            {busy === 'register' ? 'Registering…' : '1 · Register the name'}
          </button>
          <button className="btn primary" onClick={publish} disabled={!validName || busy !== ''}>
            {busy === 'publish' ? 'Publishing…' : '2 · Publish it'}
          </button>
          <span className="muted" style={{ fontSize: 12 }}>
            {validName ? <>Will publish to <code className="inline">{clean}.anm</code></> : 'Enter a name (a–z, 0–9, hyphens)'}
          </span>
        </div>

        {result && (result.ok ? (
          <div className="card" style={{ borderColor: 'var(--accent, #37e0d8)' }}>
            <b>✔ Published {result.fqdn}</b>
            <p className="muted" style={{ margin: '6px 0 0', fontSize: 13 }}>
              Served from content ID <code className="inline mono">{result.cid.slice(0, 20)}…</code> ({result.size} B).
            </p>
            <p style={{ margin: '8px 0 0' }}>
              <a className="btn primary" href={result.gateway} target="_blank" rel="noopener">Open {result.fqdn} →</a>
            </p>
          </div>
        ) : result.planLimit ? (
          <PlanUpsell message={result.planLimit.message} details={result.planLimit.details} style={{ marginTop: 0 }} />
        ) : (
          <div className="card" style={{ borderColor: '#3a2a3f' }}>
            <p style={{ margin: 0, fontSize: 13.5 }}>{result.error}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
