import { useEffect, useMemo, useState } from 'react';
import type { JobRecord, ProviderNode, ProviderProfile } from '@animica/aicf-shared';
import { EmptyState, Panel, StatTile } from '../components/Ui';
import { formatAnmNanos, shortAddress } from '../lib/anm';
import { aicfApi } from '../lib/api';
import { deriveNetworkDemand, deriveProviderGpuStats } from '../lib/gpuEconomics';
import { useSession } from '../lib/session';
import { connectWallet, signMessage } from '../lib/wallet';

type Section =
  | 'overview'
  | 'onboarding'
  | 'hardware'
  | 'benchmarks'
  | 'jobs'
  | 'earnings'
  | 'reputation'
  | 'staking'
  | 'wallet'
  | 'settings';

const sectionLabels: Record<Section, string> = {
  overview: 'Provider Command Center',
  onboarding: 'Provider Onboarding',
  hardware: 'GPU Hardware Inventory',
  benchmarks: 'Benchmark Analytics',
  jobs: 'Assigned Workloads',
  earnings: 'Earnings and Rewards',
  reputation: 'Reputation and Reliability',
  staking: 'Stake Management',
  wallet: 'Wallet and Payouts',
  settings: 'Provider Settings'
};

export function ProviderDashboardPage({ section }: { section: Section }) {
  const { session } = useSession();
  const [provider, setProvider] = useState<ProviderProfile | null>(null);
  const [nodes, setNodes] = useState<ProviderNode[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [rewardBalance, setRewardBalance] = useState('0');
  const [statusPayload, setStatusPayload] = useState<Record<string, unknown> | null>(null);
  const [message, setMessage] = useState('');

  const [stakeAmount, setStakeAmount] = useState('300000000000');
  const [walletAddress, setWalletAddress] = useState(session?.user.wallet?.address ?? 'anm1providerwallet');
  const [daemonPublicKey, setDaemonPublicKey] = useState('provider-daemon-public-key');

  const demand = useMemo(() => deriveNetworkDemand(statusPayload), [statusPayload]);
  const gpuStats = useMemo(() => deriveProviderGpuStats(nodes, jobs), [nodes, jobs]);

  async function load() {
    if (!session) return;

    const calls: Array<Promise<void>> = [
      aicfApi
        .status()
        .then((payload) => setStatusPayload(payload))
        .catch(() => setStatusPayload(null)),
      aicfApi
        .providerProfile(session)
        .then((profile) => {
          setProvider(profile.provider);
          setRewardBalance(profile.rewardBalanceAnmNanos);
        })
        .catch(() => {
          setProvider(null);
          setRewardBalance('0');
        })
    ];

    if (section === 'jobs' || section === 'overview' || section === 'earnings') {
      calls.push(
        aicfApi
          .listProviderJobs(session)
          .then((jobPayload) => setJobs(jobPayload.jobs))
          .catch(() => setJobs([]))
      );
    }

    if (section === 'hardware' || section === 'benchmarks' || section === 'overview') {
      calls.push(
        aicfApi
          .listNodes(session)
          .then((nodePayload) => setNodes(nodePayload.nodes))
          .catch(() => setNodes([]))
      );
    }

    await Promise.all(calls);
  }

  useEffect(() => {
    load().catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token, section]);

  async function registerProvider() {
    if (!session) return;

    try {
      const currentWallet = walletAddress.trim() || session.user.wallet?.address;
      let chosenWallet = currentWallet ?? '';
      if (!chosenWallet) {
        const accounts = await connectWallet();
        chosenWallet = accounts[0] ?? '';
      }
      if (!chosenWallet) {
        throw new Error('Connect wallet first to register provider');
      }

      const signature = await signMessage(`Register provider for ${chosenWallet}`);
      if (!signature) {
        throw new Error('Wallet signature is required to register provider');
      }

      const result = await aicfApi.registerProvider(session, {
        walletAddress: chosenWallet,
        signature,
        daemonPublicKey
      });
      setProvider(result.provider);
      setWalletAddress(chosenWallet);
      setMessage(`Provider registered. Daemon token issued: ${result.daemonToken}`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function addNode() {
    if (!session) return;

    const nextGpuCount = Math.max(1, Math.min(8, gpuStats.totalGpus + 1));

    try {
      const response = await aicfApi.registerNode(session, {
        metadata: {
          name: `GPU Node ${gpuStats.totalNodes + 1}`,
          machineType: nextGpuCount >= 4 ? 'H100x4' : 'H100x1',
          os: 'ubuntu-24.04',
          labels: ['gpu', 'aicf', demand.demandLabel]
        },
        capabilities: {
          runtime: 'llm',
          gpus: nextGpuCount,
          gpuMemoryGb: nextGpuCount * 80,
          cpus: 32,
          ramGb: 256,
          region: 'eu-central',
          modelFamilies: ['aicf-chat-1', 'aicf-embed-1']
        },
        benchmark: {
          llmTokensPerSecond: 126 * nextGpuCount,
          embeddingVectorsPerSecond: 180 * nextGpuCount,
          trainingSamplesPerSecond: 220 * nextGpuCount,
          score: Math.min(99, 86 + nextGpuCount * 2)
        }
      });
      setNodes((prev) => [response.node, ...prev]);
      setMessage(`Node registered: ${response.node.id}`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function stake() {
    if (!session) return;
    try {
      const response = await aicfApi.providerStake(session, stakeAmount);
      setProvider(response.provider);
      setMessage(`Stake updated: ${response.provider.stakeAnm}`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function unstake() {
    if (!session) return;
    try {
      const response = await aicfApi.providerUnstake(session, stakeAmount);
      setProvider(response.provider);
      setMessage(`Unstaked ANM: current stake ${response.provider.stakeAnm}`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function claimRewards() {
    if (!session) return;
    try {
      const response = await aicfApi.claimProviderRewards(session);
      setMessage(`Rewards claimed: ${formatAnmNanos(response.claimedAnmNanos, 6)}`);
      await load();
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  return (
    <div className="stack">
      <Panel title={sectionLabels[section]} subtitle="Provider registry, GPU telemetry, queue demand, and ANM reward automation.">
        {provider ? (
          <div className="stats-inline">
            <StatTile label="Provider ID" value={provider.id} hint={provider.state} />
            <StatTile label="Wallet" value={shortAddress(provider.walletAddress)} hint="reward destination" />
            <StatTile label="Stake" value={formatAnmNanos(provider.stakeAnm, 6)} hint="security collateral" />
            <StatTile label="Reputation" value={provider.reputation.toFixed(1)} hint="0-100 reliability score" />
            <StatTile label="Rewards" value={formatAnmNanos(rewardBalance, 6)} hint="claimable now" />
            <StatTile label="Demand" value={demand.demandLabel.toUpperCase()} hint={`x${demand.demandMultiplier.toFixed(2)} payout pressure`} />
          </div>
        ) : (
          <div className="stack">
            <p className="muted">No provider profile found for this wallet session yet.</p>
            <label>
              Provider wallet
              <input value={walletAddress} onChange={(event) => setWalletAddress(event.target.value)} />
            </label>
            <label>
              Daemon public key
              <input value={daemonPublicKey} onChange={(event) => setDaemonPublicKey(event.target.value)} />
            </label>
            <button onClick={registerProvider} type="button">
              Register provider with wallet signature
            </button>
          </div>
        )}
      </Panel>

      {(section === 'onboarding' || section === 'overview') && (
        <Panel title="Provider Quickstart" subtitle="Live path from hardware registration to reward-bearing workloads.">
          <ol className="mini-list">
            <li>1. Connect wallet and register your provider identity.</li>
            <li>2. Install worker bundle from /downloads and configure daemon key.</li>
            <li>3. Register benchmarked GPU nodes and confirm heartbeat.</li>
            <li>4. Accept assignments and monitor queue pressure + load.</li>
            <li>5. Claim ANM rewards and adjust stake based on demand.</li>
          </ol>
          <pre>{`aicf-provider-worker init-config --config provider.config.json
aicf-provider-worker benchmark --config provider.config.json
aicf-provider-worker start --config provider.config.json`}</pre>
        </Panel>
      )}

      {(section === 'hardware' || section === 'benchmarks' || section === 'overview') && (
        <Panel title="GPU Fleet Telemetry" subtitle="Capacity, throughput, and queue pressure across your provider nodes.">
          <div className="row">
            <button onClick={addNode} type="button">
              Register benchmarked node
            </button>
          </div>

          <div className="stats-inline">
            <StatTile label="Nodes" value={String(gpuStats.totalNodes)} hint={`${gpuStats.activeNodes} active`} />
            <StatTile label="Total GPUs" value={String(gpuStats.totalGpus)} hint={`${gpuStats.totalGpuMemoryGb} GB VRAM`} />
            <StatTile label="Load" value={`${gpuStats.averageLoadPercent.toFixed(1)}%`} hint={`queue ${gpuStats.queueDepth}`} />
            <StatTile label="LLM Throughput" value={`${gpuStats.llmTokensPerSecond} tok/s`} hint="aggregate" />
            <StatTile label="Embedding" value={`${gpuStats.embeddingVectorsPerSecond} vec/s`} hint="aggregate" />
            <StatTile label="Benchmark" value={gpuStats.averageBenchmarkScore.toFixed(1)} hint={`hotspot ${gpuStats.strongestRegion}`} />
          </div>

          {nodes.length ? (
            <table>
              <thead>
                <tr>
                  <th>Node</th>
                  <th>Machine</th>
                  <th>Region</th>
                  <th>GPUs</th>
                  <th>LLM tok/s</th>
                  <th>Score</th>
                  <th>Load</th>
                  <th>Queue</th>
                </tr>
              </thead>
              <tbody>
                {nodes.map((node) => (
                  <tr key={node.id}>
                    <td>{node.metadata.name}</td>
                    <td>{node.metadata.machineType}</td>
                    <td>{node.capabilities.region}</td>
                    <td>{node.capabilities.gpus}</td>
                    <td>{node.benchmark.llmTokensPerSecond}</td>
                    <td>{node.benchmark.score}</td>
                    <td>{node.currentLoad.toFixed(2)}</td>
                    <td>{node.queueDepth}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <EmptyState title="No nodes" detail="Register worker nodes to expose GPU telemetry and route workloads." />
          )}
        </Panel>
      )}

      {(section === 'jobs' || section === 'overview' || section === 'earnings') && (
        <Panel title="Workload and Settlement Flow" subtitle="Execution state, budget consumption, and provider reward attribution.">
          <div className="stats-inline">
            <StatTile
              label="Job Health"
              value={`${gpuStats.jobsRunning}/${gpuStats.jobsCompleted}/${gpuStats.jobsFailed}`}
              hint="running/completed/failed"
            />
            <StatTile label="Claimable" value={formatAnmNanos(rewardBalance, 6)} hint="from finalized settlements" />
            <StatTile label="Demand Multiplier" value={`x${demand.demandMultiplier.toFixed(2)}`} hint="higher demand = higher fees" />
          </div>

          {jobs.length ? (
            <table>
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Class</th>
                  <th>Status</th>
                  <th>Budget</th>
                  <th>Charge</th>
                  <th>Reward</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id}>
                    <td>{job.id}</td>
                    <td>{job.request.class}</td>
                    <td>{job.status}</td>
                    <td>{formatAnmNanos(job.budget.maxAnmNanos, 6)}</td>
                    <td>{formatAnmNanos(job.settlement.chargedAnmNanos, 6)}</td>
                    <td>{formatAnmNanos(job.settlement.providerRewardAnmNanos, 6)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">No jobs assigned yet.</p>
          )}
        </Panel>
      )}

      {(section === 'staking' || section === 'earnings' || section === 'overview') && (
        <Panel title="Stake and Rewards" subtitle="Adjust security stake and harvest ANM as utilization increases.">
          <div className="grid two">
            <label>
              Amount (ANM nanos)
              <input value={stakeAmount} onChange={(event) => setStakeAmount(event.target.value)} />
            </label>
            <div className="stack">
              <button onClick={stake} type="button">
                Stake ANM
              </button>
              <button onClick={unstake} type="button">
                Unstake ANM
              </button>
              <button onClick={claimRewards} type="button">
                Claim rewards
              </button>
            </div>
          </div>
        </Panel>
      )}

      {message ? <p className="muted">{message}</p> : null}
    </div>
  );
}
