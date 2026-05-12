import { Link } from 'react-router-dom';
import { Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { shortAddress } from '../lib/anm';
import { deriveNetworkDemand } from '../lib/gpuEconomics';
import { useSession } from '../lib/session';
import { signInWithWallet, type WalletAuthRole } from '../lib/walletAuth';
import { getAnimicaProvider } from '../lib/wallet';
import { useEffect, useMemo, useState } from 'react';

export function OnboardingPage() {
  const { session, setSession } = useSession();
  const [role, setRole] = useState<WalletAuthRole>('developer');
  const [message, setMessage] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [networkStatus, setNetworkStatus] = useState<Record<string, unknown> | null>(null);
  const [hasWalletProvider, setHasWalletProvider] = useState(false);

  useEffect(() => {
    aicfApi
      .status()
      .then((payload) => setNetworkStatus(payload))
      .catch(() => {
        setNetworkStatus(null);
      });
  }, []);

  useEffect(() => {
    const syncProviderState = () => {
      setHasWalletProvider(Boolean(getAnimicaProvider()));
    };

    syncProviderState();
    const pollId = window.setInterval(syncProviderState, 1_000);
    const onInit = () => syncProviderState();
    window.addEventListener('animica#initialized', onInit as EventListener);

    return () => {
      window.clearInterval(pollId);
      window.removeEventListener('animica#initialized', onInit as EventListener);
    };
  }, []);

  const demand = useMemo(() => deriveNetworkDemand(networkStatus), [networkStatus]);

  async function signIn() {
    setIsSubmitting(true);
    setMessage('');
    try {
      const result = await signInWithWallet(role);
      setSession(result.session);
      setMessage(
        `${result.createdAccount ? 'Created account' : 'Signed in'} as ${result.session.user.role} for ${shortAddress(
          result.address
        )} on chain ${result.chainId}.`
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="stack">
      <Panel
        title="Wallet Onboarding"
        subtitle="AICF now uses wallet-only developer/provider authentication. Connect Animica wallet to continue."
      >
        <div className="auth-role-grid">
          <button
            className={role === 'developer' ? 'role-card active' : 'role-card'}
            onClick={() => setRole('developer')}
            type="button"
          >
            <strong>Developer</strong>
            <span>Build, deploy, and run smart-contract AI workloads.</span>
          </button>
          <button
            className={role === 'provider' ? 'role-card active' : 'role-card'}
            onClick={() => setRole('provider')}
            type="button"
          >
            <strong>GPU Provider</strong>
            <span>Contribute hardware and earn ANM from model execution demand.</span>
          </button>
        </div>

        <div className="onboarding-actions">
          <button disabled={isSubmitting} onClick={signIn} type="button">
            {isSubmitting ? 'Connecting wallet...' : `Connect wallet and sign in as ${role}`}
          </button>
          <p className="muted">
            {hasWalletProvider
              ? 'Wallet extension detected. Signature will create or resume your AICF account automatically.'
              : 'No Animica wallet provider detected. Install/enable wallet extension and refresh.'}
          </p>
          <p className="muted">
            Admin access still uses <Link to="/admin/login">admin login</Link>.
          </p>
        </div>

        <div className="stats-inline">
          <div className="stat-tile">
            <span>GPU Demand</span>
            <strong>{demand.demandLabel.toUpperCase()}</strong>
            <small>x{demand.demandMultiplier.toFixed(2)} helper pricing</small>
          </div>
          <div className="stat-tile">
            <span>Providers</span>
            <strong>{demand.counts.providers}</strong>
            <small>{demand.counts.nodes} nodes online</small>
          </div>
          <div className="stat-tile">
            <span>Queue Units</span>
            <strong>{demand.counts.jobs + demand.counts.contractJobs + demand.counts.agentTasks}</strong>
            <small>jobs + contract jobs + agent tasks</small>
          </div>
        </div>

        {message ? <p className="muted">{message}</p> : null}
      </Panel>

      {session ? (
        <Panel title="Connected Session" subtitle="Wallet-backed access is active for this browser session.">
          <div className="stats-inline">
            <div className="stat-tile">
              <span>Role</span>
              <strong>{session.user.role}</strong>
              <small>{session.user.email}</small>
            </div>
            <div className="stat-tile">
              <span>Wallet</span>
              <strong>{shortAddress(session.user.wallet?.address ?? 'not linked')}</strong>
              <small>chain {session.user.wallet?.chainId ?? 'n/a'}</small>
            </div>
            <div className="stat-tile">
              <span>Next</span>
              <strong>{session.user.role === 'provider' ? 'Provider Console' : 'Developer Console'}</strong>
              <small>
                <Link to={session.user.role === 'provider' ? '/provider' : '/app'}>
                  Open dashboard
                </Link>
              </small>
            </div>
          </div>
        </Panel>
      ) : null}
    </div>
  );
}
