import { ReactNode } from 'react'

interface StatCardProps {
  label: string
  value: ReactNode
}

export default function StatCard({ label, value }: StatCardProps) {
  return (
    <div className="rounded-xl border border-night-800 bg-night-900 px-4 py-3">
      <p className="text-xs uppercase tracking-widest text-slate-500">{label}</p>
      <div className="mt-2 text-lg font-semibold text-slate-100">{value}</div>
    </div>
  )
}
