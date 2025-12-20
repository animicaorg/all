import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { BlockSummary } from '@animica/explorer2-shared'
import { api } from '../lib/api'
import { formatNumber, formatTimestamp, shorten, timeAgo } from '../lib/format'
import Skeleton from '../components/Skeleton'

export default function BlocksPage() {
  const [blocks, setBlocks] = useState<BlockSummary[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadBlocks = async (cursorValue?: string | null) => {
    setLoading(true)
    try {
      const res = await api.getBlocks(15, cursorValue ?? undefined)
      setBlocks((prev) => (cursorValue ? [...prev, ...res.items] : res.items))
      setCursor(res.nextCursor)
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadBlocks()
  }, [])

  if (error) {
    return (
      <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-6 text-sm text-red-100">
        Failed to load blocks. {error}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Recent blocks</h1>
      <div className="overflow-hidden rounded-xl border border-night-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-night-900 text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-3">Height</th>
              <th className="px-4 py-3">Hash</th>
              <th className="px-4 py-3">Txs</th>
              <th className="px-4 py-3">Time</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-night-800">
            {blocks.map((block) => (
              <tr key={block.hash} className="bg-night-900/50">
                <td className="px-4 py-3">
                  <Link className="text-animica-400 hover:underline" to={`/block/${block.height}`}>
                    #{formatNumber(block.height)}
                  </Link>
                </td>
                <td className="px-4 py-3 text-slate-300">{shorten(block.hash)}</td>
                <td className="px-4 py-3">{formatNumber(block.txCount)}</td>
                <td className="px-4 py-3 text-slate-400">
                  {timeAgo(block.time)} · {formatTimestamp(block.time)}
                </td>
              </tr>
            ))}
            {loading &&
              Array.from({ length: 3 }).map((_, i) => (
                <tr key={`skeleton-${i}`}>
                  <td className="px-4 py-3" colSpan={4}>
                    <Skeleton className="h-6 w-full" />
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      <div>
        <button
          type="button"
          disabled={!cursor || loading}
          onClick={() => cursor && loadBlocks(cursor)}
          className="rounded-lg border border-night-700 px-4 py-2 text-sm text-slate-300 hover:border-animica-500 disabled:opacity-40"
        >
          {cursor ? 'Load more' : 'No more blocks'}
        </button>
      </div>
    </div>
  )
}
