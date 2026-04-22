import { useEffect, useMemo, useState } from 'react';
import { Panel, StatTile } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { deriveNetworkDemand } from '../lib/gpuEconomics';

export function StatusPage() {
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);
  const [message, setMessage] = useState('');

  useEffect(() => {
    aicfApi
      .status()
      .then(setStatus)
      .catch((error) => setMessage((error as Error).message));
  }, []);

  const demand = useMemo(() => deriveNetworkDemand(status), [status]);

  return (
    <div className="stack">
      <Panel title="Network Status" subtitle="Live scheduler, provider, settlement, and GPU demand health.">
        {status ? (
          <div className="stats-inline">
            <StatTile label="Service" value={String(status.status ?? 'unknown')} />
            <StatTile label="Paused" value={String(status.paused ?? 'false')} />
            <StatTile label="Users" value={String((status.health as any)?.counts?.users ?? 0)} />
            <StatTile label="Projects" value={String((status.health as any)?.counts?.projects ?? 0)} />
            <StatTile label="Providers" value={String((status.health as any)?.counts?.providers ?? 0)} />
            <StatTile label="Nodes" value={String((status.health as any)?.counts?.nodes ?? 0)} />
            <StatTile
              label="Queue Units"
              value={String(demand.counts.jobs + demand.counts.contractJobs + demand.counts.agentTasks)}
            />
            <StatTile label="GPU Demand" value={`${demand.demandLabel.toUpperCase()} x${demand.demandMultiplier.toFixed(2)}`} />
          </div>
        ) : (
          <p className="muted">Loading status...</p>
        )}
        {message ? <p className="muted">{message}</p> : null}
      </Panel>
    </div>
  );
}
