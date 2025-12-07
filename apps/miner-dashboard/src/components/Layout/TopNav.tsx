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
    <header className="sticky top-0 z-20 flex items-center justify-between px-4 sm:px-6 py-3 sm:py-4 border-b border-white/10 bg-black/60 backdrop-blur">
      <div className="flex items-center gap-2 sm:gap-3 min-w-0">
        <div className="h-8 w-8 sm:h-10 sm:w-10 flex-shrink-0 rounded-xl bg-gradient-to-br from-neon to-indigo-500 flex items-center justify-center font-bold text-sm sm:text-base">
          Ξ
        </div>
        <div className="min-w-0">
          <p className="text-xs sm:text-sm text-white/60">Animica Mining</p>
          <h1 className="text-base sm:text-xl font-semibold truncate">{summary?.pool_name ?? 'Miner Dashboard'}</h1>
        </div>
      </div>
      <div className="flex items-center gap-2 sm:gap-3 text-sm">
        <div className="flex items-center gap-1 sm:gap-2 px-2 sm:px-3 py-1 rounded-full bg-white/5 border border-white/10">
          <StatusDot status={health?.status === 'ok' ? 'ok' : 'error'} />
          <span className="text-white/80 text-xs sm:text-sm">{health?.status === 'ok' ? 'Healthy' : 'Degraded'}</span>
        </div>
        <div className="hidden md:flex items-center gap-2 text-white/70">
          <span className="text-xs uppercase tracking-wide">Chain</span>
          <span className="font-medium">{summary?.network ?? '—'}</span>
        </div>
      </div>
    </header>
  );
};

export default TopNav;
