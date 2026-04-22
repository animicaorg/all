import { useEffect, useState } from 'react';
import { usdanApi } from '../lib/api';
import { useSession } from '../lib/session';

export function RedeemPage() {
  const { session } = useSession();
  const [amountUsdan, setAmountUsdan] = useState(50);
  const [bankAccountId, setBankAccountId] = useState('');
  const [requests, setRequests] = useState<any[]>([]);
  const [status, setStatus] = useState('');

  useEffect(() => {
    if (!session) return;
    usdanApi.getKycStatus(session).then((res) => {
      const verified = res.bankAccounts.find((x) => x.status === 'VERIFIED');
      if (verified) setBankAccountId(verified.id);
    }).catch(() => undefined);

    usdanApi.listRedemptionRequests(session).then((res) => setRequests(res.requests)).catch(() => undefined);
  }, [session]);

  async function submitRedemption() {
    if (!session) return;
    try {
      const result = await usdanApi.createRedemptionRequest(session, {
        amountUsdan,
        bankAccountId,
        walletAddress: session.walletAddress,
        userIntentHash: `intent_${Date.now()}`
      });
      setRequests((prev) => [result.request, ...prev]);
      setStatus(`Redemption request created: ${result.request.id}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Failed to create redemption request');
    }
  }

  return (
    <section className="page">
      <h2>Redeem USDAN</h2>
      <p>Redemption requires signed intent, on-chain burn/escrow confirmation, and fiat payout settlement.</p>

      <div className="card">
        <label>
          Amount (USDAN)
          <input type="number" min={1} value={amountUsdan} onChange={(e) => setAmountUsdan(Number(e.target.value))} />
        </label>
        <label>
          Bank Account ID
          <input value={bankAccountId} onChange={(e) => setBankAccountId(e.target.value)} placeholder="verified bank account id" />
        </label>
        <button disabled={!session || !bankAccountId} onClick={submitRedemption}>Create Redemption Request</button>
      </div>

      <h3>Recent Redemptions</h3>
      <div className="table">
        {requests.map((req) => (
          <div key={req.id} className="row">
            <span>{req.id}</span>
            <span>{req.amountUsdan} USDAN</span>
            <span>{req.status}</span>
          </div>
        ))}
      </div>

      {status ? <p className="status">{status}</p> : null}
    </section>
  );
}
