import { AreaChart, Area, Tooltip, ResponsiveContainer, XAxis, YAxis, CartesianGrid } from 'recharts';

interface HashrateChartProps {
  data: { timestamp: string; value: number }[];
  title?: string;
}

const HashrateChart = ({ data, title = 'Pool Hashrate' }: HashrateChartProps) => (
  <div className="glass rounded-2xl p-4 shadow-card h-full">
    <div className="flex items-center justify-between mb-2">
      <h3 className="text-sm text-white/70">{title}</h3>
      <span className="text-xs text-white/50">Last hour</span>
    </div>
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ left: -20, right: 10 }}>
          <defs>
            <linearGradient id="hashGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#7c3aed" stopOpacity={0.9} />
              <stop offset="95%" stopColor="#7c3aed" stopOpacity={0.05} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
          <XAxis dataKey="timestamp" tick={{ fill: '#9ca3af', fontSize: 12 }} hide />
          <YAxis tick={{ fill: '#9ca3af', fontSize: 12 }} width={60} />
          <Tooltip
            contentStyle={{ background: '#0b1021', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12 }}
            labelStyle={{ color: '#e5e7eb' }}
          />
          <Area type="monotone" dataKey="value" stroke="#a855f7" fill="url(#hashGradient)" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  </div>
);

export default HashrateChart;
