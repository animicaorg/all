import React, { useState, useEffect } from 'react';
import type { Account } from '../../types/wallet';
import type { PendingTx } from '../../types/tx';
import AccountsTab from '../components/AccountsTab';
import SendTab from '../components/SendTab';
import ActivityTab from '../components/ActivityTab';
import SettingsTab from '../components/SettingsTab';

interface HomeProps {
  onLock: () => void;
}

function Home({ onLock }: HomeProps) {
  const [activeTab, setActiveTab] = useState<'accounts' | 'send' | 'activity' | 'settings'>('accounts');
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [currentAccount, setCurrentAccount] = useState<Account | null>(null);
  const [balance, setBalance] = useState<{ confirmed: string; available: string } | null>(null);
  const [network, setNetwork] = useState<any>(null);
  const [pendingTxs, setPendingTxs] = useState<PendingTx[]>([]);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 10000); // Poll every 10s
    return () => clearInterval(interval);
  }, []);

  async function loadData() {
    try {
      const accountsData = await chrome.runtime.sendMessage({ method: 'wallet_getAccounts' });
      setAccounts(accountsData);
      
      if (accountsData.length > 0 && !currentAccount) {
        setCurrentAccount(accountsData[0]);
      }

      if (currentAccount) {
        const balanceData = await chrome.runtime.sendMessage({
          method: 'wallet_getBalance',
          params: { address: currentAccount.address },
        });
        setBalance(balanceData);
      }

      const networkData = await chrome.runtime.sendMessage({ method: 'wallet_getCurrentNetwork' });
      setNetwork(networkData);

      const txsData = await chrome.runtime.sendMessage({ method: 'wallet_getPendingTxs' });
      setPendingTxs(txsData);
    } catch (error) {
      console.error('Error loading data:', error);
    }
  }

  async function handleLock() {
    await chrome.runtime.sendMessage({ method: 'wallet_lock' });
    onLock();
  }

  function formatBalance(balance: string): string {
    const bn = BigInt(balance);
    const anm = Number(bn) / 1e9;
    return anm.toFixed(4);
  }

  return (
    <div className="app">
      <div className="header">
        <div className="header-logo">
          <img src="/icon-48.png" alt="Animica" />
          <span>Animica</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <div className="network-indicator">
            {network?.name || 'Loading...'}
          </div>
          <button
            onClick={handleLock}
            style={{
              background: 'rgba(255,255,255,0.2)',
              border: 'none',
              color: 'white',
              padding: '4px 12px',
              borderRadius: '4px',
              cursor: 'pointer',
              fontSize: '12px',
            }}
          >
            🔒 Lock
          </button>
        </div>
      </div>

      <div className="tabs">
        <button
          className={`tab ${activeTab === 'accounts' ? 'active' : ''}`}
          onClick={() => setActiveTab('accounts')}
        >
          Accounts
        </button>
        <button
          className={`tab ${activeTab === 'send' ? 'active' : ''}`}
          onClick={() => setActiveTab('send')}
        >
          Send
        </button>
        <button
          className={`tab ${activeTab === 'activity' ? 'active' : ''}`}
          onClick={() => setActiveTab('activity')}
        >
          Activity
        </button>
        <button
          className={`tab ${activeTab === 'settings' ? 'active' : ''}`}
          onClick={() => setActiveTab('settings')}
        >
          Settings
        </button>
      </div>

      <div className="content">
        {balance && currentAccount && (
          <div className="card">
            <div className="balance-label">Available Balance</div>
            <div className="balance">
              {formatBalance(balance.available)} ANM
            </div>
            <div style={{ fontSize: '12px', color: '#999' }}>
              Confirmed: {formatBalance(balance.confirmed)} ANM
            </div>
          </div>
        )}

        {activeTab === 'accounts' && (
          <AccountsTab
            accounts={accounts}
            currentAccount={currentAccount}
            onSelectAccount={setCurrentAccount}
            onRefresh={loadData}
          />
        )}
        
        {activeTab === 'send' && currentAccount && network && (
          <SendTab
            currentAccount={currentAccount}
            network={network}
            balance={balance}
            onSent={loadData}
          />
        )}
        
        {activeTab === 'activity' && (
          <ActivityTab pendingTxs={pendingTxs} />
        )}
        
        {activeTab === 'settings' && (
          <SettingsTab network={network} onNetworkChange={loadData} />
        )}
      </div>
    </div>
  );
}

export default Home;
