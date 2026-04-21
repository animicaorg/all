import React, { useState } from 'react';
import type { Account } from '../../types/wallet';
import { formatANM } from '../../services/balances';
import { useBalancesStore } from '../../store/balances';


type SignaturePolicyUiError = {
  message?: string;
  action?: string;
  schemeUsed?: { id: number; name: string };
  allowedSchemes?: Array<{ id: number; name: string }>;
};

function formatSendError(input: unknown): string {
  if (!input) return 'Failed to send transaction';
  if (typeof input === 'string') return input;
  if (typeof input !== 'object') return String(input);

  const err = input as SignaturePolicyUiError;
  if (err.action === 'SWITCH_ACCOUNT_OR_ENABLE_POLICY') {
    const scheme = err.schemeUsed ? `${err.schemeUsed.name} (${err.schemeUsed.id})` : 'current signature scheme';
    const allowed = Array.isArray(err.allowedSchemes) && err.allowedSchemes.length > 0
      ? err.allowedSchemes.map((s) => `${s.name} (${s.id})`).join(', ')
      : 'unknown (policy RPC unavailable)';
    return `This network currently disables ${scheme}. Create/switch to an account using one of: ${allowed}, or ask the network operator to enable it.`;
  }

  return err.message || 'Failed to send transaction';
}

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

  const liveBalance = useBalancesStore(store => store.getBalanceState(currentAccount.address));

  async function handleSend() {
    setError('');
    setSuccess('');

    if (!to.trim()) {
      setError('Please enter recipient address');
      return;
    }

    const expectedPrefix = `${(network?.addressHrp || 'anim').toLowerCase()}1`;
    if (!to.trim().toLowerCase().startsWith(expectedPrefix)) {
      setError(`Invalid address format (must start with ${expectedPrefix})`);
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
        throw result.error;
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
      setError(formatSendError(err));
    } finally {
      setLoading(false);
    }
  }

  function getCurrentBalanceText(): string {
    if (!liveBalance || liveBalance.status === 'loading') {
      return 'Balance: …';
    }

    if (liveBalance.status === 'error') {
      return liveBalance.formatted ? `Balance: ${liveBalance.formatted} ANM (stale)` : 'Balance: —';
    }

    if (liveBalance.formatted) {
      return `Balance: ${liveBalance.formatted} ANM`;
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
          <strong>⚠️ Note:</strong> Transactions use account nonces. Pending transactions reserve the next nonce until confirmed or dropped.
        </div>
      </div>
    </div>
  );
}

export default SendTab;
