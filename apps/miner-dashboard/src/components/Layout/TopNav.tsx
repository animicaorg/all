import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import usePoolSummary from '../../hooks/usePoolSummary';

const StatusDot = ({ status }: { status: 'ok' | 'error' }) => (
  <span
    className={`inline-block h-3 w-3 rounded-full mr-2 ${
      status === 'ok' ? 'bg-emerald-400' : 'bg-red-500'
    }`}
  />
);

const TopNav = () => {
  const { data: summary } = usePoolSummary();
  const { data: health } = useQuery({ queryKey: ['health'], queryFn: api.getHealth, refetchInterval: 8000 });

  return (
    <header className="sticky top-0 z-20 flex items-center justify-between px-6 py-4 border-b border-white/10 bg-black/60 backdrop-blur">
      <div className="flex items-center gap-3">
        <div className="h-10 w-10 rounded-xl bg-gradient-to-br from-neon to-indigo-500 flex items-center justify-center font-bold">
          Ξ
        </div>
        <div>
          <p className="text-sm text-white/60">Animica Mining</p>
          <h1 className="text-xl font-semibold">{summary?.pool_name ?? 'Miner Dashboard'}</h1>
        </div>
      </div>
      <div className="flex items-center gap-3 text-sm">
        <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-white/5 border border-white/10">
          <StatusDot status={health?.status === 'ok' ? 'ok' : 'error'} />
          <span className="text-white/80">{health?.status === 'ok' ? 'Healthy' : 'Degraded'}</span>
        </div>
        <div className="hidden sm:flex items-center gap-2 text-white/70">
          <span className="text-xs uppercase tracking-wide">Chain</span>
          <span className="font-medium">{summary?.network ?? '—'}</span>
        </div>
      </div>
    </header>
  );
};

export default TopNav;
