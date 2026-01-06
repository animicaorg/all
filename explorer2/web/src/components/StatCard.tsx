import { ReactNode } from 'react'

interface StatCardProps {
  label: string
  value: ReactNode
}

export default function StatCard({ label, value }: StatCardProps) {
  return (
    <div className="rounded-xl border border-day-200 bg-white px-4 py-3 shadow-sm dark:border-night-800 dark:bg-night-900">
      <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">{label}</p>
      <div className="mt-2 text-lg font-semibold text-gray-900 dark:text-slate-100">{value}</div>
    </div>
  )
}
