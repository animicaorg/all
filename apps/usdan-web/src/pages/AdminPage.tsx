import { useState } from 'react';
import { usdanApi } from '../lib/api';

export function AdminPage() {
  const [adminKey, setAdminKey] = useState(import.meta.env.VITE_USDAN_ADMIN_API_KEY ?? '');
  const [purchases, setPurchases] = useState<any[]>([]);
  const [redemptions, setRedemptions] = useState<any[]>([]);
  const [webhooks, setWebhooks] = useState<any[]>([]);
  const [status, setStatus] = useState('');
  const [kycUserId, setKycUserId] = useState('');
  const [flagUserId, setFlagUserId] = useState('');

  async function refresh() {
    try {
      const [buyRes, redeemRes, hookRes] = await Promise.all([
        usdanApi.adminListPurchases(adminKey),
        usdanApi.adminListRedemptions(adminKey),
        usdanApi.adminListWebhooks(adminKey)
      ]);
      setPurchases(buyRes.purchases);
      setRedemptions(redeemRes.redemptions);
      setWebhooks(hookRes.webhooks);
      setStatus('Admin data refreshed');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Failed to load admin data');
    }
  }

  async function approveKyc() {
    try {
      await usdanApi.adminSetKyc(adminKey, { userId: kycUserId, status: 'APPROVED', provider: 'manual' });
      setStatus(`KYC approved for ${kycUserId}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Failed to set KYC');
    }
  }

  async function addFlag() {
    try {
      await usdanApi.adminAddComplianceFlag(adminKey, {
        userId: flagUserId,
        type: 'MANUAL_REVIEW',
        reason: 'Manual compliance hold from admin panel'
      });
      setStatus(`Compliance flag added for ${flagUserId}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Failed to add compliance flag');
    }
  }

  async function publishSnapshot() {
    try {
      await usdanApi.adminPublishReserveSnapshot(adminKey);
      setStatus('Manual reserve snapshot published');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Failed to publish snapshot');
    }
  }

  return (
    <section className="page">
      <h2>Admin Operations</h2>
      <p>Approvals, holds, reserve snapshots, and webhook monitoring.</p>

      <div className="card">
        <label>
          Admin API Key
          <input value={adminKey} onChange={(e) => setAdminKey(e.target.value)} />
        </label>
        <div className="row-wrap">
          <button onClick={refresh}>Refresh Data</button>
          <button onClick={publishSnapshot}>Publish Reserve Snapshot</button>
        </div>
      </div>

      <div className="grid two">
        <article className="card">
          <h3>KYC Override</h3>
          <input value={kycUserId} onChange={(e) => setKycUserId(e.target.value)} placeholder="user id" />
          <button onClick={approveKyc}>Set KYC APPROVED</button>
        </article>

        <article className="card">
          <h3>Add Compliance Flag</h3>
          <input value={flagUserId} onChange={(e) => setFlagUserId(e.target.value)} placeholder="user id" />
          <button onClick={addFlag}>Add Manual Review Flag</button>
        </article>
      </div>

      <h3>Purchase Queue</h3>
      <div className="table">
        {purchases.map((item) => (
          <div key={item.id} className="row"><span>{item.id}</span><span>{item.status}</span><span>{item.amountUsdan}</span></div>
        ))}
      </div>

      <h3>Redemption Queue</h3>
      <div className="table">
        {redemptions.map((item) => (
          <div key={item.id} className="row"><span>{item.id}</span><span>{item.status}</span><span>{item.amountUsdan}</span></div>
        ))}
      </div>

      <h3>Webhook Deliveries</h3>
      <div className="table">
        {webhooks.map((hook) => (
          <div key={hook.id} className="row"><span>{hook.eventId}</span><span>{hook.status}</span><span>{hook.attemptCount}</span></div>
        ))}
      </div>

      {status ? <p className="status">{status}</p> : null}
    </section>
  );
}
