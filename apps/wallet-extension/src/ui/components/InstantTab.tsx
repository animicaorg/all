import React, { useCallback, useEffect, useState } from 'react';
import type { Account } from '../../types/wallet';
import { formatANM } from '../../services/balances';

interface InstantTabProps {
  currentAccount: Account;
  network: any;
}

// ANM has 9 decimals (1 ANM = 1e9 nanos). Parse a decimal ANM string into an
// integer-nanos string, matching the L1 Send tab's precision.
function parseAnmToNanos(input: string): string {
  const normalized = input.trim();
  if (!/^\d+(\.\d{1,9})?$/.test(normalized)) {
    throw new Error('Please enter a valid amount (up to 9 decimals)');
  }
  const [whole, frac = ''] = normalized.split('.');
  const fracPadded = (frac + '000000000').slice(0, 9);
  return (BigInt(whole) * 1_000_000_000n + BigInt(fracPadded)).toString();
}

type L2Status = {
  enabled?: boolean;
  mode?: string;
  l2ChainId?: number;
  settlementMode?: string;
  bridge?: { depositAddress?: string; [k: string]: unknown };
} | null;

type L2Balance = { balance?: string; nonce?: number; pendingNonce?: number; unit?: string } | null;

function InstantTab({ currentAccount }: InstantTabProps) {
  const [status, setStatus] = useState<L2Status>(null);
  const [balance, setBalance] = useState<L2Balance>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');

  const [mode, setMode] = useState<'send' | 'withdraw'>('send');
  const [to, setTo] = useState('');
  const [amount, setAmount] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<{ txid: string; status: string; proven: boolean } | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const [statusResp, balResp] = await Promise.all([
        chrome.runtime.sendMessage({ method: 'wallet_l2GetStatus' }),
        chrome.runtime.sendMessage({ method: 'wallet_l2GetBalance', params: { address: currentAccount.address } }),
      ]);
      if (statusResp?.error) throw new Error(statusResp.error.message || String(statusResp.error));
      setStatus(statusResp || null);
      if (balResp?.error) {
        // Balance can legitimately 404 for an address with no L2 activity yet.
        setBalance({ balance: '0' });
      } else {
        setBalance(balResp || null);
      }
    } catch (e: any) {
      setLoadError(e?.message || 'Failed to load L2 status');
    } finally {
      setLoading(false);
    }
  }, [currentAccount.address]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleSubmit() {
    setError('');
    setResult(null);

    if (!to.trim()) {
      setError('Please enter a recipient address');
      return;
    }
    let nanos: string;
    try {
      nanos = parseAnmToNanos(amount);
    } catch (e: any) {
      setError(e?.message || 'Invalid amount');
      return;
    }
    if (BigInt(nanos) <= 0n) {
      setError('Please enter a valid amount');
      return;
    }

    setBusy(true);
    try {
      const method = mode === 'send' ? 'wallet_l2SendInstant' : 'wallet_l2WithdrawToL1';
      const resp = await chrome.runtime.sendMessage({
        method,
        params: { from: currentAccount.address, to: to.trim(), amount: nanos },
      });
      if (resp?.error) throw new Error(resp.error.message || String(resp.error));
      if (!resp || typeof resp.txid !== 'string') {
        throw new Error('Invalid response from wallet: missing txid');
      }
      setResult({ txid: resp.txid, status: resp.status, proven: !!resp.proven });
      setTo('');
      setAmount('');
      refresh();
    } catch (e: any) {
      setError(e?.message || 'Failed to submit L2 transaction');
    } finally {
      setBusy(false);
    }
  }

  const depositAddress = status?.bridge?.depositAddress;
  const enabled = !!status?.enabled;

  return (
    <div>
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ marginTop: 0, marginBottom: 4, fontSize: '16px' }}>ANM Instant (L2)</h3>
          <span
            style={{
              fontSize: 11,
              padding: '2px 8px',
              borderRadius: 999,
              background: enabled ? '#dcfce7' : '#f1f5f9',
              color: enabled ? '#166534' : '#64748b',
            }}
          >
            {loading ? '…' : enabled ? `Live · ${status?.mode || 'rollup'}` : 'Unavailable'}
          </span>
        </div>

        {loadError && <div className="error">{loadError}</div>}

        {!loading && !enabled && !loadError && (
          <div style={{ fontSize: 12, color: '#64748b', marginTop: 8 }}>
            This node does not have the L2 (ANM Instant) rollup enabled. Same ANM asset, near-instant
            transfers once available.
          </div>
        )}

        {enabled && (
          <>
            <div className="balance-label" style={{ marginTop: 8 }}>ANM Instant Balance</div>
            <div className="balance">
              {balance?.balance != null ? formatANM(balance.balance) : '0'} ANM
            </div>
            <div style={{ fontSize: 11, color: '#94a3b8' }}>
              Distinct from your L1 balance — same asset, settled on L2 (chain {status?.l2ChainId ?? '?'}).
            </div>
            <button className="button" style={{ marginTop: 8 }} onClick={refresh} disabled={busy}>
              Refresh
            </button>
          </>
        )}
      </div>

      {enabled && depositAddress && (
        <div className="card">
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Deposit L1 → L2</h3>
          <div style={{ fontSize: 12, color: '#475569', marginBottom: 6 }}>
            Send an ordinary L1 ANM transfer to the bridge address below. Your L2 balance is credited
            after L1 finality. Never moves funds silently — you send it yourself.
          </div>
          <div className="address" style={{ wordBreak: 'break-all' }}>{depositAddress}</div>
          <button
            className="button button-secondary"
            style={{ marginTop: 6 }}
            onClick={() => navigator.clipboard?.writeText(depositAddress).catch(() => {})}
          >
            Copy bridge address
          </button>
        </div>
      )}

      {enabled && (
        <div className="card">
          <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
            <button
              type="button"
              className={`button ${mode === 'send' ? '' : 'button-secondary'}`}
              style={{ flex: 1 }}
              onClick={() => setMode('send')}
              disabled={busy}
            >
              Send Instant
            </button>
            <button
              type="button"
              className={`button ${mode === 'withdraw' ? '' : 'button-secondary'}`}
              style={{ flex: 1 }}
              onClick={() => setMode('withdraw')}
              disabled={busy}
            >
              Withdraw → L1
            </button>
          </div>

          <label className="label">{mode === 'send' ? 'Recipient (L2 address)' : 'L1 recipient address'}</label>
          <input
            type="text"
            className="input"
            placeholder="anim1... or 0x…"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            disabled={busy}
          />

          <label className="label">Amount (ANM)</label>
          <input
            type="number"
            className="input"
            placeholder="0.0"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            disabled={busy}
            step="0.0001"
            min="0"
          />

          {error && <div className="error">{error}</div>}
          {result && (
            <div className="success" style={{ marginTop: 8 }}>
              <div>
                {mode === 'send' ? 'Instant transfer' : 'Withdrawal'} submitted — status: <strong>{result.status}</strong>
              </div>
              {!result.proven && (
                <div style={{ fontSize: 11, color: '#9a6700', marginTop: 4 }}>
                  Sequencer-accepted, not yet PROVEN on L1. Not final settlement — keep this tx id.
                </div>
              )}
              <div style={{ fontSize: 11, marginTop: 4, wordBreak: 'break-all' }}>TXID: {result.txid}</div>
              <button
                type="button"
                className="button button-secondary"
                style={{ marginTop: 6, fontSize: 11 }}
                onClick={() => navigator.clipboard?.writeText(result.txid).catch(() => {})}
              >
                Copy tx id
              </button>
            </div>
          )}

          <button className="button" style={{ marginTop: 12 }} onClick={handleSubmit} disabled={busy || !to || !amount}>
            {busy ? 'Submitting…' : mode === 'send' ? 'Send Instant' : 'Withdraw to L1'}
          </button>

          <div style={{ marginTop: 12, padding: 10, background: '#eef2ff', borderRadius: 8, fontSize: 11, color: '#3730a3' }}>
            Signed with your existing account key (ML-DSA-65) — the same key as L1. ANM Instant polls to
            PROVEN before reporting settlement.
          </div>
        </div>
      )}
    </div>
  );
}

export default InstantTab;
