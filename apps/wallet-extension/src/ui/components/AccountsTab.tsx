import React, { useState } from 'react';
import type { Account } from '../../types/wallet';

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

  async function handleCreateAccount() {
    if (!newAccountLabel.trim()) {
      setError('Please enter a label');
      return;
    }

    setLoading(true);
    setError('');

    try {
      await chrome.runtime.sendMessage({
        method: 'wallet_createAccount',
        params: { label: newAccountLabel },
      });

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

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <h3 style={{ margin: 0, fontSize: '16px' }}>Your Accounts</h3>
        <button
          className="button"
          style={{ width: 'auto', padding: '8px 16px', fontSize: '12px' }}
          onClick={() => setShowCreate(!showCreate)}
        >
          {showCreate ? 'Cancel' : '+ New Account'}
        </button>
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
