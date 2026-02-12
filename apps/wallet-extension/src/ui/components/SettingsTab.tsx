import React, { useRef, useState } from 'react';

interface SettingsTabProps {
  network: any;
  onNetworkChange: () => void;
  onAccountsChanged: () => void;
}

function SettingsTab({ network, onNetworkChange, onAccountsChanged }: SettingsTabProps) {
  const [selectedNetwork, setSelectedNetwork] = useState(network?.id || 'mainnet');
  const [isBusy, setIsBusy] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function handleNetworkChange(networkId: string) {
    try {
      await chrome.runtime.sendMessage({
        method: 'wallet_switchNetwork',
        params: { networkId },
      });

      setSelectedNetwork(networkId);
      onNetworkChange();
    } catch (error) {
      console.error('Failed to switch network:', error);
    }
  }

  function triggerImportPicker() {
    fileInputRef.current?.click();
  }

  async function handleImportFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;

    try {
      setIsBusy(true);
      const json = await file.text();

      const result = await chrome.runtime.sendMessage({
        method: 'wallet_importWalletsJson',
        params: { json },
      });

      if (result?.error) {
        throw new Error(result.error);
      }

      alert(`Imported ${result.imported} wallet(s). Total accounts: ${result.total}.`);
      onAccountsChanged();
    } catch (error: any) {
      console.error('Failed to import wallets.json:', error);
      alert(`Import failed: ${error?.message || 'Unknown error'}`);
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
                {netId === 'mainnet' && 'Chain ID: 1 • Primary: 144.126.133.21'}
                {netId === 'testnet' && 'Chain ID: 2 • Local testnet'}
                {netId === 'devnet' && 'Chain ID: 1337 • Local development'}
              </div>
            </div>
          ))}
        </div>
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
              window.location.reload();
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
