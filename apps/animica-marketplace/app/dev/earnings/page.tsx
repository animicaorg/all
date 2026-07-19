'use client';
import { useCallback, useEffect, useState } from 'react';
import { api, anmToNanm, fmtAnm, fmtDate, StatusChip, inputStyle, labelStyle, Msg } from '../ui';

// Earnings — lifetime creator revenue, per-listing breakdown, marketplace balance and on-chain
// withdrawals. Data:
//   GET  /api/mkt/v1/me/earnings?byListing=1   summary + per-listing (SALE_CREDIT / FORK_ROYALTY)
//   GET  /api/mkt/v1/balance                   withdrawable ledger balance
//   GET  /api/mkt/v1/withdrawals               payout history
//   POST /api/mkt/v1/withdrawals               request an on-chain payout (scope 'withdraw')

function WithdrawForm({ balanceNanm, onDone }: { balanceNanm: string; onDone: () => void }) {
  const [amount, setAmount] = useState('');
  const [to, setTo] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function submit() {
    setBusy(true); setMsg(null);
    try {
      const amountNanm = anmToNanm(amount || '0'); // throws on malformed
      const d = await api('/api/mkt/v1/withdrawals', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ amountNanm, toAddress: to.trim() }),
      });
      setMsg({ ok: true, text: d.note || 'Withdrawal requested.' });
      setAmount(''); setTo('');
      onDone();
    } catch (e: any) { setMsg({ ok: false, text: e.message }); }
    finally { setBusy(false); }
  }

  return (
    <section className="panel">
      <h3 style={{ margin: '0 0 4px', fontSize: 17 }}>Withdraw earnings</h3>
      <p className="muted" style={{ fontSize: 13, margin: '0 0 14px' }}>
        Send your marketplace balance on-chain. Funds are held immediately; the payout worker broadcasts with per-tx / per-day caps and confirms on-chain.
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 14 }}>
        <div>
          <label style={labelStyle}>Amount (ANM)</label>
          <input style={inputStyle} value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0.0" inputMode="decimal" />
          <button
            className="pill"
            onClick={() => setAmount(fmtAnm(balanceNanm).replace(/,/g, ''))}
            style={{ marginTop: 6, cursor: 'pointer', background: 'transparent', fontFamily: 'inherit', fontSize: 11.5, color: 'var(--text-dim)' }}
          >
            Max: {fmtAnm(balanceNanm)} ANM
          </button>
        </div>
        <div style={{ gridColumn: '1 / -1' }}>
          <label style={labelStyle}>To address</label>
          <input style={inputStyle} value={to} onChange={(e) => setTo(e.target.value)} placeholder="anim1…" spellCheck={false} />
        </div>
      </div>
      <div style={{ marginTop: 14 }}>
        <button className="btn primary" onClick={submit} disabled={busy || !amount || !to.trim()}>{busy ? 'Requesting…' : 'Request withdrawal'}</button>
        <Msg msg={msg} />
      </div>
    </section>
  );
}

export default function Earnings() {
  const [earnings, setEarnings] = useState<any>(null);
  const [balance, setBalance] = useState<any>(null);
  const [withdrawals, setWithdrawals] = useState<any[]>([]);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    setErr('');
    try {
      const [e, b, w] = await Promise.all([
        api('/api/mkt/v1/me/earnings?byListing=1'),
        api('/api/mkt/v1/balance'),
        api('/api/mkt/v1/withdrawals'),
      ]);
      setEarnings(e); setBalance(b); setWithdrawals(w.withdrawals ?? []);
    } catch (e: any) { setErr(e.message); }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (err) return <div className="panel" style={{ borderColor: 'rgba(255,92,114,0.4)', color: 'var(--bad)' }}>{err}</div>;
  if (!earnings || !balance) return <div className="empty">Loading earnings…</div>;

  const summary = earnings.summary ?? {};
  const byListing: any[] = (earnings.byListing ?? []).filter((l: any) => l.earnedNanm !== '0' || l.sales > 0);
  const unattributed = earnings.unattributedNanm ?? '0';

  return (
    <div>
      <h1 style={{ fontSize: 28, letterSpacing: '-0.03em', margin: '0 0 4px' }}>Earnings</h1>
      <p className="muted" style={{ fontSize: 14, margin: '0 0 18px' }}>Lifetime revenue across your listings, your withdrawable balance, and payout history.</p>

      <div className="panel">
        <div className="kpi">
          <div className="k"><b>{fmtAnm(summary.earnedNanm ?? '0')} ANM</b><span>lifetime earned</span></div>
          <div className="k"><b>{summary.sales ?? 0}</b><span>sale credits</span></div>
          <div className="k"><b style={{ color: 'var(--accent-2)' }}>{fmtAnm(balance.balanceNanm)} ANM</b><span>withdrawable balance</span></div>
        </div>
      </div>

      <section className="section" style={{ paddingBottom: 8 }}>
        <h2>Revenue by listing</h2>
        <p className="sub">70% creator share of each sale, credited to your marketplace balance. Amounts are net of the 30% store fee.</p>
        <div className="panel" style={{ padding: 0 }}>
          {byListing.length === 0 ? (
            <div className="muted" style={{ fontSize: 13, padding: 18 }}>No sales yet.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              {byListing.map((l) => (
                <div key={l.slug} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '13px 18px', borderTop: '1px solid var(--border)', flexWrap: 'wrap' }}>
                  <a href={`/dev/apps/${l.slug}`} style={{ fontWeight: 600, fontSize: 14 }}>{l.name}</a>
                  <StatusChip status={l.status} />
                  <span className="muted" style={{ fontSize: 12.5 }}>{l.sales} sale{l.sales === 1 ? '' : 's'}</span>
                  <span style={{ marginLeft: 'auto', fontWeight: 700, fontSize: 14 }}>{fmtAnm(l.earnedNanm)} <small className="muted">ANM</small></span>
                </div>
              ))}
              {unattributed !== '0' && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '13px 18px', borderTop: '1px solid var(--border)' }}>
                  <span className="muted" style={{ fontSize: 13 }}>Other (usage, agent offers, task payouts)</span>
                  <span style={{ marginLeft: 'auto', fontWeight: 700, fontSize: 14 }}>{fmtAnm(unattributed)} <small className="muted">ANM</small></span>
                </div>
              )}
            </div>
          )}
        </div>
      </section>

      <div style={{ marginTop: 8 }}>
        <WithdrawForm balanceNanm={balance.balanceNanm} onDone={load} />
      </div>

      <section className="section">
        <h2>Withdrawals</h2>
        <div className="panel" style={{ padding: 0 }}>
          {withdrawals.length === 0 ? (
            <div className="muted" style={{ fontSize: 13, padding: 18 }}>No withdrawals yet.</div>
          ) : (
            withdrawals.map((w) => (
              <div key={w.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 18px', borderTop: '1px solid var(--border)', flexWrap: 'wrap' }}>
                <StatusChip status={w.status} />
                <span style={{ fontWeight: 700, fontSize: 14 }}>{fmtAnm(w.amountNanm)} <small className="muted">ANM</small></span>
                <span className="mono muted" style={{ fontSize: 12 }} title={w.toAddress}>→ {String(w.toAddress).slice(0, 14)}…</span>
                {w.txid && <a className="mono" href={`/api/mkt/v1/ledger`} style={{ fontSize: 12, color: 'var(--accent)' }} title={w.txid}>{String(w.txid).slice(0, 12)}…</a>}
                {w.error && <span style={{ color: 'var(--bad)', fontSize: 12 }}>{w.error}</span>}
                <span className="muted" style={{ marginLeft: 'auto', fontSize: 12 }}>{fmtDate(w.createdAt)}</span>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}
