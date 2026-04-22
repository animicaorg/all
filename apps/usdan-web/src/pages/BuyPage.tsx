import { useEffect, useMemo, useState } from 'react';
import { usdanApi } from '../lib/api';
import { useSession } from '../lib/session';

export function BuyPage() {
  const { session } = useSession();
  const [amountUsd, setAmountUsd] = useState(100);
  const [bankAccountId, setBankAccountId] = useState('');
  const [bankHash, setBankHash] = useState('');
  const [status, setStatus] = useState('');
  const [kyc, setKyc] = useState<any>(null);
  const [intents, setIntents] = useState<any[]>([]);

  const canBuy = useMemo(() => Boolean(session && kyc?.status === 'APPROVED' && bankAccountId), [session, kyc?.status, bankAccountId]);

  useEffect(() => {
    if (!session) return;
    usdanApi.getKycStatus(session).then((res) => {
      setKyc(res);
      const verified = res.bankAccounts.find((x) => x.status === 'VERIFIED');
      if (verified) setBankAccountId(verified.id);
    }).catch((err) => setStatus(err.message));

    usdanApi.listBuyIntents(session).then((res) => setIntents(res.intents)).catch(() => undefined);
  }, [session]);

  async function createBankAccount() {
    if (!session) return;
    try {
      const result = await usdanApi.createBankAccount(session, bankHash);
      setBankAccountId(result.bankAccount.id);
      setStatus('Bank account submitted for verification');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Failed to create bank account');
    }
  }

  async function submitIntent() {
    if (!session) return;
    try {
      const result = await usdanApi.createBuyIntent(session, {
        amountUsd,
        bankAccountId,
        walletAddress: session.walletAddress
      });
      setIntents((prev) => [result.intent, ...prev]);
      setStatus(`Purchase intent created: ${result.intent.id}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Failed to create purchase intent');
    }
  }

  return (
    <section className="page">
      <h2>Buy USDAN</h2>
      <p>Minting occurs only after settled fiat funds and backend authorization.</p>
      {!session ? <p className="warning">Create a wallet session to use buy flow.</p> : null}

      <div className="grid two">
        <article className="card">
          <h3>Onboarding Check</h3>
          <p>KYC status: {kyc?.status ?? 'unknown'}</p>
          <label>
            Bank hash
            <input value={bankHash} onChange={(e) => setBankHash(e.target.value)} placeholder="bank_account_hash" />
          </label>
          <button onClick={createBankAccount}>Add Bank Account</button>
          <p className="muted">Use admin panel to mark KYC and bank account as verified in local dev.</p>
        </article>

        <article className="card">
          <h3>Purchase Intent</h3>
          <label>
            Amount (USD)
            <input type="number" min={1} value={amountUsd} onChange={(e) => setAmountUsd(Number(e.target.value))} />
          </label>
          <label>
            Bank Account ID
            <input value={bankAccountId} onChange={(e) => setBankAccountId(e.target.value)} placeholder="uuid" />
          </label>
          <button disabled={!canBuy} onClick={submitIntent}>Create Buy Intent</button>
        </article>
      </div>

      <h3>Recent Buy Intents</h3>
      <div className="table">
        {intents.map((intent) => (
          <div key={intent.id} className="row">
            <span>{intent.id}</span>
            <span>{intent.amountUsd} USD</span>
            <span>{intent.status}</span>
          </div>
        ))}
      </div>

      {status ? <p className="status">{status}</p> : null}
    </section>
  );
}
