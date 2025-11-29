import { ClipboardCopy } from 'lucide-react';

interface StratumConfigCardProps {
  endpoint: string;
}

const copy = (text: string) => navigator.clipboard?.writeText(text);

const StratumConfigCard = ({ endpoint }: StratumConfigCardProps) => {
  const commands = [
    {
      label: 'Generic miner',
      cmd: `miner --url ${endpoint} --worker my-rig --address animica1...`,
    },
    {
      label: 'SHA-256 miner',
      cmd: `cgminer -o ${endpoint.replace('stratum+tcp://', '')} -u animica1... -p x`,
    },
  ];

  return (
    <div className="glass rounded-2xl p-4 shadow-card">
      <div className="flex items-center justify-between mb-3">
        <div>
          <p className="text-sm text-white/60">Stratum endpoint</p>
          <h3 className="font-semibold">{endpoint}</h3>
        </div>
        <button
          type="button"
          className="px-3 py-1 rounded-lg bg-white/10 hover:bg-white/20 text-sm"
          onClick={() => copy(endpoint)}
        >
          Copy
        </button>
      </div>
      <div className="space-y-3">
        {commands.map((item) => (
          <div
            key={item.label}
            className="flex items-start justify-between gap-2 p-3 rounded-xl bg-black/30 border border-white/5"
          >
            <div>
              <p className="text-xs text-white/60">{item.label}</p>
              <code className="text-sm text-white">{item.cmd}</code>
            </div>
            <button
              type="button"
              className="p-2 rounded-lg bg-white/5 hover:bg-white/10"
              onClick={() => copy(item.cmd)}
              aria-label={`Copy ${item.label}`}
            >
              <ClipboardCopy className="h-4 w-4" />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
};

export default StratumConfigCard;
