import { useMemo } from 'react';
import { Sparkles, Clock, PlugZap, Activity } from 'lucide-react';

import StatCard from '../components/Cards/StatCard';
import HashrateChart from '../components/Charts/HashrateChart';
import BlocksTable from '../components/Tables/BlocksTable';
import StratumConfigCard from '../components/Connection/StratumConfigCard';
import usePoolSummary from '../hooks/usePoolSummary';
import useBlocks from '../hooks/useBlocks';
import useMiners from '../hooks/useMiners';

const DashboardPage = () => {
  const { data: summary } = usePoolSummary();
  const { data: blocks } = useBlocks();
  const { data: miners } = useMiners();

  const hashrateSeries = useMemo(() => {
    const base = summary?.pool_hashrate ?? 0;
    return Array.from({ length: 24 }).map((_, idx) => ({
      timestamp: `${idx}`,
      value: Math.max(base * (0.9 + (idx % 5) * 0.02), 0),
    }));
  }, [summary]);

  return (
    <div className="space-y-6">
      <div className="card-grid">
        <StatCard label="Pool Hashrate" value={`${(summary?.pool_hashrate ?? 0).toFixed(2)} H/s`} icon={<Activity />} />
        <StatCard label="Online Workers" value={summary?.num_workers ?? '—'} icon={<PlugZap />} />
        <StatCard label="Height" value={summary?.height ?? '—'} icon={<Sparkles />} helper={`Last block ${summary?.last_block_hash ?? ''}`} />
        <StatCard label="Uptime" value={`${Math.floor((summary?.uptime_seconds ?? 0) / 3600)}h`} icon={<Clock />} />
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <HashrateChart data={hashrateSeries} title="Pool hashrate" />
        </div>
        <div className="glass rounded-2xl p-4 shadow-card h-full">
          <h3 className="text-white font-semibold mb-2">Current round</h3>
          <div className="space-y-2 text-sm text-white/80">
            <div className="flex justify-between">
              <span>Shares submitted</span>
              <span>{summary?.round_shares ?? 0}</span>
            </div>
            <div className="flex justify-between">
              <span>Round window</span>
              <span>{summary?.round_duration_seconds ?? 0}s</span>
            </div>
            <div className="flex justify-between">
              <span>Estimated reward</span>
              <span>{summary?.round_estimated_reward ?? '—'}</span>
            </div>
            <div className="flex justify-between">
              <span>Active miners</span>
              <span>{summary?.num_miners ?? '—'}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 space-y-4">
          <BlocksTable blocks={blocks?.items ?? []} />
        </div>
        <div className="lg:col-span-1 space-y-4">
          <StratumConfigCard endpoint={summary?.stratum_endpoint ?? 'stratum+tcp://localhost:3333'} />
          <div className="glass rounded-2xl p-4">
            <h3 className="font-semibold">Live miners</h3>
            <p className="text-sm text-white/60">{miners?.total ?? 0} workers connected</p>
            <ul className="mt-3 space-y-2 text-sm">
              {(miners?.items ?? []).slice(0, 4).map((miner) => (
                <li key={miner.worker_id} className="flex justify-between">
                  <span className="text-white/80">{miner.worker_name}</span>
                  <span className="text-white/60">{miner.hashrate_1m.toFixed(2)} H/s</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DashboardPage;
