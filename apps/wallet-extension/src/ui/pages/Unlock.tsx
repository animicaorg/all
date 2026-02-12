import React, { useState } from 'react';

interface UnlockProps {
  onUnlock: () => void;
}

function Unlock({ onUnlock }: UnlockProps) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleUnlock() {
    setError('');
    setLoading(true);

    try {
      const result = await chrome.runtime.sendMessage({
        method: 'wallet_unlock',
        params: { password },
      });

      if (result?.error) {
        throw new Error(result.error);
      }

      onUnlock();
    } catch (err: any) {
      setError('Incorrect password');
      setPassword('');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app">
      <div className="header">
        <div className="header-logo">
          <img src="/icon-48.png" alt="Animica" />
          <span>Animica Wallet</span>
        </div>
      </div>
      
      <div className="content" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div className="card" style={{ width: '100%' }}>
          <div style={{ textAlign: 'center', marginBottom: '24px' }}>
            <img src="/icon-128.png" alt="Animica" style={{ width: '64px', height: '64px' }} />
            <h2 style={{ marginTop: '16px', marginBottom: '8px' }}>Welcome Back</h2>
            <p style={{ color: '#666', fontSize: '14px', margin: 0 }}>
              Enter your password to unlock your wallet
            </p>
          </div>

          <label className="label">Password</label>
          <input
            type="password"
            className="input"
            placeholder="Enter your password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={loading}
            autoFocus
            onKeyPress={(e) => {
              if (e.key === 'Enter' && !loading) {
                handleUnlock();
              }
            }}
          />

          {error && <div className="error">{error}</div>}

          <button
            className="button"
            onClick={handleUnlock}
            disabled={loading || !password}
          >
            {loading ? 'Unlocking...' : 'Unlock'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default Unlock;
