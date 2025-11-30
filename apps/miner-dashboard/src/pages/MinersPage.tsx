import { useState, useMemo } from 'react';
import MinersTable from '../components/Tables/MinersTable';
import useMiners from '../hooks/useMiners';
import DataState from '../components/Feedback/DataState';

const MinersPage = () => {
  const { data, isLoading, isError, error } = useMiners();
  const [search, setSearch] = useState('');

  const filtered = useMemo(() => {
    const miners = data?.items ?? [];
    if (!search) return miners;
    return miners.filter((miner) => miner.worker_name.toLowerCase().includes(search.toLowerCase()));
  }, [data, search]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold">Miners</h2>
          <p className="text-white/60 text-sm">Search and inspect worker performance.</p>
        </div>
        <input
          className="px-3 py-2 rounded-lg bg-white/10 border border-white/10 focus:outline-none"
          placeholder="Search workers..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <DataState isLoading={isLoading} isError={isError} errorMessage={error instanceof Error ? error.message : undefined}>
        <MinersTable miners={filtered} />
      </DataState>
    </div>
  );
};

export default MinersPage;
