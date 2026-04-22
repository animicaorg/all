import { useEffect, useMemo, useState } from 'react';
import { addUsdanToWallet, connectWallet, getAccounts, getAnimicaProvider, getChainId, onAccountsChanged, signMessage } from '../lib/wallet';
import { useSession } from '../lib/session';
import { usdanApi } from '../lib/api';

const expectedChainId = Number(import.meta.env.VITE_ANIMICA_CHAIN_ID ?? 1337);
const tokenAddress = String(import.meta.env.VITE_USDAN_TOKEN_ADDRESS ?? 'anim1_usdan_token');

export function WalletConnectPanel() {
  const { session, setSession } = useSession();
  const [walletAddress, setWalletAddress] = useState<string>('');
  const [chainId, setChainId] = useState<number | null>(null);
  const [status, setStatus] = useState<string>('Wallet disconnected');
  const [userId, setUserId] = useState<string>('');

  useEffect(() => {
    let active = true;
    const hydrate = async () => {
      const [accounts, currentChainId] = await Promise.all([getAccounts(), getChainId()]);
      if (!active) return;
      setWalletAddress(accounts[0] ?? session?.walletAddress ?? '');
      setChainId(currentChainId);
    };
    hydrate().catch(() => undefined);

    const off = onAccountsChanged((accounts) => {
      setWalletAddress(accounts[0] ?? '');
    });
    return () => {
      active = false;
      off();
    };
  }, [session?.walletAddress]);

  const providerAvailable = Boolean(getAnimicaProvider());
  const chainOk = chainId === null ? true : chainId === expectedChainId;

  const headline = useMemo(() => {
    if (!providerAvailable) return 'Animica wallet not detected';
    if (walletAddress) return `Connected: ${walletAddress}`;
    return 'Connect your Animica wallet';
  }, [providerAvailable, walletAddress]);

  async function handleConnect() {
    try {
      const accounts = await connectWallet();
      setWalletAddress(accounts[0] ?? '');
      setChainId(await getChainId());
      setStatus(accounts.length > 0 ? 'Wallet connected' : 'No wallet accounts found');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Wallet connection failed');
    }
  }

  async function handleSessionStart() {
    try {
      if (!walletAddress) throw new Error('Connect wallet first');
      if (!userId) throw new Error('Enter a user ID');

      const message = `USDAN login:${userId}:${walletAddress}:${Date.now()}`;
      const signature = await signMessage(message, walletAddress);
      if (!signature) throw new Error('Wallet does not support message signing in current mode');

      const result = await usdanApi.createWalletSession({
        userId,
        walletAddress,
        chainId: chainId ?? expectedChainId,
        message,
        signature
      });
      setSession(result);
      setStatus('Session created');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Session creation failed');
    }
  }

  async function handleAddToken() {
    const ok = await addUsdanToWallet({ tokenAddress, symbol: 'USDAN', decimals: 6 });
    setStatus(ok ? 'USDAN added to wallet watchlist' : 'Unable to add USDAN asset');
  }

  return (
    <section className="wallet-panel">
      <div>
        <h2>{headline}</h2>
        <p className="wallet-meta">
          Chain: {chainId ?? 'unknown'}
          {!chainOk ? ` (expected ${expectedChainId})` : ''}
        </p>
      </div>

      <div className="wallet-actions">
        <button onClick={handleConnect}>Connect Wallet</button>
        <button onClick={handleAddToken}>Add USDAN</button>
        <button onClick={() => setSession(null)}>Disconnect Session</button>
      </div>

      <div className="session-row">
        <input value={userId} onChange={(e) => setUserId(e.target.value)} placeholder="user id" />
        <button onClick={handleSessionStart}>Create Session</button>
      </div>

      <p className="wallet-status">{status}</p>
      {session ? <p className="wallet-status">Active session: {session.userId}</p> : null}
    </section>
  );
}
