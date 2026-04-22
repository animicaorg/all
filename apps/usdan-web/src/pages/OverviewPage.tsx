import { useEffect, useState } from 'react';
import { usdanApi } from '../lib/api';
import { formatUsd } from '../lib/format';

export function OverviewPage() {
  const [dashboard, setDashboard] = useState<any>(null);
  const [error, setError] = useState<string>('');

  useEffect(() => {
    usdanApi
      .getReserveDashboard()
      .then((res) => setDashboard(res.dashboard))
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load reserve dashboard'));
  }, []);

  return (
    <section className="page">
      <h2>USDAN Platform Overview</h2>
      <p>Fiat-backed mint/redeem platform with on-chain controls and reserve transparency.</p>
      {error ? <p className="error">{error}</p> : null}
      {dashboard ? (
        <div className="grid">
          <article className="card">
            <h3>Token Supply</h3>
            <p>{formatUsd(dashboard.tokenSupply)}</p>
          </article>
          <article className="card">
            <h3>Reserve Ledger</h3>
            <p>{formatUsd(dashboard.reserveLedgerBalance)}</p>
          </article>
          <article className="card">
            <h3>Coverage Ratio</h3>
            <p>{(dashboard.coverageRatioBps / 100).toFixed(2)}%</p>
          </article>
          <article className="card">
            <h3>Pending Mint Queue</h3>
            <p>{formatUsd(dashboard.pendingMintQueue)}</p>
          </article>
        </div>
      ) : (
        <p>Loading reserve metrics...</p>
      )}
    </section>
  );
}
