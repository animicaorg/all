import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, Power, Gauge } from 'lucide-react';
import HashrateChart from '../components/Charts/HashrateChart';
import useMinerDetail from '../hooks/useMinerDetail';

const MinerDetailPage = () => {
  const { workerId = '' } = useParams();
  const { data } = useMinerDetail(workerId);

  const chartData = (data?.hashrate_timeseries ?? []).map(([timestamp, value]) => ({ timestamp, value }));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/miners" className="text-white/60 hover:text-white">
            <ArrowLeft />
          </Link>
          <div>
            <p className="text-sm text-white/60">Worker</p>
            <h2 className="text-2xl font-semibold">{data?.worker_name ?? workerId}</h2>
            <p className="text-white/60 text-sm">{data?.address}</p>
          </div>
        </div>
        <div className="flex gap-3">
          <div className="px-3 py-2 rounded-lg bg-emerald-500/20 border border-emerald-500/40 text-emerald-200 text-sm flex items-center gap-2">
            <Power className="h-4 w-4" /> Online
          </div>
          <div className="px-3 py-2 rounded-lg bg-white/10 text-sm flex items-center gap-2">
            <Gauge className="h-4 w-4" /> Difficulty {data?.current_difficulty ?? 0}
          </div>
        </div>
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <HashrateChart data={chartData} title="Worker hashrate" />
        </div>
        <div className="glass rounded-2xl p-4 shadow-card space-y-2 text-sm text-white/80">
          <div className="flex justify-between">
            <span>Last share</span>
            <span>{data?.last_share?.time ? new Date(data.last_share.time).toLocaleString() : '—'}</span>
          </div>
          <div className="flex justify-between">
            <span>Status</span>
            <span className={data?.last_share?.status === 'accepted' ? 'text-emerald-400' : 'text-red-400'}>
              {data?.last_share?.status ?? '—'}
            </span>
          </div>
          <div className="flex justify-between">
            <span>Accepted</span>
            <span>{data?.shares_accepted ?? 0}</span>
          </div>
          <div className="flex justify-between">
            <span>Rejected</span>
            <span>{data?.shares_rejected ?? 0}</span>
          </div>
          <div className="flex justify-between">
            <span>Connected since</span>
            <span>{data?.connected_since ?? '—'}</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default MinerDetailPage;
