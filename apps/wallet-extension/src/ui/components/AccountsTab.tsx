import React, { useState, useEffect } from 'react';
import type { Account } from '../../types/wallet';
import { useBalancesStore } from '../../store/balances';

interface AccountsTabProps {
  accounts: Account[];
  currentAccount: Account | null;
  onSelectAccount: (account: Account) => void;
  onRefresh: () => void;
}

function AccountsTab({ accounts, currentAccount, onSelectAccount, onRefresh }: AccountsTabProps) {
  const [showCreate, setShowCreate] = useState(false);
  const [newAccountLabel, setNewAccountLabel] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const getBalanceState = useBalancesStore(store => store.getBalanceState);
  const refreshBalances = useBalancesStore(store => store.refreshBalances);

  useEffect(() => {
    if (accounts.length === 0) {
      return;
    }

    const addresses = accounts.map(account => account.address);
    refreshBalances(addresses, false);

    const interval = window.setInterval(() => {
      refreshBalances(addresses, false);
    }, 10000);

    return () => window.clearInterval(interval);
  }, [accounts, refreshBalances]);

  async function handleCreateAccount() {
    if (!newAccountLabel.trim()) {
      setError('Please enter a label');
      return;
    }

    setLoading(true);
    setError('');

    try {
      const result = await chrome.runtime.sendMessage({
        method: 'wallet_createAccount',
        params: { label: newAccountLabel },
      });

      if (result?.error) {
        throw new Error(result.error);
      }

      setNewAccountLabel('');
      setShowCreate(false);
      onRefresh();
    } catch (err: any) {
      setError(err.message || 'Failed to create account');
    } finally {
      setLoading(false);
    }
  }

  function copyAddress(address: string) {
    navigator.clipboard.writeText(address);
  }

  async function handleRefreshBalances() {
    await refreshBalances(accounts.map(account => account.address), true);
  }

  function getBalanceText(address: string): string {
    const balanceState = getBalanceState(address);
    if (!balanceState || balanceState.status === 'loading') {
      return 'Balance: …';
    }
    if (balanceState.status === 'ok') {
      return `Balance: ${balanceState.formatted} ANM`;
    }
    if (balanceState.formatted) {
      return `Balance: ${balanceState.formatted} ANM (stale)`;
    }
    return 'Balance: —';
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <h3 style={{ margin: 0, fontSize: '16px' }}>Your Accounts</h3>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            className="button"
            style={{ width: 'auto', padding: '8px 12px', fontSize: '12px' }}
            onClick={handleRefreshBalances}
            title="Refresh balances"
          >
            ↻ Refresh
          </button>
          <button
            className="button"
            style={{ width: 'auto', padding: '8px 16px', fontSize: '12px' }}
            onClick={() => setShowCreate(!showCreate)}
          >
            {showCreate ? 'Cancel' : '+ New Account'}
          </button>
        </div>
      </div>

      {showCreate && (
        <div className="card" style={{ marginBottom: '12px' }}>
          <label className="label">Account Label</label>
          <input
            type="text"
            className="input"
            placeholder="e.g., My Account"
            value={newAccountLabel}
            onChange={(e) => setNewAccountLabel(e.target.value)}
            disabled={loading}
          />

          {error && <div className="error">{error}</div>}

          <button
            className="button"
            onClick={handleCreateAccount}
            disabled={loading || !newAccountLabel.trim()}
          >
            {loading ? 'Creating...' : 'Create Account'}
          </button>
        </div>
      )}

      {accounts.map((account) => (
        <div
          key={account.address}
          className={`account-item ${currentAccount?.address === account.address ? 'active' : ''}`}
          onClick={() => onSelectAccount(account)}
        >
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, marginBottom: '4px' }}>
              {account.label}
              {account.watchOnly && (
                <span style={{ marginLeft: '8px', fontSize: '11px', color: '#999' }}>
                  (Watch Only)
                </span>
              )}
            </div>
            <div className="address">
              {account.address.slice(0, 20)}...{account.address.slice(-10)}
            </div>
            <div style={{ marginTop: '4px', fontSize: '12px', color: '#666' }} title={getBalanceState(account.address)?.error?.message || ''}>
              {getBalanceText(account.address)}
            </div>
          </div>
          <button
            onClick={(e) => {
              e.stopPropagation();
              copyAddress(account.address);
            }}
            style={{
              background: '#f0f0f0',
              border: 'none',
              padding: '6px 12px',
              borderRadius: '4px',
              cursor: 'pointer',
              fontSize: '12px',
            }}
          >
            📋 Copy
          </button>
        </div>
      ))}

      {accounts.length === 0 && (
        <div style={{ textAlign: 'center', color: '#999', padding: '32px' }}>
          No accounts yet. Create one to get started!
        </div>
      )}
    </div>
  );
}

export default AccountsTab;
