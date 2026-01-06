import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { MempoolView } from '@animica/explorer2-shared'
import { api } from '../lib/api'
import { formatNumber, shorten } from '../lib/format'
import Skeleton from '../components/Skeleton'

export default function MempoolPage() {
  const [data, setData] = useState<MempoolView | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = async (cursor?: string | null) => {
    setLoading(true)
    try {
      const res = await api.getMempool(50, cursor ?? undefined)
      setData((prev) => {
        if (!prev || !cursor) return res
        return {
          ...res,
          entries: [...prev.entries, ...res.entries]
        }
      })
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-900/10 dark:text-red-100">
        <strong className="font-semibold">Error:</strong> {error}
      </div>
    )
  }

  if (!data) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24" />
        <Skeleton className="h-64" />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-slate-100">Mempool</h1>
        <p className="mt-2 text-sm text-gray-600 dark:text-slate-400">
          Pending transactions: <span className="font-semibold">{formatNumber(data.total)}</span>
        </p>
      </div>
      
      <div className="overflow-hidden rounded-xl border border-day-200 bg-white shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-day-200 bg-day-50 text-xs font-semibold uppercase tracking-wider text-gray-600 dark:border-night-800 dark:bg-night-800 dark:text-slate-400">
              <tr>
                <th className="px-4 py-3 sm:px-6">Transaction Hash</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-day-200 dark:divide-night-800">
              {data.entries.length === 0 && !loading && (
                <tr>
                  <td className="px-4 py-8 text-center text-gray-500 dark:text-slate-400 sm:px-6">
                    No pending transactions
                  </td>
                </tr>
              )}
              {data.entries.map((entry) => (
                <tr key={entry.hash} className="hover:bg-day-50 dark:hover:bg-night-800/50">
                  <td className="px-4 py-3 sm:px-6">
                    <Link 
                      to={`/tx/${entry.hash}`} 
                      className="block font-mono text-sm text-animica-600 hover:underline dark:text-animica-400"
                    >
                      {shorten(entry.hash, 10, 8)}
                    </Link>
                  </td>
                </tr>
              ))}
              {loading && (
                <tr>
                  <td className="px-4 py-3 sm:px-6">
                    <Skeleton className="h-6 w-full" />
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      
      {data.nextCursor && (
        <div className="flex justify-center">
          <button
            type="button"
            disabled={!data.nextCursor || loading}
            onClick={() => data.nextCursor && load(data.nextCursor)}
            className="rounded-lg border border-day-300 bg-white px-6 py-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-day-50 disabled:opacity-40 dark:border-night-700 dark:bg-night-800 dark:text-slate-300 dark:hover:bg-night-700"
          >
            {loading ? 'Loading...' : 'Load More'}
          </button>
        </div>
      )}
    </div>
  )
}
