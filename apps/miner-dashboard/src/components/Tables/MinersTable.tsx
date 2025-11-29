import { useNavigate } from 'react-router-dom';
import { Miner } from '../../lib/api';

interface MinersTableProps {
  miners: Miner[];
}

const formatHashrate = (value: number) => `${value.toFixed(2)} H/s`;

const MinersTable = ({ miners }: MinersTableProps) => {
  const navigate = useNavigate();

  return (
    <div className="glass rounded-2xl p-4 table-card overflow-hidden">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-white font-semibold">Workers</h3>
        <p className="text-sm text-white/60">Click a row for details</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-white/60">
            <tr>
              <th className="py-2 text-left">Worker</th>
              <th className="py-2 text-left">Address</th>
              <th className="py-2 text-right">1m</th>
              <th className="py-2 text-right">15m</th>
              <th className="py-2 text-right">1h</th>
              <th className="py-2 text-right">Accepted</th>
              <th className="py-2 text-right">Rejected</th>
            </tr>
          </thead>
          <tbody>
            {miners.map((miner) => (
              <tr
                key={miner.worker_id}
                onClick={() => navigate(`/miners/${miner.worker_id}`)}
                className="border-t border-white/5 hover:bg-white/5 cursor-pointer"
              >
                <td className="py-3 font-medium">{miner.worker_name}</td>
                <td className="py-3 text-white/70">{miner.address}</td>
                <td className="py-3 text-right">{formatHashrate(miner.hashrate_1m)}</td>
                <td className="py-3 text-right">{formatHashrate(miner.hashrate_15m)}</td>
                <td className="py-3 text-right">{formatHashrate(miner.hashrate_1h)}</td>
                <td className="py-3 text-right text-emerald-400">{miner.shares_accepted}</td>
                <td className="py-3 text-right text-red-400">{miner.shares_rejected}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default MinersTable;
