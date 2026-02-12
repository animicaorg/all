import React, { useState, useEffect } from 'react';
import Onboarding from './pages/Onboarding';
import Unlock from './pages/Unlock';
import Home from './pages/Home';

function App() {
  const [hasVault, setHasVault] = useState<boolean | null>(null);
  const [isLocked, setIsLocked] = useState<boolean>(true);

  useEffect(() => {
    checkWalletStatus();
  }, []);

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

  return <Home onLock={() => setIsLocked(true)} />;
}

export default App;
