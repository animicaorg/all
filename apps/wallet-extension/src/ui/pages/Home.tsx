import React, { useState, useEffect } from 'react';
import type { Account } from '../../types/wallet';
import type { PendingTx } from '../../types/tx';
import AccountsTab from '../components/AccountsTab';
import SendTab from '../components/SendTab';
import ActivityTab from '../components/ActivityTab';
import SettingsTab from '../components/SettingsTab';
import { formatANM } from '../../services/balances';
import { useBalancesStore } from '../../store/balances';
import { activeWalletStoreActions, useActiveWalletStore } from '../../store/activeWallet';
import { stringifySafe } from '../../core/rpc/safeJson';

// `JSON.stringify` throws on bigint, which breaks the debug panel when RPC
// debug state contains balance/nonce/gas values. Use the same bigint+Uint8Array
// safe encoder used by every other JSON boundary in the extension.
function safeStringifyPretty(value: unknown): string {
  try {
    return JSON.stringify(JSON.parse(stringifySafe(value)), null, 2);
  } catch {
    return '[unserializable]';
  }
}

interface HomeProps {
  onLock: () => void;
}

const DEBUG_WALLET_UI = true; // Always enabled for troubleshooting

function Home({ onLock }: HomeProps) {
  const [activeTab, setActiveTab] = useState<'accounts' | 'send' | 'activity' | 'settings'>('accounts');
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [balance, setBalance] = useState<{ confirmed: string; available: string } | null>(null);
  const [balanceError, setBalanceError] = useState<string | null>(null);
  const [loadingBalance, setLoadingBalance] = useState(false);
  const [network, setNetwork] = useState<any>(null);
  const [pendingTxs, setPendingTxs] = useState<PendingTx[]>([]);
  const [showDebug, setShowDebug] = useState(false);
  const [debugState, setDebugState] = useState<any>(null);

  const getBalanceState = useBalancesStore(store => store.getBalanceState);
  const refreshBalance = useBalancesStore(store => store.refreshBalance);
  const refreshBalances = useBalancesStore(store => store.refreshBalances);
  const currentAccount = useActiveWalletStore(store => store.activeWallet);
  const activeWalletError = useActiveWalletStore(store => store.error);

  useEffect(() => {
    activeWalletStoreActions.hydrateActiveWallet();
    const unsubscribe = activeWalletStoreActions.listenForActiveWalletChanges();
    loadData();
    const interval = setInterval(loadData, 10000); // Poll every 10s
    return () => {
      clearInterval(interval);
      unsubscribe();
    };
  }, []);

  useEffect(() => {
    if (!showDebug) return;
    loadData();
  }, [showDebug]);

  useEffect(() => {
    if (!currentAccount) {
      return;
    }

    const interval = setInterval(() => {
      loadBalance(currentAccount.address);
    }, 12000);

    return () => clearInterval(interval);
  }, [currentAccount]);

  useEffect(() => {
    if (accounts.length > 0) {
      refreshBalances(accounts.map(account => account.address), false);
    }
  }, [accounts, refreshBalances]);

  useEffect(() => {
    if (currentAccount) {
      refreshBalance(currentAccount.address, true);
      loadBalance(currentAccount.address);
    }
  }, [currentAccount, refreshBalance]);

  async function loadData() {
    try {
      const accountsData = await chrome.runtime.sendMessage({ method: 'wallet_getAccounts' });
      if (accountsData?.error) {
        throw new Error(accountsData.error);
      }
      setAccounts(accountsData);

      const networkData = await chrome.runtime.sendMessage({ method: 'wallet_getCurrentNetwork' });
      if (networkData?.error) {
        throw new Error(networkData.error);
      }
      setNetwork(networkData);

      const txsData = await chrome.runtime.sendMessage({ method: 'wallet_getPendingTxs' });
      if (txsData?.error) {
        throw new Error(txsData.error);
      }
      setPendingTxs(txsData);

      if (showDebug) {
        const debugData = await chrome.runtime.sendMessage({ method: 'wallet_getDebugState' });
        if (debugData?.error) {
          throw new Error(debugData.error);
        }
        setDebugState(debugData);
      }
    } catch (error) {
      console.error('Error loading data:', error);
      setBalanceError(error instanceof Error ? error.message : 'Failed to load balance');
      setLoadingBalance(false);
    }
  }

  async function loadBalance(address: string): Promise<void> {
    try {
      setLoadingBalance(true);
      setBalanceError(null);
      if (DEBUG_WALLET_UI) {
        console.debug('[wallet-ui] loadBalance', { address, active: currentAccount?.address, rpcUrl: network?.effectiveRpcUrl, chainId: network?.chainId });
      }
      const balanceData = await chrome.runtime.sendMessage({
        method: 'wallet_getBalance',
        params: { address },
      });
      if (balanceData?.error) {
        throw new Error(balanceData.error);
      }
      setBalance(balanceData);
    } catch (error) {
      setBalanceError(error instanceof Error ? error.message : 'Failed to load balance');
    } finally {
      setLoadingBalance(false);
    }
  }

  async function handleLock() {
    try {
      const result = await chrome.runtime.sendMessage({ method: 'wallet_lock' });
      if (result?.error) {
        throw new Error(result.error);
      }
      onLock();
    } catch (error) {
      console.error('Error locking wallet:', error);
      // Still call onLock to ensure UI state is consistent
      onLock();
    }
  }

  function formatAvailableBalance(balanceRaw: string): string {
    const liveBalance = currentAccount ? getBalanceState(currentAccount.address) : undefined;
    if (liveBalance?.status === 'ok' && liveBalance.formatted) {
      return liveBalance.formatted;
    }

    const bn = BigInt(balanceRaw);
    return formatANM(bn);
  }

  function handleSelectAccount(account: Account) {
    activeWalletStoreActions.switchActiveWallet(account.address);
    refreshBalance(account.address, true);
    loadBalance(account.address);
  }

  async function refreshCurrentBalance(): Promise<void> {
    if (currentAccount) {
      await loadBalance(currentAccount.address);
    }
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
        {currentAccount && (
          <div className="card">
            <div className="balance-label">Available Balance</div>
            {loadingBalance && <div style={{ fontSize: '14px', color: '#888' }}>Loading balance...</div>}
            {!loadingBalance && balanceError && (
              <details style={{ color: '#b00020', fontSize: '12px' }}>
                <summary>—</summary>
                {balanceError}
              </details>
            )}
            {!loadingBalance && !balanceError && balance && (
              <>
                <div className="balance">
                  {formatAvailableBalance(balance.available)} ANM
                </div>
                <div style={{ fontSize: '12px', color: '#999' }}>
                  Confirmed: {formatANM(balance.confirmed)} ANM
                </div>
              </>
            )}
            <div style={{ fontSize: '11px', color: '#777', marginTop: 6 }}>
              RPC: {network?.effectiveRpcUrl ? new URL(network.effectiveRpcUrl).host : 'unknown'}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
              <button className="button" style={{ flex: '1 1 auto' }} onClick={refreshCurrentBalance}>Refresh</button>
              <button
                className="button button-accent"
                style={{ flex: '1 1 auto', background: 'linear-gradient(135deg,#6c5ce7,#00b894)', color: '#fff' }}
                onClick={() => {
                  try {
                    chrome.tabs?.create({ url: 'https://trade.animica.org' });
                  } catch {
                    window.open('https://trade.animica.org', '_blank', 'noopener,noreferrer');
                  }
                }}
                title="Open the Animica exchange in a new tab"
                aria-label="Open Exchange"
              >
                💱 Open Exchange
              </button>
            </div>
            <button className="button" style={{ marginTop: 8 }} onClick={() => setShowDebug(v => !v)}>
              {showDebug ? 'Hide debug' : 'Show debug'}
            </button>
            {showDebug && (
              <details style={{ marginTop: 8, fontSize: '11px' }} open>
                <summary>Debug details</summary>
                <div style={{ marginTop: 8, padding: 8, background: '#f5f5f5', borderRadius: 4 }}>
                  <div><strong>Active Wallet:</strong></div>
                  <div style={{ marginLeft: 8 }}>Label: {debugState?.activeWallet?.label || 'n/a'}</div>
                  <div style={{ marginLeft: 8, wordBreak: 'break-all' }}>Address: {debugState?.activeWallet?.address || 'n/a'}</div>
                </div>
                <div style={{ marginTop: 8, padding: 8, background: '#f5f5f5', borderRadius: 4 }}>
                  <div><strong>RPC Configuration:</strong></div>
                  <div style={{ marginLeft: 8 }}>URL: {debugState?.rpcUrl || network?.effectiveRpcUrl || 'n/a'}</div>
                  <div style={{ marginLeft: 8 }}>Chain ID: {debugState?.chainId ?? network?.chainId ?? 'n/a'}</div>
                  <div style={{ marginLeft: 8 }}>Last fetch: {debugState?.lastBalanceFetchedAt ? new Date(debugState.lastBalanceFetchedAt).toLocaleString() : 'never'}</div>
                </div>
                {debugState?.lastBalanceRequest && (
                  <div style={{ marginTop: 8, padding: 8, background: '#fff3cd', borderRadius: 4 }}>
                    <div><strong>Last Balance Request:</strong></div>
                    <div style={{ marginLeft: 8, wordBreak: 'break-all' }}>Address: {debugState.lastBalanceRequest.address}</div>
                    <div style={{ marginLeft: 8 }}>RPC URL: {debugState.lastBalanceRequest.rpcUrl}</div>
                    <div style={{ marginLeft: 8 }}>Chain ID: {debugState.lastBalanceRequest.chainId}</div>
                    <div style={{ marginLeft: 8 }}>Timestamp: {new Date(debugState.lastBalanceRequest.timestamp).toLocaleString()}</div>
                  </div>
                )}
                {(debugState?.lastBalanceError || debugState?.lastPingError) && (
                  <div style={{ marginTop: 8, padding: 8, background: '#f8d7da', borderRadius: 4, color: '#721c24' }}>
                    <div><strong>Errors:</strong></div>
                    {debugState?.lastBalanceError && <div style={{ marginLeft: 8 }}>Balance: {debugState.lastBalanceError}</div>}
                    {debugState?.lastPingError && <div style={{ marginLeft: 8 }}>Ping: {debugState.lastPingError}</div>}
                  </div>
                )}
                <div style={{ marginTop: 8, padding: 8, background: '#eef7ff', borderRadius: 4 }}>
                  <div><strong>Wallet balance state:</strong></div>
                  {accounts.map((account) => {
                    const state = getBalanceState(account.address);
                    return (
                      <div key={account.address} style={{ marginLeft: 8 }}>
                        {account.label}: {state?.status ?? 'loading'} · updated {state?.updatedAt ? new Date(state.updatedAt).toLocaleTimeString() : 'never'}
                        {state?.error?.message ? ` · error: ${state.error.message}` : ''}
                      </div>
                    );
                  })}
                </div>

                {debugState?.txRpcDebug?.lastSendRequest && (
                  <div style={{ marginTop: 8, padding: 8, background: '#e8f4fd', borderRadius: 4 }}>
                    <div><strong>Last Send RPC Request:</strong></div>
                    <div style={{ marginLeft: 8 }}>Method: {debugState.txRpcDebug.lastSendRequest.method}</div>
                    <div style={{ marginLeft: 8, wordBreak: 'break-all' }}>RPC URL: {debugState.txRpcDebug.lastSendRequest.rpcUrl}</div>
                    <pre style={{ whiteSpace: 'pre-wrap', fontSize: 10, background: '#fff', padding: 8, borderRadius: 4, maxHeight: 180, overflow: 'auto' }}>
                      {safeStringifyPretty(debugState.txRpcDebug.lastSendRequest.params)}
                    </pre>
                    <details>
                      <summary><strong>Raw JSON-RPC request body</strong></summary>
                      <pre style={{ whiteSpace: 'pre-wrap', fontSize: 10, background: '#fff', padding: 8, borderRadius: 4, maxHeight: 180, overflow: 'auto' }}>
                        {debugState.txRpcDebug.lastSendRequest.requestBody}
                      </pre>
                    </details>
                    <details style={{ marginTop: 6 }}>
                      <summary><strong>Raw send response / error</strong></summary>
                      <pre style={{ whiteSpace: 'pre-wrap', fontSize: 10, background: '#fff', padding: 8, borderRadius: 4, maxHeight: 180, overflow: 'auto' }}>
                        {safeStringifyPretty({ response: debugState?.txRpcDebug?.lastSendResponse ?? null, error: debugState?.txRpcDebug?.lastSendError ?? null })}
                      </pre>
                    </details>
                  </div>
                )}
                <details style={{ marginTop: 8 }}>
                  <summary><strong>Raw Balance Response</strong></summary>
                  <pre style={{ whiteSpace: 'pre-wrap', fontSize: 10, background: '#fff', padding: 8, borderRadius: 4, maxHeight: 200, overflow: 'auto' }}>
                    {safeStringifyPretty(debugState?.lastBalanceResponse ?? null)}
                  </pre>
                  <button
                    className="button"
                    style={{ marginTop: 4, fontSize: 10, padding: '4px 8px' }}
                    onClick={() => navigator.clipboard.writeText(safeStringifyPretty(debugState?.lastBalanceResponse ?? null))}
                  >
                    Copy Response
                  </button>
                </details>
                <details style={{ marginTop: 8 }}>
                  <summary><strong>Raw Ping Response</strong></summary>
                  <pre style={{ whiteSpace: 'pre-wrap', fontSize: 10, background: '#fff', padding: 8, borderRadius: 4, maxHeight: 200, overflow: 'auto' }}>
                    {safeStringifyPretty(debugState?.lastPingResponse ?? null)}
                  </pre>
                  <button
                    className="button"
                    style={{ marginTop: 4, fontSize: 10, padding: '4px 8px' }}
                    onClick={() => navigator.clipboard.writeText(safeStringifyPretty(debugState?.lastPingResponse ?? null))}
                  >
                    Copy Response
                  </button>
                </details>
              </details>
            )}
          </div>
        )}

        {activeWalletError && (
          <div className="card" style={{ color: '#b00020', fontSize: '12px' }}>
            Active wallet error: {activeWalletError}
          </div>
        )}

        {activeTab === 'accounts' && (
          <AccountsTab
            accounts={accounts}
            currentAccount={currentAccount}
            onSelectAccount={handleSelectAccount}
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
          <SettingsTab network={network} onNetworkChange={loadData} onAccountsChanged={loadData} />
        )}
      </div>
    </div>
  );
}

export default Home;
