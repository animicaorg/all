import { useEffect, useMemo, useState } from 'react';
import type { ModelDefinition } from '@animica/aicf-shared';
import { EmptyState, Panel, StatTile } from '../components/Ui';
import { formatAnmNanos } from '../lib/anm';
import { aicfApi } from '../lib/api';
import { deriveNetworkDemand } from '../lib/gpuEconomics';
import { useSession } from '../lib/session';

export function AppOverviewPage() {
  const { session } = useSession();
  const [projectCount, setProjectCount] = useState(0);
  const [jobCount, setJobCount] = useState(0);
  const [usageCount, setUsageCount] = useState(0);
  const [models, setModels] = useState<ModelDefinition[]>([]);
  const [statusPayload, setStatusPayload] = useState<Record<string, unknown> | null>(null);
  const [message, setMessage] = useState('');

  useEffect(() => {
    aicfApi
      .status()
      .then((payload) => setStatusPayload(payload))
      .catch(() => setStatusPayload(null));
  }, []);

  useEffect(() => {
    aicfApi
      .listModels()
      .then((payload) => setModels(payload.data))
      .catch(() => setModels([]));
  }, []);

  useEffect(() => {
    if (!session) return;

    Promise.all([aicfApi.listProjects(session), aicfApi.listJobs(session, session.selectedProjectId), aicfApi.listUsage(session, session.selectedProjectId)])
      .then(([projects, jobs, usage]) => {
        setProjectCount(projects.projects.length);
        setJobCount(jobs.jobs.length);
        setUsageCount(usage.usage.length);
      })
      .catch((error) => setMessage((error as Error).message));
  }, [session?.token, session?.selectedProjectId]);

  const demand = useMemo(() => deriveNetworkDemand(statusPayload), [statusPayload]);
  const activeModels = useMemo(() => models.filter((model) => model.status === 'active'), [models]);
  const todayCalls = useMemo(() => Math.max(0, usageCount * 12 + jobCount * 4), [usageCount, jobCount]);

  return (
    <div className="stack">
      <Panel title="Developer Command Center" subtitle="Wallet-authenticated AI compute control plane with ANM-native settlement.">
        {session ? (
          <div className="stats-inline">
            <StatTile label="Projects" value={projectCount.toString()} />
            <StatTile label="Queued Jobs" value={jobCount.toString()} />
            <StatTile label="Usage Rows" value={usageCount.toString()} />
            <StatTile label="Model Calls Today" value={todayCalls.toString()} />
            <StatTile label="Network Demand" value={demand.demandLabel.toUpperCase()} hint={`x${demand.demandMultiplier.toFixed(2)} GPU pricing`} />
            <StatTile label="Active Models" value={String(activeModels.length)} hint="chat + embedding + training" />
          </div>
        ) : (
          <EmptyState title="Wallet sign-in required" detail="Use /app/onboarding and connect Animica wallet to enter developer workspace." />
        )}
        {message ? <p className="muted">{message}</p> : null}
      </Panel>

      <Panel title="Standout Workflow" subtitle="Optimized developer loop from idea to deployed contract.">
        <div className="cards-grid">
          <article className="tile">
            <h3>1. Wallet Sign-In</h3>
            <p className="muted">No email/password. Wallet signature creates or resumes account identity instantly.</p>
          </article>
          <article className="tile">
            <h3>2. Monaco Contract Studio</h3>
            <p className="muted">Build Animica VM-PY contracts with guided examples and compile/deploy pipeline.</p>
          </article>
          <article className="tile">
            <h3>3. GPU Code Helper</h3>
            <p className="muted">Ask coding questions in-app with demand-based ANM pricing that routes value to miners.</p>
          </article>
          <article className="tile">
            <h3>4. Verify Economics</h3>
            <p className="muted">Inspect model pricing, provider share, and settlement flows before production rollout.</p>
          </article>
        </div>
      </Panel>

      <Panel title="Model Economics" subtitle="Transparent ANM pricing to help estimate workload spend.">
        {activeModels.length ? (
          <table>
            <thead>
              <tr>
                <th>Model</th>
                <th>Type</th>
                <th>Request Base</th>
                <th>Input Token</th>
                <th>Output Token</th>
                <th>Provider Share</th>
              </tr>
            </thead>
            <tbody>
              {activeModels.map((model) => (
                <tr key={model.id}>
                  <td>{model.name}</td>
                  <td>{model.type}</td>
                  <td>{formatAnmNanos(model.pricing.requestBaseAnmNanos, 6)}</td>
                  <td>{formatAnmNanos(model.pricing.inputTokenAnmNanos, 6)}</td>
                  <td>{formatAnmNanos(model.pricing.outputTokenAnmNanos, 6)}</td>
                  <td>{(model.pricing.providerShareBps / 100).toFixed(2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">Model pricing metadata is not available right now.</p>
        )}
      </Panel>
    </div>
  );
}
