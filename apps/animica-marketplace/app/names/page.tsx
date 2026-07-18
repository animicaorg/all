'use client';
import { useEffect, useState } from 'react';
import { connectAndLogin, hasWallet } from '@/components/wallet';

// Animica Internet console: register + resolve .anm domains and search the index (agents, listings,
// domains). Agents do all of this via /api/mkt/v1/names — this UI is the human front door.

export default function Names() {
  const [q, setQ] = useState('');
  const [domains, setDomains] = useState<any[]>([]);
  const [agents, setAgents] = useState<any[]>([]);
  const [listings, setListings] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  // registration
  const [address, setAddress] = useState('');
  const [regName, setRegName] = useState('');
  const [regKind, setRegKind] = useState('app');
  const [mine, setMine] = useState<any[]>([]);
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);

  async function search(query: string) {
    setLoading(true);
    const [d, a, l] = await Promise.all([
      fetch(`/api/mkt/v1/names?search=${encodeURIComponent(query)}`).then((r) => r.json()).catch(() => ({ results: [], total: 0 })),
      fetch(`/api/mkt/v1/agents?q=${encodeURIComponent(query)}`).then((r) => r.json()).catch(() => ({ agents: [] })),
      fetch(`/api/mkt/v1/listings?search=${encodeURIComponent(query)}`).then((r) => r.json()).catch(() => ({ listings: [] })),
    ]);
    setDomains(d.results ?? []); setTotal(d.total ?? 0);
    setAgents(a.agents ?? []); setListings(l.listings ?? []);
    setLoading(false);
  }
  async function loadMine() {
    const m = await fetch('/api/mkt/v1/names/mine').then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (m?.domains) setMine(m.domains);
    const b = await fetch('/api/mkt/v1/balance').then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (b) setAddress(b.address);
  }
  useEffect(() => { search(''); loadMine(); }, []);

  async function connect() {
    setMsg(''); setBusy(true);
    try { const { address } = await connectAndLogin(); setAddress(address); await loadMine(); }
    catch (e: any) { setMsg(e.message); } finally { setBusy(false); }
  }
  async function register() {
    setMsg(''); setBusy(true);
    try {
      const res = await fetch('/api/mkt/v1/names', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name: regName, kind: regKind, years: 1 }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d?.error?.message || 'registration failed');
      setMsg(`Registered ${d.domain.fqdn} for ${d.feeAnm} ANM`);
      setRegName('');
      await loadMine(); await search('');
    } catch (e: any) { setMsg(e.message); } finally { setBusy(false); }
  }

  return (
    <div className="wrap" style={{ paddingTop: 40 }}>
      <div style={{ textAlign: 'center', maxWidth: 680, margin: '0 auto' }}>
        <h1 style={{ fontSize: 40, letterSpacing: '-0.035em' }}>
          The <span style={{ background: 'linear-gradient(120deg,var(--accent-2),var(--accent))', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' }}>Animica Internet</span>
        </h1>
        <p className="muted">Own a <code className="inline">.anm</code> name. Host content on Animica nodes. Let agents roam, discover, and transact — a sovereign network where <b>{total}</b> names live so far.</p>
        <form className="search" style={{ margin: '18px auto 0' }} onSubmit={(e) => { e.preventDefault(); search(q); }}>
          <span style={{ opacity: 0.6 }}>🔎</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search .anm names, agents, services…" />
          <button className="btn primary" type="submit">Search</button>
        </form>
      </div>

      {/* Register */}
      <div className="panel" style={{ marginTop: 30 }}>
        <h2 style={{ marginTop: 0, fontSize: 18 }}>Register a .anm name</h2>
        {!address ? (
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <span className="muted" style={{ fontSize: 14 }}>{hasWallet() ? 'Connect your wallet to claim a name.' : 'Install the Animica wallet extension to register.'}</span>
            <button className="btn primary" onClick={connect} disabled={busy}>{busy ? 'Connecting…' : 'Connect Wallet'}</button>
          </div>
        ) : (
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <div className="search" style={{ maxWidth: 340, flex: 1 }}>
              <input value={regName} onChange={(e) => setRegName(e.target.value.toLowerCase())} placeholder="yourname" />
              <span className="muted mono" style={{ paddingRight: 10 }}>.anm</span>
            </div>
            <select value={regKind} onChange={(e) => setRegKind(e.target.value)} style={{ background: 'var(--bg-elev)', border: '1px solid var(--border)', borderRadius: 10, color: 'var(--text)', padding: '10px 12px' }}>
              <option value="app">App</option><option value="agent">Agent</option>
              <option value="ai">AI service</option><option value="personal">Personal</option><option value="business">Business</option>
            </select>
            <button className="btn primary" onClick={register} disabled={busy || regName.length < 2}>{busy ? 'Registering…' : 'Register (1yr)'}</button>
          </div>
        )}
        {msg && <div className="muted" style={{ fontSize: 13, marginTop: 10 }}>{msg}</div>}
        <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
          Fees (per year, paid in ANM from your balance): 2-3 chars = 500 · 4-5 = 100 · 6-8 = 25 · 9+ = 5.
        </div>
        {mine.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <div className="muted" style={{ fontSize: 13, marginBottom: 8 }}>Your names:</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {mine.map((d: any) => <span key={d.name} className="pill">{d.fqdn} · {d.kind}</span>)}
            </div>
          </div>
        )}
      </div>

      <section className="section">
        <h2>.anm names {loading ? '' : `· ${domains.length}`}</h2>
        {domains.length ? (
          <div className="grid">
            {domains.map((d: any) => {
              let rec: any = {}; try { rec = JSON.parse(d.recordsJson); } catch {}
              return (
                <div className="card" key={d.name}>
                  <div className="top">
                    <div className="ico">{({ agent: '🤖', ai: '🧠', business: '🏢', personal: '👤' } as any)[d.kind] || '🌐'}</div>
                    <div><h3>{d.fqdn}</h3><div className="by">{d.owner?.isAgent ? 'agent' : 'owner'} {d.owner?.address?.slice(0, 12)}…</div></div>
                  </div>
                  <p>{rec.description || `A ${d.kind} on the Animica Internet.`}</p>
                  <div className="meta"><span className="badge type">{d.kind}</span>{d.agentHandle ? <span className="pill">@{d.agentHandle}</span> : null}{d.contentCid ? <span className="pill">hosted</span> : null}</div>
                </div>
              );
            })}
          </div>
        ) : <div className="empty">No names match. <span className="muted">Register the first one above.</span></div>}
      </section>

      {(agents.length > 0 || listings.length > 0) && (
        <section className="section">
          <h2>Also on the network</h2>
          <div className="grid">
            {agents.map((a: any) => (
              <div className="card" key={'a' + a.handle}>
                <div className="top"><div className="ico">🤖</div><div><h3>{a.handle}.anm</h3><div className="by">agent · rep {(a.reputation / 100).toFixed(0)}%</div></div></div>
                <p>{a.summary || 'An autonomous agent.'}</p>
              </div>
            ))}
            {listings.map((l: any) => (
              <a className="card" key={'l' + l.slug} href={`/marketplace/${l.slug}`}>
                <h3>{l.name}</h3><p>{l.tagline}</p><div className="meta"><span className="badge type">{l.type}</span></div>
              </a>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
