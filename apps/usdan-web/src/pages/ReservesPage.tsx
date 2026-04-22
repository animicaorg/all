import { useEffect, useState } from 'react';
import { usdanApi } from '../lib/api';
import { formatDate } from '../lib/format';

export function ReservesPage() {
  const [dashboard, setDashboard] = useState<any>(null);
  const [snapshots, setSnapshots] = useState<any[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([usdanApi.getReserveDashboard(), usdanApi.getReserveSnapshots()])
      .then(([d, s]) => {
        setDashboard(d.dashboard);
        setSnapshots(s.snapshots);
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load reserves'));
  }, []);

  return (
    <section className="page">
      <h2>Reserve Transparency</h2>
      <p>On-chain USDAN supply reconciled with off-chain reserve ledger and attestation metadata.</p>
      {error ? <p className="error">{error}</p> : null}

      {dashboard ? (
        <div className="grid">
          <article className="card"><h3>Total Supply</h3><p>{dashboard.tokenSupply}</p></article>
          <article className="card"><h3>Reserve Ledger</h3><p>{dashboard.reserveLedgerBalance}</p></article>
          <article className="card"><h3>Coverage</h3><p>{(dashboard.coverageRatioBps / 100).toFixed(2)}%</p></article>
          <article className="card"><h3>Outstanding Redemptions</h3><p>{dashboard.outstandingRedemptionQueue}</p></article>
          <article className="card"><h3>Pending Mints</h3><p>{dashboard.pendingMintQueue}</p></article>
          <article className="card"><h3>Reconciliation Hash</h3><p className="hash">{dashboard.reconciliationHash}</p></article>
        </div>
      ) : (
        <p>Loading reserve dashboard...</p>
      )}

      <h3>Snapshot Trail</h3>
      <div className="table">
        {snapshots.map((snap) => (
          <div className="row" key={snap.id}>
            <span>{formatDate(snap.capturedAt)}</span>
            <span>{snap.source}</span>
            <span>{snap.coverageRatioBps / 100}%</span>
            <span className="hash">{snap.reconciliationHash}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
