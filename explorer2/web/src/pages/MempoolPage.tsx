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
      <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-6 text-sm text-red-100">
        Failed to load mempool. {error}
      </div>
    )
  }

  if (!data) {
    return <Skeleton className="h-40" />
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-night-800 bg-night-900 p-6">
        <h1 className="text-xl font-semibold">Mempool</h1>
        <p className="mt-2 text-sm text-slate-400">Pending transactions: {formatNumber(data.total)}</p>
      </div>
      <div className="overflow-hidden rounded-xl border border-night-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-night-900 text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-3">Tx hash</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-night-800">
            {data.entries.map((entry) => (
              <tr key={entry.hash} className="bg-night-900/50">
                <td className="px-4 py-3">
                  <Link to={`/tx/${entry.hash}`} className="text-animica-400 hover:underline">
                    {shorten(entry.hash)}
                  </Link>
                </td>
              </tr>
            ))}
            {loading && (
              <tr>
                <td className="px-4 py-3">
                  <Skeleton className="h-6 w-full" />
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <button
        type="button"
        disabled={!data.nextCursor || loading}
        onClick={() => data.nextCursor && load(data.nextCursor)}
        className="rounded-lg border border-night-700 px-4 py-2 text-sm text-slate-300 hover:border-animica-500 disabled:opacity-40"
      >
        {data.nextCursor ? 'Load more' : 'No more pending txs'}
      </button>
    </div>
  )
}
