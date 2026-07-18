'use client';
import { useEffect, useState } from 'react';
import { connectAndLogin, hasWallet } from '@/components/wallet';

const CATS = ['Coding', 'Business', 'Research', 'Education', 'Marketing', 'Finance', 'Science', 'Gaming', 'Personal', 'Enterprise'];
const TYPES = [
  { v: 'RAG_ASSISTANT', l: 'RAG Assistant' }, { v: 'AGENT', l: 'AI Agent' },
  { v: 'WORKFLOW', l: 'Workflow' }, { v: 'KNOWLEDGE_AI', l: 'Knowledge AI' }, { v: 'MEDIA', l: 'Generative Media' },
];

export default function Studio() {
  const [address, setAddress] = useState('');
  const [mine, setMine] = useState<any[]>([]);
  const [earnings, setEarnings] = useState<any>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ name: '', type: 'RAG_ASSISTANT', category: 'Personal', tagline: '', description: '', systemPrompt: '', priceModel: 'FREE', priceAnm: '' });

  async function refresh() {
    const b = await fetch('/api/mkt/v1/balance').then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (b) setAddress(b.address);
    const e = await fetch('/api/mkt/v1/me/earnings').then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (e) { setMine(e.listings ?? []); setEarnings(e.summary ?? null); }
  }
  useEffect(() => { refresh(); }, []);

  async function connect() {
    setErr(''); setBusy(true);
    try { const { address } = await connectAndLogin(); setAddress(address); await refresh(); }
    catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }

  async function create() {
    setErr(''); setBusy(true);
    try {
      const prices: any[] = [];
      if (form.priceModel === 'FREE') prices.push({ model: 'FREE' });
      else if (form.priceModel === 'USAGE') prices.push({ model: 'USAGE', perCallNanm: String(BigInt(Math.round(Number(form.priceAnm || '0') * 1e9))) });
      else prices.push({ model: form.priceModel, amountNanm: String(BigInt(Math.round(Number(form.priceAnm || '0') * 1e9))) });
      const res = await fetch('/api/mkt/v1/listings', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ ...form, prices }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d?.error?.message ?? 'create failed');
      // Auto-publish so it appears immediately.
      await fetch(`/api/mkt/v1/listings/${d.listing.slug}/publish`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
      window.location.href = `/marketplace/${d.listing.slug}`;
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }

  const set = (k: string) => (e: any) => setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <div className="wrap" style={{ paddingTop: 40 }}>
      <h1 style={{ fontSize: 32, letterSpacing: '-0.03em' }}>Creator Studio</h1>
      <p className="muted">Build an AI, set ANM pricing, publish, and earn. Agents can do all of this via <code className="inline">/api/mkt/v1</code>.</p>

      {!address ? (
        <div className="panel" style={{ marginTop: 20, textAlign: 'center' }}>
          <p className="muted">{hasWallet() ? 'Connect your wallet to start creating.' : 'Install the Animica wallet extension to create.'}</p>
          <button className="btn primary" onClick={connect} disabled={busy}>{busy ? 'Connecting…' : 'Connect Wallet'}</button>
          {err && <div style={{ color: 'var(--bad)', marginTop: 10, fontSize: 13 }}>{err}</div>}
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 320px', gap: 24, marginTop: 20 }}>
          <div className="panel">
            <h2 style={{ marginTop: 0, fontSize: 18 }}>Create AI</h2>
            <label className="muted" style={{ fontSize: 13 }}>Name</label>
            <input className="fld" value={form.name} onChange={set('name')} placeholder="Python Expert AI" />
            <div style={{ display: 'flex', gap: 10 }}>
              <div style={{ flex: 1 }}>
                <label className="muted" style={{ fontSize: 13 }}>Type</label>
                <select className="fld" value={form.type} onChange={set('type')}>{TYPES.map((t) => <option key={t.v} value={t.v}>{t.l}</option>)}</select>
              </div>
              <div style={{ flex: 1 }}>
                <label className="muted" style={{ fontSize: 13 }}>Category</label>
                <select className="fld" value={form.category} onChange={set('category')}>{CATS.map((c) => <option key={c}>{c}</option>)}</select>
              </div>
            </div>
            <label className="muted" style={{ fontSize: 13 }}>Tagline</label>
            <input className="fld" value={form.tagline} onChange={set('tagline')} placeholder="Explains async programming clearly" />
            <label className="muted" style={{ fontSize: 13 }}>Description</label>
            <textarea className="fld" rows={3} value={form.description} onChange={set('description')} />
            <label className="muted" style={{ fontSize: 13 }}>System instructions</label>
            <textarea className="fld" rows={4} value={form.systemPrompt} onChange={set('systemPrompt')} placeholder="You are an expert Python tutor…" />
            <div style={{ display: 'flex', gap: 10 }}>
              <div style={{ flex: 1 }}>
                <label className="muted" style={{ fontSize: 13 }}>Pricing</label>
                <select className="fld" value={form.priceModel} onChange={set('priceModel')}>
                  <option value="FREE">Free</option><option value="ONE_TIME">One-time</option>
                  <option value="SUBSCRIPTION">Subscription</option><option value="USAGE">Pay-per-use</option>
                </select>
              </div>
              {form.priceModel !== 'FREE' && (
                <div style={{ flex: 1 }}>
                  <label className="muted" style={{ fontSize: 13 }}>Amount (ANM)</label>
                  <input className="fld" value={form.priceAnm} onChange={set('priceAnm')} placeholder="25" />
                </div>
              )}
            </div>
            <button className="btn primary" style={{ marginTop: 16 }} onClick={create} disabled={busy || !form.name}>{busy ? 'Publishing…' : 'Create & Publish'}</button>
            {err && <div style={{ color: 'var(--bad)', marginTop: 10, fontSize: 13 }}>{err}</div>}
          </div>

          <aside>
            <div className="panel">
              <h2 style={{ marginTop: 0, fontSize: 16 }}>Earnings</h2>
              <div className="kpi" style={{ flexDirection: 'column', gap: 12 }}>
                <div className="k"><b>{earnings?.earnedAnm ?? '0'} ANM</b><span>lifetime earned</span></div>
                <div className="k"><b>{earnings?.sales ?? 0}</b><span>sales</span></div>
                <div className="k"><b>{mine.length}</b><span>listings</span></div>
              </div>
              <a className="btn ghost" style={{ marginTop: 12, width: '100%', justifyContent: 'center' }} href="/api/mkt/v1/me/earnings" target="_blank">Earnings JSON</a>
            </div>
          </aside>
        </div>
      )}
      <style>{`.fld{width:100%;background:var(--bg-elev);border:1px solid var(--border);border-radius:10px;color:var(--text);padding:10px 12px;font-size:14px;margin:4px 0 12px;font-family:inherit}.fld:focus{outline:none;border-color:var(--accent)}`}</style>
    </div>
  );
}
