import usePoolSummary from '../hooks/usePoolSummary';

const SettingsPage = () => {
  const { data } = usePoolSummary();

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-semibold">Settings & About</h2>
      <div className="glass rounded-2xl p-4 shadow-card space-y-3 text-sm text-white/80">
        <div className="flex justify-between">
          <span>Stratum endpoint</span>
          <span className="font-medium text-white">{data?.stratum_endpoint ?? 'stratum+tcp://localhost:3333'}</span>
        </div>
        <div className="flex justify-between">
          <span>Network</span>
          <span>{data?.network ?? '—'}</span>
        </div>
        <div className="flex justify-between">
          <span>Last update</span>
          <span>{data?.last_update ? new Date(data.last_update).toLocaleString() : '—'}</span>
        </div>
        <div className="pt-2 text-white/60">
          <p>
            The dashboard polls the Stratum metrics API every few seconds for near real-time updates. Use this page to confirm your
            miner endpoints and active network profile.
          </p>
        </div>
      </div>
    </div>
  );
};

export default SettingsPage;
