'use client';
import { useEffect, useState } from 'react';
import { connectAndLogin, hasWallet } from '@/components/wallet';

export default function MyAI() {
  const [address, setAddress] = useState('');
  const [balance, setBalance] = useState<string | null>(null);
  const [purchases, setPurchases] = useState<any[]>([]);
  const [deposit, setDeposit] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const b = await fetch('/api/mkt/v1/balance').then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (b) { setAddress(b.address); setBalance(b.balanceAnm); }
    const p = await fetch('/api/mkt/v1/me/purchases').then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (p?.purchases) setPurchases(p.purchases);
  }
  useEffect(() => { refresh(); }, []);

  async function connect() {
    setErr(''); setBusy(true);
    try { const { address } = await connectAndLogin(); setAddress(address); await refresh(); }
    catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }
  async function getDeposit() {
    setBusy(true); setErr('');
    try {
      const r = await fetch('/api/mkt/v1/deposits/address', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
      const d = await r.json();
      if (!r.ok) throw new Error(d?.error?.message ?? 'failed');
      setDeposit(d.address);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }

  return (
    <div className="wrap" style={{ paddingTop: 40 }}>
      <h1 style={{ fontSize: 32, letterSpacing: '-0.03em' }}>My AI</h1>
      <p className="muted">Your library, balance, and deposits.</p>

      {!balance ? (
        <div className="panel" style={{ marginTop: 20, textAlign: 'center' }}>
          <p className="muted">{hasWallet() ? 'Connect your Animica wallet to continue.' : 'Install the Animica wallet extension to sign in.'}</p>
          <button className="btn primary" onClick={connect} disabled={busy}>{busy ? 'Connecting…' : 'Connect Wallet'}</button>
          {err && <div style={{ color: 'var(--bad)', marginTop: 10, fontSize: 13 }}>{err}</div>}
        </div>
      ) : (
        <>
          <div className="panel" style={{ marginTop: 20 }}>
            <div className="kpi">
              <div className="k"><b>{balance} ANM</b><span>ledger balance</span></div>
              <div className="k"><b className="mono" style={{ fontSize: 15 }}>{address.slice(0, 18)}…</b><span>wallet</span></div>
              <div className="k"><b>{purchases.length}</b><span>purchases</span></div>
            </div>
            <div style={{ marginTop: 16, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <button className="btn" onClick={getDeposit} disabled={busy}>Get deposit address</button>
              <a className="btn ghost" href="/api/mkt/v1/ledger" target="_blank">View ledger</a>
            </div>
            {deposit && (
              <div style={{ marginTop: 14 }}>
                <div className="muted" style={{ fontSize: 13 }}>Send ANM here to top up (credited after finality):</div>
                <code className="inline">{deposit}</code>
              </div>
            )}
            {err && <div style={{ color: 'var(--bad)', marginTop: 10, fontSize: 13 }}>{err}</div>}
          </div>

          <h2 style={{ marginTop: 30, fontSize: 20 }}>Your AI</h2>
          {purchases.length === 0 ? (
            <div className="empty">No purchases yet. <a href="/marketplace" className="mono">Explore the marketplace →</a></div>
          ) : (
            <div className="grid">
              {purchases.map((p: any) => (
                <a className="card" key={p.id} href={`/marketplace/${p.listing?.slug ?? ''}`}>
                  <h3>{p.listing?.name ?? 'AI'}</h3>
                  <p className="muted">{p.priceModel} · {p.status}</p>
                </a>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
