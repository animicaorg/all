import React, { useEffect, useRef, useState } from 'react';

interface SettingsTabProps {
  network: any;
  onNetworkChange: () => void;
  onAccountsChanged: () => void;
}

const FORCE_RAWTX_COMPAT_KEY = 'force_rawtx_compat';

interface ImportSummary {
  imported_count: number;
  skipped_duplicates: number;
  upgraded_watch_only: number;
  invalid_records: Array<{ index: number; label?: string; reason: string }>;
  total_accounts: number;
}

interface WatchedToken {
  type: string;
  address: string;
  symbol: string;
  decimals: number;
  chainId: number;
  name?: string;
  image?: string;
}

function SettingsTab({ network, onNetworkChange, onAccountsChanged }: SettingsTabProps) {
  const [selectedNetwork, setSelectedNetwork] = useState(network?.id || 'mainnet');
  const [isBusy, setIsBusy] = useState(false);
  const [rpcInput, setRpcInput] = useState('');
  const [effectiveRpcUrl, setEffectiveRpcUrl] = useState('');
  const [rpcMessage, setRpcMessage] = useState<string | null>(null);
  const [rpcError, setRpcError] = useState<string | null>(null);
  const [isTestingRpc, setIsTestingRpc] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [rpcWarning, setRpcWarning] = useState<string | null>(null);
  const [forceRawTxCompat, setForceRawTxCompat] = useState(false);
  const [importSummary, setImportSummary] = useState<ImportSummary | null>(null);
  const [watchedTokens, setWatchedTokens] = useState<WatchedToken[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setSelectedNetwork(network?.id || 'mainnet');
  }, [network?.id]);

  useEffect(() => {
    loadRpcConfig();
    loadWatchedTokens();
  }, []);

  useEffect(() => {
    loadWatchedTokens();
  }, [network?.id]);

  async function loadRpcConfig() {
    try {
      const result = await chrome.runtime.sendMessage({ method: 'wallet_getRpcConfig' });
      if (result?.error) {
        throw new Error(result.error);
      }

      setRpcInput(result.rpcUrl || '');
      setEffectiveRpcUrl(result.rpcUrl || '');
      setRpcWarning(result.warning || null);
      setRpcError(null);

      const stored = await chrome.storage.local.get([FORCE_RAWTX_COMPAT_KEY]);
      const value = stored?.[FORCE_RAWTX_COMPAT_KEY];
      setForceRawTxCompat(value === true || value === '1' || value === 'true');
    } catch (error: any) {
      setRpcError(error?.message || 'Failed to load RPC configuration');
    }
  }

  async function loadWatchedTokens() {
    try {
      const result = await chrome.runtime.sendMessage({ method: 'wallet_getTokens' });
      if (result?.error) {
        throw new Error(result.error);
      }
      setWatchedTokens(Array.isArray(result) ? result : []);
    } catch {
      setWatchedTokens([]);
    }
  }

  async function handleNetworkChange(networkId: string) {
    try {
      const result = await chrome.runtime.sendMessage({
        method: 'wallet_switchNetwork',
        params: { networkId },
      });

      if (result?.error) {
        throw new Error(result.error);
      }

      setSelectedNetwork(networkId);
      onNetworkChange();
      loadWatchedTokens();
    } catch (error) {
      console.error('Failed to switch network:', error);
    }
  }

  async function handleSaveRpc() {
    setRpcMessage(null);
    setRpcError(null);
    setTestResult(null);

    try {
      setIsBusy(true);
      const result = await chrome.runtime.sendMessage({
        method: 'wallet_setRpcUrl',
        params: { url: rpcInput },
      });

      if (result?.error) {
        throw new Error(result.error);
      }

      setRpcInput(result.rpcUrl);
      setEffectiveRpcUrl(result.rpcUrl);
      setRpcWarning(result.warning || null);
      setRpcMessage('RPC endpoint saved. Runtime client updated.');
      onNetworkChange();
    } catch (error: any) {
      setRpcError(error?.message || 'Failed to save RPC endpoint');
    } finally {
      setIsBusy(false);
    }
  }

  async function handleResetRpc() {
    setRpcMessage(null);
    setRpcError(null);
    setTestResult(null);

    try {
      setIsBusy(true);
      const result = await chrome.runtime.sendMessage({ method: 'wallet_resetRpcUrl' });
      if (result?.error) {
        throw new Error(result.error);
      }

      setRpcInput(result.rpcUrl);
      setEffectiveRpcUrl(result.rpcUrl);
      setRpcWarning(null);
      setRpcMessage('RPC endpoint reset to default mainnet RPC.');
      onNetworkChange();
    } catch (error: any) {
      setRpcError(error?.message || 'Failed to reset RPC endpoint');
    } finally {
      setIsBusy(false);
    }
  }

  async function handleTestConnection() {
    setRpcError(null);
    setTestResult(null);

    try {
      setIsTestingRpc(true);
      const result = await chrome.runtime.sendMessage({
        method: 'wallet_testRpcConnection',
        params: { url: rpcInput },
      });

      if (result?.error) {
        throw new Error(result.error);
      }

      if (!result.ok) {
        setRpcError(`Connection test failed (${result.latencyMs}ms): ${result.error}`);
        return;
      }

      setTestResult(
        `Connected in ${result.latencyMs}ms • chain_id=${result.chainId ?? 'n/a'} • node=${result.nodeId ?? 'n/a'}`,
      );
    } catch (error: any) {
      setRpcError(error?.message || 'Failed to test RPC connection');
    } finally {
      setIsTestingRpc(false);
    }
  }


  async function handleForceCompatToggle(enabled: boolean) {
    setForceRawTxCompat(enabled);
    await chrome.storage.local.set({ [FORCE_RAWTX_COMPAT_KEY]: enabled ? '1' : '0' });
    setRpcMessage(enabled
      ? 'Forced raw-tx compatibility mode enabled.'
      : 'Forced raw-tx compatibility mode disabled.');
  }

  function triggerImportPicker() {
    fileInputRef.current?.click();
  }

  async function handleImportFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;

    try {
      setIsBusy(true);
      setImportSummary(null);
      const json = await file.text();

      const result = await chrome.runtime.sendMessage({
        method: 'wallet_importWalletsJson',
        params: { json },
      });

      if (result?.error) {
        throw new Error(result.error);
      }

      setImportSummary(result as ImportSummary);
      onAccountsChanged();
    } catch (error: any) {
      setImportSummary({
        imported_count: 0,
        skipped_duplicates: 0,
        upgraded_watch_only: 0,
        total_accounts: 0,
        invalid_records: [{ index: 0, reason: error?.message || 'Unknown error' }],
      });
    } finally {
      setIsBusy(false);
      event.target.value = '';
    }
  }

  async function handleExportWallets() {
    try {
      setIsBusy(true);
      const includeSecrets = confirm(
        'Include private keys in exported file? Choose Cancel to export public-only data.',
      );

      const result = await chrome.runtime.sendMessage({
        method: 'wallet_exportWalletsJson',
        params: { includeSecrets },
      });

      if (result?.error) {
        throw new Error(result.error);
      }

      const json = result?.json;
      if (typeof json !== 'string') {
        throw new Error('Invalid export payload');
      }

      const blob = new Blob([json], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `wallets-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);

      alert('Wallet export downloaded. Keep the file secure.');
    } catch (error: any) {
      console.error('Failed to export wallets.json:', error);
      alert(`Export failed: ${error?.message || 'Unknown error'}`);
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <div>
      <div className="card">
        <h3 style={{ marginTop: 0, fontSize: '16px' }}>Network</h3>

        <div style={{ marginTop: '12px' }}>
          {['mainnet', 'testnet', 'devnet'].map((netId) => (
            <div
              key={netId}
              onClick={() => handleNetworkChange(netId)}
              style={{
                padding: '12px',
                background: selectedNetwork === netId ? '#e7f3ff' : '#f9f9f9',
                border: selectedNetwork === netId ? '2px solid #667eea' : '2px solid transparent',
                borderRadius: '8px',
                marginBottom: '8px',
                cursor: 'pointer',
                transition: 'all 0.2s',
              }}
            >
              <div style={{ fontWeight: 600, marginBottom: '4px', textTransform: 'capitalize' }}>
                {netId}
                {selectedNetwork === netId && (
                  <span style={{ marginLeft: '8px', color: '#667eea' }}>✓</span>
                )}
              </div>
              <div style={{ fontSize: '11px', color: '#666' }}>
                {netId === 'mainnet' && 'Chain ID: 1 • Primary: mainnet.animica.org'}
                {netId === 'testnet' && 'Chain ID: 2 • Local testnet'}
                {netId === 'devnet' && 'Chain ID: 1337 • Local development'}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h3 style={{ marginTop: 0, fontSize: '16px' }}>RPC Endpoint</h3>

        <div style={{ fontSize: '12px', color: '#666', marginBottom: '8px' }}>
          Effective RPC URL: <code>{effectiveRpcUrl || 'Loading...'}</code>
        </div>

        <label className="label" htmlFor="rpc-url">RPC URL</label>
        <input
          id="rpc-url"
          className="input"
          type="text"
          value={rpcInput}
          onChange={(event) => setRpcInput(event.target.value)}
          placeholder="https://rpc.animica.org/rpc"
          disabled={isBusy || isTestingRpc}
        />

        <div className="settings-rpc-actions">
          <button className="button" onClick={handleSaveRpc} disabled={isBusy || isTestingRpc}>
            {isBusy ? 'Saving…' : 'Save'}
          </button>
          <button className="button button-secondary" onClick={handleResetRpc} disabled={isBusy || isTestingRpc}>
            Reset to default
          </button>
          <button className="button button-secondary" onClick={handleTestConnection} disabled={isBusy || isTestingRpc}>
            {isTestingRpc ? 'Testing…' : 'Test Connection'}
          </button>
        </div>


        <label style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '8px', fontSize: '12px', color: '#444' }}>
          <input
            type="checkbox"
            checked={forceRawTxCompat}
            onChange={(event) => {
              void handleForceCompatToggle(event.target.checked);
            }}
            disabled={isBusy || isTestingRpc}
          />
          Force raw transaction compatibility probing
        </label>

        {rpcWarning && <div className="warning">{rpcWarning}</div>}
        {network?.rpcWarning && <div className="warning">{network.rpcWarning}</div>}
        {rpcMessage && <div className="success">{rpcMessage}</div>}
        {testResult && <div className="success">{testResult}</div>}
        {rpcError && <div className="error">{rpcError}</div>}
      </div>

      <div className="card">
        <h3 style={{ marginTop: 0, fontSize: '16px' }}>Watched Tokens</h3>
        {watchedTokens.length === 0 && (
          <div style={{ fontSize: '12px', color: '#666' }}>
            No watched tokens on this network yet. Dapps can add tokens via <code>animica_watchAsset</code> / <code>animica_addToken</code>.
          </div>
        )}
        {watchedTokens.length > 0 && (
          <div style={{ display: 'grid', gap: '8px' }}>
            {watchedTokens.map((token) => (
              <div key={`${token.chainId}:${token.address.toLowerCase()}`} style={{ padding: '10px', background: '#f9f9f9', borderRadius: '8px' }}>
                <div style={{ fontWeight: 600 }}>{token.symbol} {token.name ? `· ${token.name}` : ''}</div>
                <div style={{ fontSize: '11px', color: '#666' }}>Decimals: {token.decimals} · Type: {token.type}</div>
                <div style={{ fontSize: '11px', color: '#888', wordBreak: 'break-all' }}>{token.address}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card">
        <h3 style={{ marginTop: 0, fontSize: '16px' }}>About</h3>

        <div style={{ fontSize: '13px', color: '#666', lineHeight: '1.6' }}>
          <div style={{ marginBottom: '8px' }}>
            <strong>Animica Wallet</strong> v1.0.0
          </div>
          <div style={{ marginBottom: '8px' }}>
            Post-quantum secure wallet for the Animica blockchain
          </div>
          <div style={{ marginTop: '12px', padding: '12px', background: '#f9f9f9', borderRadius: '8px' }}>
            <div style={{ fontWeight: 600, marginBottom: '4px' }}>Security Features:</div>
            <ul style={{ margin: '4px 0', paddingLeft: '20px', fontSize: '12px' }}>
              <li>Dilithium3 (ML-DSA-65) signatures</li>
              <li>AES-GCM vault encryption</li>
              <li>PBKDF2 key derivation (100k iterations)</li>
              <li>Auto-lock timer</li>
            </ul>
          </div>
        </div>
      </div>

      <div className="card">
        <h3 style={{ marginTop: 0, fontSize: '16px' }}>Import/Export</h3>

        <input
          ref={fileInputRef}
          type="file"
          accept="application/json,.json"
          style={{ display: 'none' }}
          onChange={handleImportFile}
        />

        <button className="button button-secondary" onClick={triggerImportPicker} disabled={isBusy}>
          {isBusy ? 'Working…' : 'Import wallets.json'}
        </button>

        <button className="button button-secondary" onClick={handleExportWallets} disabled={isBusy}>
          {isBusy ? 'Working…' : 'Export wallets.json'}
        </button>

        {importSummary && (
          <div className="success" style={{ color: '#22543d' }}>
            <div><strong>Import summary</strong></div>
            <div>Imported: {importSummary.imported_count}</div>
            <div>Skipped duplicates: {importSummary.skipped_duplicates}</div>
            <div>Upgraded watch-only: {importSummary.upgraded_watch_only}</div>
            <div>Total accounts: {importSummary.total_accounts}</div>
            {importSummary.invalid_records.length > 0 && (
              <div style={{ marginTop: '8px' }}>
                <div><strong>Invalid records</strong></div>
                <ul style={{ margin: '4px 0 0', paddingLeft: '16px' }}>
                  {importSummary.invalid_records.map((record, idx) => (
                    <li key={`${record.index}-${idx}`}>
                      [{record.index}] {record.label ? `${record.label}: ` : ''}{record.reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        <div style={{ marginTop: '12px', padding: '12px', background: '#fff4e6', borderRadius: '8px', fontSize: '12px', color: '#9a6700' }}>
          <strong>⚠️ Warning:</strong> Exported files contain private keys. Store them securely!
        </div>
      </div>

      <div className="card">
        <h3 style={{ marginTop: 0, fontSize: '16px', color: '#c33' }}>Danger Zone</h3>

        <button
          className="button"
          style={{ background: '#c33' }}
          onClick={() => {
            if (confirm('Are you sure? This will delete all accounts and data!')) {
              chrome.storage.local.clear();
              location.reload();
            }
          }}
        >
          Reset Wallet
        </button>
      </div>
    </div>
  );
}

export default SettingsTab;
