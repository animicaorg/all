import { useEffect, useState } from 'react';
import { usdanApi } from '../lib/api';
import { useSession } from '../lib/session';
import { formatDate } from '../lib/format';

export function DashboardPage() {
  const { session } = useSession();
  const [kyc, setKyc] = useState<any>(null);
  const [buys, setBuys] = useState<any[]>([]);
  const [redeems, setRedeems] = useState<any[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!session) return;
    Promise.all([
      usdanApi.getKycStatus(session),
      usdanApi.listBuyIntents(session),
      usdanApi.listRedemptionRequests(session)
    ])
      .then(([kycRes, buyRes, redeemRes]) => {
        setKyc(kycRes);
        setBuys(buyRes.intents);
        setRedeems(redeemRes.requests);
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load dashboard'));
  }, [session]);

  return (
    <section className="page">
      <h2>User Dashboard</h2>
      {error ? <p className="error">{error}</p> : null}
      {!session ? <p className="warning">Create a session to view account dashboard.</p> : null}

      <div className="grid two">
        <article className="card">
          <h3>Identity & Banking</h3>
          <p>KYC: {kyc?.status ?? 'unknown'}</p>
          <p>Bank Accounts: {kyc?.bankAccounts?.length ?? 0}</p>
          <p>Session User: {session?.userId ?? 'none'}</p>
        </article>

        <article className="card">
          <h3>Flow Summary</h3>
          <p>Buy intents: {buys.length}</p>
          <p>Redemptions: {redeems.length}</p>
          <p>Wallet: {session?.walletAddress ?? 'not connected'}</p>
        </article>
      </div>

      <h3>Mint History</h3>
      <div className="table">
        {buys.map((item) => (
          <div className="row" key={item.id}>
            <span>{item.id}</span>
            <span>{item.status}</span>
            <span>{formatDate(item.createdAt)}</span>
          </div>
        ))}
      </div>

      <h3>Redemption History</h3>
      <div className="table">
        {redeems.map((item) => (
          <div className="row" key={item.id}>
            <span>{item.id}</span>
            <span>{item.status}</span>
            <span>{formatDate(item.createdAt)}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
