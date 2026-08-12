'use client';

// Withdraw earnings on-chain via the EXISTING marketplace payout rail:
// POST /api/mkt/v1/withdrawals (holds the funds immediately; the payout worker sends on-chain).

import { useCallback, useState } from 'react';
import { useRouter } from 'next/navigation';
import { fmtAnm, anmToNanm, nanmToAnmInput } from '@/app/dev/ui';
import { api, type CloudApiError, ApiErrBox, OkBox, ConfirmDialog, inputStyle, labelStyle } from '@/components/cloud/ui';

export default function WithdrawPanel({
  balanceNanm,
  minWithdrawalNanm,
  defaultAddress,
  payoutEnabled,
}: {
  balanceNanm: string;
  minWithdrawalNanm: string;
  defaultAddress: string;
  payoutEnabled: boolean;
}) {
  const router = useRouter();
  const [amountAnm, setAmountAnm] = useState('');
  const [toAddress, setToAddress] = useState(defaultAddress);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<CloudApiError | null>(null);
  const [done, setDone] = useState('');

  let amountNanm = '0';
  let amountOk = false;
  try {
    amountNanm = amountAnm.trim() ? anmToNanm(amountAnm.trim()) : '0';
    amountOk = BigInt(amountNanm) >= BigInt(minWithdrawalNanm) && BigInt(amountNanm) <= BigInt(balanceNanm);
  } catch { amountOk = false; }
  const addressOk = /^anim1[a-z0-9]{20,}$/.test(toAddress.trim().toLowerCase());

  const submit = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const j = await api('/api/mkt/v1/withdrawals', {
        method: 'POST',
        body: JSON.stringify({ amountNanm, toAddress: toAddress.trim().toLowerCase() }),
      });
      setDone(
        `Withdrawal of ${fmtAnm(amountNanm)} ANM requested — the funds are held from your balance now. ${j?.note ?? ''}`,
      );
      setAmountAnm('');
      setConfirming(false);
      router.refresh();
    } catch (e) {
      setError(e as CloudApiError);
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }, [amountNanm, toAddress, router]);

  return (
    <div className="panel">
      <h3 style={{ margin: '0 0 4px', fontSize: 15 }}>Withdraw on-chain</h3>
      <p className="muted" style={{ fontSize: 12.5, margin: '0 0 12px' }}>
        Balance: <b>{fmtAnm(balanceNanm)} ANM</b> · minimum withdrawal {fmtAnm(minWithdrawalNanm)} ANM.
        {!payoutEnabled && (
          <span style={{ color: 'var(--warn)' }}>
            {' '}The on-chain payout worker is currently disabled — requests queue and are paid out when the
            operator re-enables it; your funds stay held, never lost.
          </span>
        )}
      </p>

      <label style={labelStyle}>Amount (ANM)</label>
      <div style={{ display: 'flex', gap: 8 }}>
        <input
          style={{ ...inputStyle, borderColor: amountAnm && !amountOk ? 'var(--bad)' : undefined }}
          value={amountAnm}
          onChange={(e) => setAmountAnm(e.target.value)}
          placeholder="0.0"
          inputMode="decimal"
        />
        <button className="btn ghost" style={{ flexShrink: 0 }} onClick={() => setAmountAnm(nanmToAnmInput(balanceNanm))}>
          Max
        </button>
      </div>

      <label style={{ ...labelStyle, marginTop: 12 }}>Destination address</label>
      <input
        style={{ ...inputStyle, fontFamily: 'var(--mono)', fontSize: 12.5, borderColor: toAddress && !addressOk ? 'var(--bad)' : undefined }}
        value={toAddress}
        onChange={(e) => setToAddress(e.target.value)}
        spellCheck={false}
        autoComplete="off"
      />
      {toAddress && !addressOk && (
        <div style={{ color: 'var(--bad)', fontSize: 11.5, marginTop: 4 }}>must be a bech32m anim1… address</div>
      )}

      <button
        className="btn primary"
        style={{ marginTop: 14 }}
        disabled={busy || !amountOk || !addressOk}
        onClick={() => setConfirming(true)}
      >
        Withdraw {amountOk ? `${fmtAnm(amountNanm)} ANM` : ''}
      </button>

      {done && <OkBox>{done}</OkBox>}
      <ApiErrBox error={error} />

      <ConfirmDialog
        open={confirming}
        title="Confirm withdrawal"
        danger={false}
        busy={busy}
        confirmLabel="Request withdrawal"
        body={
          <>
            Send <b>{fmtAnm(amountNanm)} ANM</b> on-chain to{' '}
            <span className="mono" style={{ overflowWrap: 'anywhere' }}>{toAddress.trim().toLowerCase()}</span>?
            The amount is held from your balance immediately and paid out by the on-chain worker.
            Double-check the address — on-chain sends cannot be reversed.
          </>
        }
        onConfirm={submit}
        onCancel={() => setConfirming(false)}
      />
    </div>
  );
}
