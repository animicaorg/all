import React, { useState, useEffect, useCallback } from 'react';
import Onboarding from './pages/Onboarding';
import Unlock from './pages/Unlock';
import Home from './pages/Home';
import ConnectApprove from './pages/ConnectApprove';

interface PendingAccount { address: string; label: string; }
interface PendingConnect {
  requestId: string;
  origin: string;
  createdAt: number;
  accounts: PendingAccount[];
}

function App() {
  const [hasVault, setHasVault] = useState<boolean | null>(null);
  const [isLocked, setIsLocked] = useState<boolean>(true);
  const [pending, setPending] = useState<PendingConnect | null>(null);

  const refreshPending = useCallback(async () => {
    try {
      const res = await chrome.runtime.sendMessage({ method: 'wallet_connectGetPending' });
      setPending(res?.pending || null);
    } catch {
      // Background unreachable — treat as no pending request.
      setPending(null);
    }
  }, []);

  useEffect(() => {
    checkWalletStatus();
    refreshPending();
    // Re-check periodically in case a request lands while the popup is open.
    const id = setInterval(refreshPending, 1500);
    return () => clearInterval(id);
  }, [refreshPending]);

  async function checkWalletStatus() {
    try {
      const vaultStatus = await chrome.runtime.sendMessage({ method: 'wallet_hasVault' });
      if (vaultStatus?.error) {
        throw new Error(vaultStatus.error);
      }
      setHasVault(vaultStatus.hasVault);

      if (vaultStatus.hasVault) {
        const lockStatus = await chrome.runtime.sendMessage({ method: 'wallet_isLocked' });
        if (lockStatus?.error) {
          throw new Error(lockStatus.error);
        }
        setIsLocked(lockStatus.isLocked);
      }
    } catch (error) {
      console.error('Error checking wallet status:', error);
    }
  }

  if (hasVault === null) {
    return <div className="app"><div className="loader"></div></div>;
  }

  if (!hasVault) {
    return <Onboarding onComplete={() => {
      setHasVault(true);
      setIsLocked(false);
    }} />;
  }

  if (isLocked) {
    return <Unlock onUnlock={() => setIsLocked(false)} />;
  }

  // Pending connect request takes priority — block until the user decides.
  if (pending) {
    return <ConnectApprove pending={pending} onResolved={() => setPending(null)} />;
  }

  return <Home onLock={() => setIsLocked(true)} />;
}

export default App;
