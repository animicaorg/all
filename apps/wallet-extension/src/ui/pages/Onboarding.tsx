import React, { useState } from 'react';

interface OnboardingProps {
  onComplete: () => void;
}

function Onboarding({ onComplete }: OnboardingProps) {
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleCreate() {
    setError('');

    if (password.length < 8) {
      setError('Password must be at least 8 characters');
      return;
    }

    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    setLoading(true);

    try {
      const result = await chrome.runtime.sendMessage({
        method: 'wallet_create',
        params: { password },
      });

      if (result?.error) {
        throw new Error(result.error);
      }

      onComplete();
    } catch (err: any) {
      setError(err.message || 'Failed to create wallet');
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
      
      <div className="content">
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Welcome to Animica</h2>
          <p style={{ color: '#666', fontSize: '14px' }}>
            Create a new wallet to get started with the post-quantum blockchain.
          </p>

          <label className="label">Password</label>
          <input
            type="password"
            className="input"
            placeholder="Enter password (min 8 characters)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={loading}
          />

          <label className="label">Confirm Password</label>
          <input
            type="password"
            className="input"
            placeholder="Confirm password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            disabled={loading}
            onKeyPress={(e) => {
              if (e.key === 'Enter' && !loading) {
                handleCreate();
              }
            }}
          />

          {error && <div className="error">{error}</div>}

          <button
            className="button"
            onClick={handleCreate}
            disabled={loading}
          >
            {loading ? 'Creating Wallet...' : 'Create Wallet'}
          </button>

          <div style={{ marginTop: '16px', padding: '12px', background: '#fff4e6', borderRadius: '8px', fontSize: '13px', color: '#9a6700' }}>
            <strong>⚠️ Security Notice:</strong> Your password encrypts your wallet. Make sure to remember it - it cannot be recovered!
          </div>
        </div>
      </div>
    </div>
  );
}

export default Onboarding;
