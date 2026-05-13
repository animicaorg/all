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

/**
 * Convert a base-units bigint back to an ANM decimal string with up to
 * 9 decimal places (trailing zeros trimmed). Reverses parseAnmToBaseUnits.
 */
function formatBaseUnitsToAnm(baseUnits: bigint): string {
  if (baseUnits < 0n) return '0';
  const divisor = 1_000_000_000n;
  const whole = baseUnits / divisor;
  const frac = baseUnits % divisor;
  const fracStr = frac.toString().padStart(9, '0').replace(/0+$/, '');
  return fracStr.length ? `${whole.toString()}.${fracStr}` : whole.toString();
}

// Reserve a tiny buffer so the user can't accidentally send their entire
// balance and run into mempool fee deductions. 0.000021 ANM mirrors the
// "Est. Gas" hint shown below.
const SEND_GAS_RESERVE_BASE_UNITS = 21_000n;

function SendTab({ currentAccount, network, balance, onSent }: SendTabProps) {
  const [to, setTo] = useState('');
  const [amount, setAmount] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [lastTxHash, setLastTxHash] = useState<string | null>(null);

  const liveBalance = useBalancesStore(store => store.getBalanceState(currentAccount.address));

  function getAvailableBaseUnits(): bigint | null {
    try {
      if (liveBalance?.status === 'ok' && liveBalance.valueAtomic) {
        return BigInt(liveBalance.valueAtomic);
      }
    } catch {
      // fall through to legacy path
    }
    if (balance?.available) {
      try {
        return BigInt(balance.available);
      } catch {
        return null;
      }
    }
    return null;
  }

  function setMaxAmount() {
    const available = getAvailableBaseUnits();
    if (available === null) {
      setError('Balance unavailable; refresh first');
      return;
    }
    const usable = available > SEND_GAS_RESERVE_BASE_UNITS
      ? available - SEND_GAS_RESERVE_BASE_UNITS
      : available;
    setAmount(formatBaseUnitsToAnm(usable));
    setError('');
  }

  async function handleSend() {
    setError('');
    setSuccess('');
    setLastTxHash(null);

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
      setLastTxHash(result.txid);
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

        <label className="label" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>Amount (ANM)</span>
          <button
            type="button"
            onClick={setMaxAmount}
            disabled={loading || !getAvailableBaseUnits()}
            style={{
              background: 'transparent',
              border: '1px solid #cbd5e1',
              borderRadius: 4,
              padding: '2px 8px',
              fontSize: 11,
              cursor: loading ? 'not-allowed' : 'pointer',
              color: '#475569',
            }}
            title="Set amount to the maximum minus a small gas reserve"
          >
            Max
          </button>
        </label>
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
        {success && (
          <div className="success">
            <div>{success}</div>
            {lastTxHash && (
              <div style={{ marginTop: 6, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <button
                  type="button"
                  onClick={() => navigator.clipboard?.writeText(lastTxHash).catch(() => {})}
                  style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, border: '1px solid #94a3b8', background: 'white', cursor: 'pointer' }}
                  title="Copy full tx hash"
                >
                  Copy tx hash
                </button>
                <button
                  type="button"
                  onClick={() => {
                    try {
                      chrome.tabs?.create({ url: 'https://trade.animica.org' });
                    } catch {
                      window.open('https://trade.animica.org', '_blank', 'noopener,noreferrer');
                    }
                  }}
                  style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, border: '1px solid #94a3b8', background: 'white', cursor: 'pointer' }}
                  title="Open the Animica exchange"
                >
                  Open Exchange
                </button>
              </div>
            )}
          </div>
        )}

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
