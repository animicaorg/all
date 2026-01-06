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
  const [isPaginated, setIsPaginated] = useState(false)

  const loadBlocks = async (cursorValue?: string | null) => {
    setLoading(true)
    try {
      const res = await api.getBlocks(20, cursorValue ?? undefined)
      setBlocks((prev) => (cursorValue ? [...prev, ...res.items] : res.items))
      setCursor(res.nextCursor)
      setError(null)
      if (cursorValue) {
        setIsPaginated(true)
      }
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    // Initial load
    loadBlocks()

    // Poll every 5 seconds for new blocks, but only if user hasn't paginated
    // and the page is visible
    const intervalId = setInterval(() => {
      if (!isPaginated && document.visibilityState === 'visible') {
        loadBlocks()
      }
    }, 5000)

    return () => {
      clearInterval(intervalId)
    }
  }, [isPaginated])

  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-900/10 dark:text-red-100">
        <strong className="font-semibold">Error:</strong> {error}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-gray-900 dark:text-slate-100">Recent Blocks</h1>
      
      <div className="overflow-hidden rounded-xl border border-day-200 bg-white shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-day-200 bg-day-50 text-xs font-semibold uppercase tracking-wider text-gray-600 dark:border-night-800 dark:bg-night-800 dark:text-slate-400">
              <tr>
                <th className="px-4 py-3 sm:px-6">Height</th>
                <th className="hidden px-4 py-3 sm:table-cell sm:px-6">Hash</th>
                <th className="px-4 py-3 sm:px-6">Txs</th>
                <th className="px-4 py-3 sm:px-6">Time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-day-200 dark:divide-night-800">
              {blocks.map((block) => (
                <tr key={block.hash} className="hover:bg-day-50 dark:hover:bg-night-800/50">
                  <td className="whitespace-nowrap px-4 py-3 sm:px-6">
                    <Link 
                      className="font-mono text-animica-600 hover:underline dark:text-animica-400" 
                      to={`/block/${block.height}`}
                    >
                      #{formatNumber(block.height)}
                    </Link>
                  </td>
                  <td className="hidden px-4 py-3 font-mono text-gray-600 dark:text-slate-300 sm:table-cell sm:px-6">
                    {shorten(block.hash, 10, 8)}
                  </td>
                  <td className="px-4 py-3 text-gray-700 dark:text-slate-200 sm:px-6">{formatNumber(block.txCount)}</td>
                  <td className="px-4 py-3 text-gray-500 dark:text-slate-400 sm:px-6">
                    <span className="block sm:hidden">{timeAgo(block.time)}</span>
                    <span className="hidden sm:block">{timeAgo(block.time)} · {formatTimestamp(block.time)}</span>
                  </td>
                </tr>
              ))}
              {loading &&
                Array.from({ length: 3 }).map((_, i) => (
                  <tr key={`skeleton-${i}`}>
                    <td className="px-4 py-3 sm:px-6" colSpan={4}>
                      <Skeleton className="h-6 w-full" />
                    </td>
                  </tr>
                ))}
              {!loading && blocks.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-8 text-center text-gray-500 dark:text-slate-400">
                    No blocks found
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      
      {cursor && (
        <div className="flex justify-center">
          <button
            type="button"
            disabled={!cursor || loading}
            onClick={() => cursor && loadBlocks(cursor)}
            className="rounded-lg border border-day-300 bg-white px-6 py-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-day-50 disabled:opacity-40 dark:border-night-700 dark:bg-night-800 dark:text-slate-300 dark:hover:bg-night-700"
          >
            {loading ? 'Loading...' : 'Load More'}
          </button>
        </div>
      )}
    </div>
  )
}
