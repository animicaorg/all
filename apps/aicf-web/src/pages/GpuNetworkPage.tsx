import { useEffect, useMemo, useState } from 'react';
import type { JobRecord, ModelDefinition, ProviderNode } from '@animica/aicf-shared';
import { EmptyState, Panel, StatTile } from '../components/Ui';
import { formatAnmNanos } from '../lib/anm';
import { aicfApi } from '../lib/api';
import { deriveNetworkDemand, deriveProviderGpuStats } from '../lib/gpuEconomics';
import { useSession } from '../lib/session';

export function GpuNetworkPage() {
  const { session } = useSession();
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);
  const [models, setModels] = useState<ModelDefinition[]>([]);
  const [nodes, setNodes] = useState<ProviderNode[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [message, setMessage] = useState('');

  useEffect(() => {
    let active = true;

    async function load() {
      try {
        const [statusPayload, modelPayload] = await Promise.all([aicfApi.status(), aicfApi.listModels()]);
        if (!active) return;
        setStatus(statusPayload);
        setModels(modelPayload.data);

        if (session?.user.role === 'provider') {
          const [nodePayload, jobPayload] = await Promise.all([aicfApi.listNodes(session), aicfApi.listProviderJobs(session)]);
          if (!active) return;
          setNodes(nodePayload.nodes);
          setJobs(jobPayload.jobs);
        }
      } catch (error) {
        if (active) {
          setMessage((error as Error).message);
        }
      }
    }

    load().catch(() => undefined);

    return () => {
      active = false;
    };
  }, [session]);

  const demand = useMemo(() => deriveNetworkDemand(status), [status]);
  const providerStats = useMemo(() => deriveProviderGpuStats(nodes, jobs), [nodes, jobs]);

  return (
    <div className="stack">
      <Panel title="GPU Network Stats" subtitle="Real-time demand signals and miner reward economics for Animica compute providers.">
        <div className="stats-inline">
          <StatTile label="Demand" value={demand.demandLabel.toUpperCase()} hint={`multiplier x${demand.demandMultiplier.toFixed(2)}`} />
          <StatTile label="Providers" value={String(demand.counts.providers)} hint={`${demand.counts.nodes} nodes`} />
          <StatTile
            label="Queued Work"
            value={String(demand.counts.jobs + demand.counts.contractJobs + demand.counts.agentTasks)}
            hint="jobs + contract jobs + agent tasks"
          />
          <StatTile label="Estimated GPU Slots" value={String(demand.estimatedGpuSlots)} hint="provider/node weighted" />
        </div>
      </Panel>

      <Panel title="Model Pricing and Miner Share" subtitle="Provider share of usage charges determines ANM flow back to GPU miners.">
        {models.length ? (
          <table>
            <thead>
              <tr>
                <th>Model</th>
                <th>Type</th>
                <th>Provider Share</th>
                <th>Treasury Share</th>
                <th>Request Base</th>
                <th>Input Token</th>
                <th>Output Token</th>
              </tr>
            </thead>
            <tbody>
              {models
                .filter((model) => model.status === 'active')
                .map((model) => (
                  <tr key={model.id}>
                    <td>{model.name}</td>
                    <td>{model.type}</td>
                    <td>{(model.pricing.providerShareBps / 100).toFixed(2)}%</td>
                    <td>{(model.pricing.treasuryShareBps / 100).toFixed(2)}%</td>
                    <td>{formatAnmNanos(model.pricing.requestBaseAnmNanos, 6)}</td>
                    <td>{formatAnmNanos(model.pricing.inputTokenAnmNanos, 6)}</td>
                    <td>{formatAnmNanos(model.pricing.outputTokenAnmNanos, 6)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        ) : (
          <EmptyState title="No active model pricing" detail="Model catalog is unavailable right now." />
        )}
      </Panel>

      <Panel
        title="Your Provider Fleet"
        subtitle="Visible when signed in as a provider. Includes live throughput and benchmark capacity."
      >
        {session?.user.role !== 'provider' ? (
          <EmptyState title="Provider view locked" detail="Sign in as provider to inspect node-level GPU telemetry." />
        ) : providerStats.totalNodes === 0 ? (
          <EmptyState title="No registered nodes" detail="Register hardware in /provider/hardware to unlock throughput analytics." />
        ) : (
          <div className="stats-inline">
            <StatTile label="Nodes" value={String(providerStats.totalNodes)} hint={`${providerStats.activeNodes} active`} />
            <StatTile label="GPUs" value={String(providerStats.totalGpus)} hint={`${providerStats.totalGpuMemoryGb} GB VRAM`} />
            <StatTile label="Avg Load" value={`${providerStats.averageLoadPercent.toFixed(1)}%`} hint={`queue ${providerStats.queueDepth}`} />
            <StatTile label="LLM Throughput" value={`${providerStats.llmTokensPerSecond} tok/s`} hint="aggregate" />
            <StatTile label="Embedding Throughput" value={`${providerStats.embeddingVectorsPerSecond} vec/s`} hint="aggregate" />
            <StatTile label="Benchmark" value={providerStats.averageBenchmarkScore.toFixed(1)} hint={`hotspot ${providerStats.strongestRegion}`} />
            <StatTile
              label="Job Health"
              value={`${providerStats.jobsRunning}/${providerStats.jobsCompleted}/${providerStats.jobsFailed}`}
              hint="running/completed/failed"
            />
          </div>
        )}
      </Panel>

      {message ? <p className="muted">{message}</p> : null}
    </div>
  );
}
