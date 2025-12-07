import { BlockRow } from '../../lib/api';

interface BlocksTableProps {
  blocks: BlockRow[];
}

const BlocksTable = ({ blocks }: BlocksTableProps) => (
  <div className="glass rounded-2xl p-4 table-card">
    <div className="flex items-center justify-between mb-3">
      <h3 className="text-white font-semibold">Recent Blocks</h3>
      <span className="text-sm text-white/60 hidden sm:inline">Newest first</span>
    </div>
    {blocks.length === 0 ? (
      <p className="text-sm text-white/60">No block submissions yet.</p>
    ) : (
      <>
        {/* Mobile: Stacked card view */}
        <div className="sm:hidden space-y-3">
          {blocks.map((block) => (
            <div key={`${block.height}-${block.hash}`} className="border border-white/5 rounded-lg p-3 space-y-2">
              <div className="flex justify-between items-center">
                <span className="text-white/60 text-xs">Height</span>
                <span className="text-white font-semibold">{block.height}</span>
              </div>
              <div className="flex justify-between items-start">
                <span className="text-white/60 text-xs">Hash</span>
                <span className="font-mono text-xs text-white/80 truncate-hash">{block.hash}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-white/60 text-xs">Time</span>
                <span className="text-white/70 text-xs">{new Date(block.timestamp).toLocaleString()}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-white/60 text-xs">Found by Pool</span>
                <span className="text-white">{block.found_by_pool ? 'Yes' : 'No'}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-white/60 text-xs">Reward</span>
                <span className="text-white">{block.reward}</span>
              </div>
            </div>
          ))}
        </div>

        {/* Desktop: Table view with horizontal scroll */}
        <div className="hidden sm:block overflow-x-auto -webkit-overflow-scrolling-touch">
          <table className="w-full text-sm min-w-[600px]">
            <thead className="text-white/60">
              <tr>
                <th className="py-2 text-left whitespace-nowrap">Height</th>
                <th className="py-2 text-left whitespace-nowrap">Hash</th>
                <th className="py-2 text-left whitespace-nowrap">Time</th>
                <th className="py-2 text-left whitespace-nowrap">Found by Pool</th>
                <th className="py-2 text-left whitespace-nowrap">Reward</th>
              </tr>
            </thead>
            <tbody>
              {blocks.map((block) => (
                <tr key={`${block.height}-${block.hash}`} className="border-t border-white/5">
                  <td className="py-3">{block.height}</td>
                  <td className="py-3 font-mono text-xs">{block.hash?.slice(0, 16)}...</td>
                  <td className="py-3 text-white/70 whitespace-nowrap">{new Date(block.timestamp).toLocaleString()}</td>
                  <td className="py-3">{block.found_by_pool ? 'Yes' : 'No'}</td>
                  <td className="py-3">{block.reward}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>
    )}
  </div>
);

export default BlocksTable;
