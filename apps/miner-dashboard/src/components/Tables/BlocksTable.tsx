import { BlockRow } from '../../lib/api';

interface BlocksTableProps {
  blocks: BlockRow[];
}

const BlocksTable = ({ blocks }: BlocksTableProps) => (
  <div className="glass rounded-2xl p-4 table-card">
    <div className="flex items-center justify-between mb-3">
      <h3 className="text-white font-semibold">Recent Blocks</h3>
      <span className="text-sm text-white/60">Newest first</span>
    </div>
    {blocks.length === 0 ? (
      <p className="text-sm text-white/60">No block submissions yet.</p>
    ) : (
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-white/60">
            <tr>
              <th className="py-2 text-left">Height</th>
              <th className="py-2 text-left">Hash</th>
              <th className="py-2 text-left">Time</th>
              <th className="py-2 text-left">Found by Pool</th>
              <th className="py-2 text-left">Reward</th>
            </tr>
          </thead>
          <tbody>
            {blocks.map((block) => (
              <tr key={`${block.height}-${block.hash}`} className="border-t border-white/5">
                <td className="py-2">{block.height}</td>
                <td className="py-2 font-mono text-xs">{block.hash?.slice(0, 16)}...</td>
                <td className="py-2 text-white/70">{new Date(block.timestamp).toLocaleString()}</td>
                <td className="py-2">{block.found_by_pool ? 'Yes' : 'No'}</td>
                <td className="py-2">{block.reward}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )}
  </div>
);

export default BlocksTable;
