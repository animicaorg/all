import React, { useState } from 'react';
import type { Account } from '../../types/wallet';
import { formatANM } from '../../services/balances';
import { useBalancesStore } from '../../store/balances';

interface SendTabProps {
  currentAccount: Account;
  network: any;
  balance: { confirmed: string; available: string } | null;
  onSent: () => void;
}


function parseAnmToBaseUnits(input: string): bigint {
  const normalized = input.trim();
  if (!/^\d+(\.\d{1,9})?$/.test(normalized)) {
    throw new Error('Please enter a valid amount (up to 9 decimals)');
  }

  const [whole, frac = ''] = normalized.split('.');
  const fracPadded = (frac + '000000000').slice(0, 9);
  return BigInt(whole) * 1_000_000_000n + BigInt(fracPadded);
}

function SendTab({ currentAccount, network, balance, onSent }: SendTabProps) {
  const [to, setTo] = useState('');
  const [amount, setAmount] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const liveBalance = useBalancesStore(store => store.balancesByAddress[currentAccount.address]);
  const isBalanceLoading = useBalancesStore(store => store.loadingByAddress[currentAccount.address]);
  const hasBalanceError = useBalancesStore(store => store.errorByAddress[currentAccount.address]);

  async function handleSend() {
    setError('');
    setSuccess('');

    if (!to.trim()) {
      setError('Please enter recipient address');
      return;
    }

    if (!to.startsWith('anim1')) {
      setError('Invalid address format (must start with anim1)');
      return;
    }

    let amountBase: bigint;
    try {
      amountBase = parseAnmToBaseUnits(amount);
    } catch (parseError: any) {
      setError(parseError?.message || 'Please enter a valid amount');
      return;
    }

    if (amountBase <= 0n) {
      setError('Please enter a valid amount');
      return;
    }

    if (balance) {
      const available = BigInt(balance.available);
      if (amountBase > available) {
        setError('Insufficient balance');
        return;
      }
    }

    setLoading(true);

    try {
      const result = await chrome.runtime.sendMessage({
        method: 'wallet_sendTransaction',
        params: {
          from: currentAccount.address,
          to: to.trim(),
          amount: amountBase.toString(),
        },
      });

      if (result?.error) {
        throw new Error(result.error);
      }

      // Validate result has required fields
      if (!result || typeof result.txid !== 'string') {
        throw new Error('Invalid response from wallet: missing txid');
      }

      setSuccess(`Transaction sent! TXID: ${result.txid.slice(0, 16)}...`);
      setTo('');
      setAmount('');

      // Refresh balance after a short delay
      setTimeout(() => {
        onSent();
      }, 1000);
    } catch (err: any) {
      setError(err.message || 'Failed to send transaction');
    } finally {
      setLoading(false);
    }
  }

  function getCurrentBalanceText(): string {
    if (isBalanceLoading) {
      return 'Balance: …';
    }

    if (hasBalanceError) {
      return 'Balance: unavailable';
    }

    if (typeof liveBalance === 'bigint') {
      return `Balance: ${formatANM(liveBalance)} ANM`;
    }

    if (balance) {
      return `Balance: ${formatANM(balance.available)} ANM`;
    }

    return 'Balance: …';
  }

  return (
    <div>
      <div className="card">
        <h3 style={{ marginTop: 0, fontSize: '16px' }}>Send ANM</h3>

        <div style={{ marginBottom: '16px', padding: '12px', background: '#f5f5f5', borderRadius: '8px' }}>
          <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>From</div>
          <div style={{ fontWeight: 600, fontSize: '14px', marginBottom: '4px' }}>
            {currentAccount.label}
          </div>
          <div className="address">
            {currentAccount.address}
          </div>
          <div style={{ marginTop: '8px', fontSize: '12px', color: '#999' }}>
            {getCurrentBalanceText()}
          </div>
        </div>

        <label className="label">To Address</label>
        <input
          type="text"
          className="input"
          placeholder="anim1..."
          value={to}
          onChange={(e) => setTo(e.target.value)}
          disabled={loading}
        />

        <label className="label">Amount (ANM)</label>
        <input
          type="number"
          className="input"
          placeholder="0.0"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          disabled={loading}
          step="0.0001"
          min="0"
        />

        <div style={{ marginTop: '12px', padding: '12px', background: '#f9f9f9', borderRadius: '8px', fontSize: '12px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
            <span style={{ color: '#666' }}>Network:</span>
            <span style={{ fontWeight: 600 }}>{network.name}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
            <span style={{ color: '#666' }}>Est. Gas:</span>
            <span style={{ fontWeight: 600 }}>~0.000021 ANM</span>
          </div>
        </div>

        {error && <div className="error">{error}</div>}
        {success && <div className="success">{success}</div>}

        <button
          className="button"
          onClick={handleSend}
          disabled={loading || !to || !amount}
        >
          {loading ? 'Sending...' : 'Send Transaction'}
        </button>

        <div style={{ marginTop: '12px', padding: '12px', background: '#fff4e6', borderRadius: '8px', fontSize: '12px', color: '#9a6700' }}>
          <strong>⚠️ Note:</strong> Transactions use v2 validity windows instead of nonces. Your tx will be valid for ~120 blocks (~2 hours).
        </div>
      </div>
    </div>
  );
}

export default SendTab;
