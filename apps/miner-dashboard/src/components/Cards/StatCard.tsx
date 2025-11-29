import { ReactNode } from 'react';

interface StatCardProps {
  label: string;
  value: string | number;
  helper?: string;
  icon?: ReactNode;
}

const StatCard = ({ label, value, helper, icon }: StatCardProps) => (
  <div className="glass rounded-2xl p-4 shadow-card">
    <div className="flex items-center justify-between text-sm text-white/70">
      <span>{label}</span>
      {icon && <span className="text-white/60">{icon}</span>}
    </div>
    <p className="mt-2 text-2xl font-semibold text-white">{value}</p>
    {helper && <p className="text-xs text-white/50 mt-1">{helper}</p>}
  </div>
);

export default StatCard;
