import { useState } from 'react';
import { Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';
import { connectWallet, getAnmBalance, getChainId, sendContractCall, signMessage } from '../lib/wallet';

export function WalletPage() {
  const { session, setSession } = useSession();
  const [walletAddress, setWalletAddress] = useState('');
  const [anmBalance, setAnmBalance] = useState('');
  const [stakeAmount, setStakeAmount] = useState('250000000000');
  const [message, setMessage] = useState('');

  async function connectAndLink() {
    if (!session) {
      setMessage('Sign in first');
      return;
    }
    try {
      const accounts = await connectWallet();
      if (!accounts.length) {
        setMessage('No accounts returned by wallet provider');
        return;
      }
      const chainId = (await getChainId()) ?? 1337;
      const signature = (await signMessage(`AICF wallet link for ${accounts[0]}`)) ?? '';
      const linked = await aicfApi.linkWallet(session, {
        address: accounts[0],
        chainId,
        signature
      });
      setSession({ ...session, user: linked.user });
      setWalletAddress(accounts[0]);
      const balance = await getAnmBalance(accounts[0]);
      if (balance) setAnmBalance(balance);
      setMessage(`Wallet linked: ${accounts[0]}`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function stakeWithWallet() {
    if (!session) {
      setMessage('Sign in first');
      return;
    }

    try {
      const governance = await aicfApi.governanceConfig(session);
      const wallet = session.user.wallet?.address;
      if (!wallet) {
        setMessage('Link wallet first');
        return;
      }

      const txHash = await sendContractCall({
        from: wallet,
        contractAddress: String(governance.config.stakeManagerAddress),
        method: 'stake_for_provider',
        args: {
          amount_anm_nanos: stakeAmount
        }
      });
      setMessage(txHash ? `Stake tx submitted: ${txHash}` : 'Stake call attempted but wallet did not return tx hash');
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  return (
    <div className="stack">
      <Panel title="Wallet & Contract Calls" subtitle="Direct browser wallet interaction with AICF smart contracts">
        <button onClick={connectAndLink}>Connect + link wallet</button>
        <p className="muted">Linked wallet: {session?.user.wallet?.address ?? 'none'}</p>
        <p className="muted">ANM balance (raw): {anmBalance || 'unknown'}</p>
      </Panel>

      <Panel title="Stake ANM" subtitle="Provider stake flow through AICFStakeManager contract">
        <label>
          Stake amount (ANM nanos)
          <input value={stakeAmount} onChange={(event) => setStakeAmount(event.target.value)} />
        </label>
        <button onClick={stakeWithWallet}>Submit stake transaction</button>
      </Panel>

      {walletAddress ? <p className="muted">Active account: {walletAddress}</p> : null}
      {message ? <p className="muted">{message}</p> : null}
    </div>
  );
}
