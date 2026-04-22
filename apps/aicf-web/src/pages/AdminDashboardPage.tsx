import { useEffect, useState } from 'react';
import type { ContractDisputeRecord, ContractJobRecord, JobRecord } from '@animica/aicf-shared';
import { EmptyState, Panel, StatTile } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';

type AdminSection =
  | 'overview'
  | 'providers'
  | 'jobs'
  | 'disputes'
  | 'treasury'
  | 'model-routing'
  | 'feature-flags'
  | 'escrow'
  | 'rewards'
  | 'releases';

type TreasuryGrant = {
  id: string;
  projectId: string;
  amountAnmNanos: string;
  consumedAnmNanos: string;
  reason: string;
  createdAt: string;
  expiresAt?: string;
};

export function AdminDashboardPage({ section }: { section: AdminSection }) {
  const { session } = useSession();
  const [overview, setOverview] = useState<Record<string, unknown> | null>(null);
  const [providers, setProviders] = useState<Array<Record<string, unknown>>>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [contractJobs, setContractJobs] = useState<ContractJobRecord[]>([]);
  const [disputes, setDisputes] = useState<JobRecord[]>([]);
  const [contractDisputes, setContractDisputes] = useState<ContractDisputeRecord[]>([]);
  const [finalizationQueue, setFinalizationQueue] = useState<ContractJobRecord[]>([]);
  const [featureFlags, setFeatureFlags] = useState<Array<{ key: string; enabled: boolean; note?: string }>>([]);
  const [treasury, setTreasury] = useState<Record<string, unknown> | null>(null);
  const [grants, setGrants] = useState<TreasuryGrant[]>([]);
  const [models, setModels] = useState<Array<{ name: string; status: string }>>([]);
  const [providerArtifacts, setProviderArtifacts] = useState<
    Array<{
      platform: string;
      label: string;
      filename: string;
      version: string;
      size_bytes: number;
      sha256: string;
      url: string;
    }>
  >([]);
  const [message, setMessage] = useState('');
  const [pause, setPause] = useState(false);
  const [depositAmount, setDepositAmount] = useState('1000000000000');
  const [depositTxHash, setDepositTxHash] = useState('');
  const [depositNote, setDepositNote] = useState('top up treasury for default workloads');
  const [grantProjectId, setGrantProjectId] = useState('');
  const [grantAmount, setGrantAmount] = useState('50000000000');
  const [grantReason, setGrantReason] = useState('bootstrap default project usage');

  async function load() {
    if (!session || session.user.role !== 'admin') {
      setMessage('Admin role required');
      return;
    }

    if (section === 'overview') {
      const payload = await aicfApi.adminOverview(session);
      setOverview(payload);
    }

    if (section === 'providers' || section === 'rewards') {
      const payload = await aicfApi.adminProviders(session);
      setProviders(payload.providers);
    }

    if (section === 'jobs' || section === 'escrow') {
      const [payload, contractPayload] = await Promise.all([
        aicfApi.adminJobs(session),
        aicfApi.adminContractJobs(session)
      ]);
      setJobs(payload.jobs);
      setContractJobs(contractPayload.jobs);
    }

    if (section === 'disputes') {
      const [payload, contractPayload] = await Promise.all([
        aicfApi.adminDisputes(session),
        aicfApi.adminContractDisputes(session)
      ]);
      setDisputes(payload.disputes);
      setContractDisputes(contractPayload.disputes);
    }

    if (section === 'escrow') {
      const payload = await aicfApi.adminFinalizationQueue(session);
      setFinalizationQueue(payload.jobs);
    }

    if (section === 'feature-flags') {
      const payload = await aicfApi.adminFeatureFlags(session);
      setFeatureFlags(payload.flags);
    }

    if (section === 'treasury') {
      const payload = await aicfApi.adminTreasury(session);
      setTreasury(payload.treasury);
      setGrants(payload.grants as TreasuryGrant[]);
    }

    if (section === 'model-routing') {
      const payload = await aicfApi.adminModelRouting(session);
      setModels(payload.models.map((model) => ({ name: model.name, status: model.status })));
    }

    if (section === 'releases') {
      const payload = await aicfApi.listProviderDownloads();
      setProviderArtifacts(payload.manifest.items);
    }
  }

  useEffect(() => {
    load().catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token, section]);

  async function togglePause() {
    if (!session) return;
    try {
      const payload = await aicfApi.adminPause(session, pause, pause ? 'manual emergency pause' : 'resume after checks');
      setMessage(`Platform paused=${payload.paused}`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function toggleFlag(flag: { key: string; enabled: boolean }) {
    if (!session) return;
    try {
      await aicfApi.adminSetFeatureFlag(session, {
        key: flag.key,
        enabled: !flag.enabled,
        note: 'toggled from admin console'
      });
      await load();
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function resolveDispute(jobId: string) {
    if (!session) return;
    try {
      await aicfApi.adminResolveDispute(session, jobId, {
        action: 'uphold_provider',
        note: 'manual admin review'
      });
      await load();
      setMessage(`Resolved dispute ${jobId}`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function resolveContractDispute(disputeId: string) {
    if (!session) return;
    try {
      await aicfApi.adminResolveContractDispute(session, disputeId, {
        action: 'clear',
        note: 'manual admin review'
      });
      await load();
      setMessage(`Resolved contract dispute ${disputeId}`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function finalizeContractJob(jobId: string) {
    if (!session) return;
    try {
      await aicfApi.adminFinalizeContractJob(session, jobId);
      await load();
      setMessage(`Finalized contract job ${jobId}`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function depositTreasury() {
    if (!session) return;
    try {
      const payload = await aicfApi.adminDepositTreasury(session, {
        amountAnmNanos: depositAmount,
        sourceTxHash: depositTxHash || undefined,
        note: depositNote || undefined
      });
      setTreasury(payload.treasury);
      setMessage(`Treasury deposit recorded: ${depositAmount} ANM nanos`);
      await load();
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function allocateGrant() {
    if (!session) return;
    if (!grantProjectId.trim()) {
      setMessage('Project ID is required for grant allocation');
      return;
    }
    try {
      const payload = await aicfApi.adminGrant(session, {
        projectId: grantProjectId.trim(),
        amountAnmNanos: grantAmount,
        reason: grantReason
      });
      setMessage(`Grant allocated: ${String((payload as { grantId?: string }).grantId ?? 'created')}`);
      await load();
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  if (session?.user.role !== 'admin') {
    return (
      <Panel title="Admin Console" subtitle="Restricted">
        <p className="muted">Sign in with an admin account to access this section.</p>
      </Panel>
    );
  }

  return (
    <div className="stack">
      <Panel title={`Admin ${section}`} subtitle="Platform governance, safety, treasury, and routing controls">
        <div className="grid two">
          <label>
            Emergency pause
            <select value={pause ? '1' : '0'} onChange={(event) => setPause(event.target.value === '1')}>
              <option value="0">Resume</option>
              <option value="1">Pause</option>
            </select>
          </label>
          <button onClick={togglePause}>Apply pause state</button>
        </div>
      </Panel>

      {section === 'overview' && overview ? (
        <Panel title="Overview Metrics">
          <div className="stats-inline">
            <StatTile label="Providers" value={String((overview.providers as any)?.total ?? 0)} />
            <StatTile label="Jobs" value={String((overview.jobs as any)?.total ?? 0)} />
            <StatTile label="Disputes" value={String((overview.jobs as any)?.disputed ?? 0)} />
            <StatTile label="Treasury" value={String((overview.treasury as any)?.availableAnmNanos ?? 0)} />
          </div>
        </Panel>
      ) : null}

      {section === 'providers' && (
        <Panel title="Provider Moderation">
          {providers.length ? (
            <table>
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>State</th>
                  <th>Reputation</th>
                  <th>Stake</th>
                  <th>Rewards</th>
                </tr>
              </thead>
              <tbody>
                {providers.map((provider) => (
                  <tr key={String(provider.id)}>
                    <td>{String(provider.id)}</td>
                    <td>{String(provider.state)}</td>
                    <td>{String(provider.reputation)}</td>
                    <td>{String(provider.stakeAnm)}</td>
                    <td>{String(provider.rewardBalanceAnmNanos)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <EmptyState title="No providers" detail="Providers appear here after onboarding." />
          )}
        </Panel>
      )}

      {section === 'releases' && (
        <Panel title="Provider Release Artifacts">
          {providerArtifacts.length ? (
            <table>
              <thead>
                <tr>
                  <th>Platform</th>
                  <th>Label</th>
                  <th>Filename</th>
                  <th>Version</th>
                  <th>Size</th>
                  <th>SHA256</th>
                </tr>
              </thead>
              <tbody>
                {providerArtifacts.map((artifact) => (
                  <tr key={artifact.filename}>
                    <td>{artifact.platform}</td>
                    <td>{artifact.label}</td>
                    <td>{artifact.filename}</td>
                    <td>{artifact.version}</td>
                    <td>{artifact.size_bytes}</td>
                    <td style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.7rem' }}>{artifact.sha256}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <EmptyState title="No release artifacts" detail="Provider bundle manifest returned no items." />
          )}
        </Panel>
      )}

      {section === 'jobs' && (
        <Panel title="Global Jobs">
          <table>
            <thead>
              <tr>
                <th>Job</th>
                <th>Project</th>
                <th>Status</th>
                <th>Class</th>
                <th>Provider</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id}>
                  <td>{job.id}</td>
                  <td>{job.projectId}</td>
                  <td>{job.status}</td>
                  <td>{job.request.class}</td>
                  <td>{job.assignedProviderId ?? 'unassigned'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3>Contract Jobs</h3>
          <table>
            <thead>
              <tr>
                <th>Job</th>
                <th>Contract</th>
                <th>Mode</th>
                <th>State</th>
                <th>Budget</th>
              </tr>
            </thead>
            <tbody>
              {contractJobs.map((job) => (
                <tr key={job.id}>
                  <td>{job.id}</td>
                  <td>{job.contractAddress}</td>
                  <td>{job.mode}</td>
                  <td>{job.state}</td>
                  <td>{job.budgetAnmNanos}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      {section === 'disputes' && (
        <Panel title="Dispute Queue">
          {disputes.length ? (
            <div className="stack">
              {disputes.map((job) => (
                <article key={job.id} className="tile">
                  <h3>{job.id}</h3>
                  <p>{job.error ?? 'No dispute reason'}</p>
                  <button onClick={() => resolveDispute(job.id)}>Resolve dispute</button>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="No active disputes" detail="All disputes are currently resolved." />
          )}
          <h3>Contract Disputes</h3>
          {contractDisputes.length ? (
            <div className="stack">
              {contractDisputes.map((dispute) => (
                <article key={dispute.id} className="tile">
                  <h3>{dispute.id}</h3>
                  <p>
                    job={dispute.jobId} reason={dispute.reasonCode} status={dispute.status}
                  </p>
                  <button onClick={() => resolveContractDispute(dispute.id)}>Resolve contract dispute</button>
                </article>
              ))}
            </div>
          ) : (
            <p className="muted">No contract disputes.</p>
          )}
        </Panel>
      )}

      {section === 'treasury' && treasury ? (
        <Panel title="Treasury">
          <div className="stats-inline">
            <StatTile label="Available" value={String(treasury.availableAnmNanos ?? 0)} />
            <StatTile label="Subsidized" value={String(treasury.allocatedSubsidyAnmNanos ?? 0)} />
            <StatTile label="Provider payouts" value={String(treasury.paidProviderAnmNanos ?? 0)} />
            <StatTile label="Fees" value={String(treasury.protocolFeesAnmNanos ?? 0)} />
          </div>
          <div className="grid two">
            <div className="stack">
              <h3>Deposit ANM</h3>
              <label>
                Amount (ANM nanos)
                <input value={depositAmount} onChange={(event) => setDepositAmount(event.target.value)} />
              </label>
              <label>
                Source tx hash (optional)
                <input value={depositTxHash} onChange={(event) => setDepositTxHash(event.target.value)} />
              </label>
              <label>
                Note
                <input value={depositNote} onChange={(event) => setDepositNote(event.target.value)} />
              </label>
              <button onClick={depositTreasury}>Deposit to treasury</button>
            </div>

            <div className="stack">
              <h3>Power Default Workloads</h3>
              <label>
                Target project ID
                <input value={grantProjectId} onChange={(event) => setGrantProjectId(event.target.value)} />
              </label>
              <label>
                Grant amount (ANM nanos)
                <input value={grantAmount} onChange={(event) => setGrantAmount(event.target.value)} />
              </label>
              <label>
                Reason
                <input value={grantReason} onChange={(event) => setGrantReason(event.target.value)} />
              </label>
              <button onClick={allocateGrant}>Allocate grant</button>
            </div>
          </div>

          <h3>Recent Grants</h3>
          {grants.length ? (
            <table>
              <thead>
                <tr>
                  <th>Grant</th>
                  <th>Project</th>
                  <th>Amount</th>
                  <th>Consumed</th>
                  <th>Reason</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {grants.slice(0, 12).map((grant) => (
                  <tr key={grant.id}>
                    <td>{grant.id}</td>
                    <td>{grant.projectId}</td>
                    <td>{grant.amountAnmNanos}</td>
                    <td>{grant.consumedAnmNanos}</td>
                    <td>{grant.reason}</td>
                    <td>{grant.createdAt}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">No grants allocated yet.</p>
          )}
        </Panel>
      ) : null}

      {section === 'model-routing' && (
        <Panel title="Model Routing">
          {models.length ? (
            <table>
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {models.map((model) => (
                  <tr key={model.name}>
                    <td>{model.name}</td>
                    <td>{model.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">No models configured.</p>
          )}
        </Panel>
      )}

      {section === 'feature-flags' && (
        <Panel title="Feature Flags">
          <div className="stack">
            {featureFlags.map((flag) => (
              <article key={flag.key} className="tile compact">
                <div>
                  <h3>{flag.key}</h3>
                  <p>{flag.note ?? 'No note'}</p>
                </div>
                <button onClick={() => toggleFlag(flag)}>{flag.enabled ? 'Disable' : 'Enable'}</button>
              </article>
            ))}
          </div>
        </Panel>
      )}

      {section === 'escrow' && (
        <Panel title="Escrow / Settlements">
          <table>
            <thead>
              <tr>
                <th>Job</th>
                <th>Escrow</th>
                <th>Charge</th>
                <th>Refund</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id}>
                  <td>{job.id}</td>
                  <td>{job.settlement.escrowId ?? 'n/a'}</td>
                  <td>{job.settlement.chargedAnmNanos}</td>
                  <td>{job.settlement.refundedAnmNanos}</td>
                  <td>{job.settlement.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3>Contract Finalization Queue</h3>
          {finalizationQueue.length ? (
            <table>
              <thead>
                <tr>
                  <th>Contract Job</th>
                  <th>State</th>
                  <th>Mode</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {finalizationQueue.map((job) => (
                  <tr key={job.id}>
                    <td>{job.id}</td>
                    <td>{job.state}</td>
                    <td>{job.mode}</td>
                    <td>
                      <button onClick={() => finalizeContractJob(job.id)}>Finalize</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">No contract jobs pending finalization.</p>
          )}
        </Panel>
      )}

      {section === 'rewards' && (
        <Panel title="Reward Queue">
          <table>
            <thead>
              <tr>
                <th>Provider</th>
                <th>Reward Balance</th>
                <th>State</th>
              </tr>
            </thead>
            <tbody>
              {providers.map((provider) => (
                <tr key={String(provider.id)}>
                  <td>{String(provider.id)}</td>
                  <td>{String(provider.rewardBalanceAnmNanos)}</td>
                  <td>{String(provider.state)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      {message ? <p className="muted">{message}</p> : null}
    </div>
  );
}
