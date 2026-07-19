'use client';
import { useCallback, useEffect, useState } from 'react';
import { api, fmtDate, inputStyle, labelStyle, Msg } from '../ui';

// API keys — list, mint and revoke anm_mkt_ keys. A key with the 'publish' scope drives the same
// store API this console uses (create listings, upload builds, edit prices) for CI / automation.
// Data: GET /keys ; POST /keys {name, scopes} (raw shown once) ; DELETE /keys?id=.
//
// A devportal session can only grant the scopes it holds — read, publish, withdraw — so those are
// the developer-relevant scopes offered here (the server rejects any escalation beyond them).

const GRANTABLE: { scope: string; label: string; hint: string }[] = [
  { scope: 'read', label: 'read', hint: 'read catalog, balance, own listings' },
  { scope: 'publish', label: 'publish', hint: 'create listings, upload builds, edit prices/assets' },
  { scope: 'withdraw', label: 'withdraw', hint: 'request on-chain payouts of earnings' },
];

function Mint({ onMinted }: { onMinted: () => void }) {
  const [name, setName] = useState('');
  const [scopes, setScopes] = useState<Record<string, boolean>>({ read: true, publish: true, withdraw: false });
  const [busy, setBusy] = useState(false);
  const [raw, setRaw] = useState('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function mint() {
    setBusy(true); setMsg(null); setRaw('');
    try {
      const chosen = GRANTABLE.map((g) => g.scope).filter((s) => scopes[s]);
      if (chosen.length === 0) { setMsg({ ok: false, text: 'select at least one scope' }); setBusy(false); return; }
      const d = await api('/api/mkt/v1/keys', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name: name.trim() || 'key', scopes: chosen }),
      });
      setRaw(d.apiKey);
      setName('');
      onMinted();
    } catch (e: any) { setMsg({ ok: false, text: e.message }); }
    finally { setBusy(false); }
  }

  return (
    <section className="panel">
      <h3 style={{ margin: '0 0 14px', fontSize: 17 }}>Mint a key</h3>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 14, alignItems: 'start' }}>
        <div>
          <label style={labelStyle}>Name</label>
          <input style={inputStyle} value={name} onChange={(e) => setName(e.target.value)} placeholder="ci-publisher" />
        </div>
        <div>
          <label style={labelStyle}>Scopes</label>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 2 }}>
            {GRANTABLE.map((g) => (
              <label key={g.scope} style={{ display: 'flex', alignItems: 'baseline', gap: 8, fontSize: 13.5, cursor: 'pointer' }}>
                <input type="checkbox" checked={!!scopes[g.scope]} onChange={(e) => setScopes((s) => ({ ...s, [g.scope]: e.target.checked }))} />
                <span className="mono">{g.label}</span>
                <span className="muted" style={{ fontSize: 12 }}>— {g.hint}</span>
              </label>
            ))}
          </div>
        </div>
      </div>
      <div style={{ marginTop: 14 }}>
        <button className="btn primary" onClick={mint} disabled={busy}>{busy ? 'Minting…' : 'Mint key'}</button>
        <Msg msg={msg} />
      </div>

      {raw && (
        <div style={{ marginTop: 16, border: '1px solid var(--good)', borderRadius: 10, padding: 14, background: 'rgba(36,209,139,0.06)' }}>
          <div style={{ color: 'var(--good)', fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
            Copy this key now — it is shown only once and cannot be retrieved later.
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <code className="inline mono" style={{ fontSize: 12.5, wordBreak: 'break-all', flex: 1, minWidth: 240 }}>{raw}</code>
            <button className="btn" style={{ fontSize: 13 }} onClick={() => navigator.clipboard?.writeText(raw).catch(() => {})}>Copy</button>
            <button className="btn ghost" style={{ fontSize: 13 }} onClick={() => setRaw('')}>Done</button>
          </div>
        </div>
      )}
    </section>
  );
}

export default function Keys() {
  const [keys, setKeys] = useState<any[] | null>(null);
  const [err, setErr] = useState('');
  const [busyId, setBusyId] = useState('');

  const load = useCallback(async () => {
    setErr('');
    try { const d = await api('/api/mkt/v1/keys'); setKeys(d.keys ?? []); }
    catch (e: any) { setErr(e.message); setKeys([]); }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function revoke(id: string) {
    setBusyId(id);
    try { await api(`/api/mkt/v1/keys?id=${encodeURIComponent(id)}`, { method: 'DELETE' }); await load(); }
    catch (e: any) { setErr(e.message); }
    finally { setBusyId(''); }
  }

  return (
    <div>
      <h1 style={{ fontSize: 28, letterSpacing: '-0.03em', margin: '0 0 4px' }}>API keys</h1>
      <p className="muted" style={{ fontSize: 14, margin: '0 0 18px' }}>
        Bearer <code className="inline">anm_mkt_</code> keys for automation. Send them as <code className="inline">Authorization: Bearer …</code> to the <a className="inline" href="/docs">store API</a>.
      </p>

      <Mint onMinted={load} />

      <section className="section">
        <h2>Your keys</h2>
        {err && <div className="panel" style={{ borderColor: 'rgba(255,92,114,0.4)', color: 'var(--bad)', marginBottom: 12 }}>{err}</div>}
        {keys == null ? (
          <div className="empty">Loading…</div>
        ) : keys.length === 0 ? (
          <div className="empty">No active keys.</div>
        ) : (
          <div className="panel" style={{ padding: 0 }}>
            {keys.map((k) => (
              <div key={k.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '13px 18px', borderTop: '1px solid var(--border)', flexWrap: 'wrap' }}>
                <div style={{ minWidth: 160 }}>
                  <div style={{ fontWeight: 600, fontSize: 14 }}>{k.name}</div>
                  <code className="inline mono" style={{ fontSize: 11.5 }}>{k.prefix}…</code>
                </div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {(k.scopes ?? []).map((s: string) => <span key={s} className="pill mono" style={{ fontSize: 11 }}>{s}</span>)}
                </div>
                <span className="muted" style={{ fontSize: 12 }}>
                  {k.lastUsedAt ? `used ${fmtDate(k.lastUsedAt)}` : 'never used'}
                </span>
                <button
                  className="btn ghost"
                  onClick={() => revoke(k.id)}
                  disabled={busyId === k.id}
                  style={{ marginLeft: 'auto', color: 'var(--bad)', fontSize: 13, padding: '7px 12px' }}
                >
                  {busyId === k.id ? 'Revoking…' : 'Revoke'}
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
