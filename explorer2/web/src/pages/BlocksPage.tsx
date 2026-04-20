import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import type { BlockSummary } from '@animica/explorer2-shared'
import { api } from '../lib/api'
import { formatNumber, shorten } from '../lib/format'
import Skeleton from '../components/Skeleton'

const FIXED_BLOCK_PROGRESS_SECONDS = 300

export default function BlocksPage() {
  const [blocks, setBlocks] = useState<BlockSummary[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())
  const [lastBlockSeenAtMs, setLastBlockSeenAtMs] = useState<number | null>(null)
  const [justLanded, setJustLanded] = useState(false)
  const isPaginatedRef = useRef(false)
  const mountedRef = useRef(true)
  const latestHashRef = useRef<string | null>(null)
  const landedTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const previousArrivalAtRef = useRef<number | null>(null)
  const recentArrivalIntervalsRef = useRef<number[]>([])

  const loadBlocks = useCallback(async (cursorValue?: string | null) => {
    if (!mountedRef.current) return
    setLoading(true)
    try {
      const res = await api.getBlocks(20, cursorValue ?? undefined)
      if (!mountedRef.current) return
      setBlocks((prev) => (cursorValue ? [...prev, ...res.items] : res.items))
      setCursor(res.nextCursor)
      setError(null)
      if (cursorValue) {
        isPaginatedRef.current = true
      } else {
        // Reset pagination flag when loading first page
        isPaginatedRef.current = false
      }
    } catch (err) {
      if (!mountedRef.current) return
      setError(String(err))
    } finally {
      if (mountedRef.current) {
        setLoading(false)
      }
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    
    // Initial load
    loadBlocks()

    // Poll every 5 seconds for new blocks, but only if user hasn't paginated
    // and the page is visible
    const intervalId = setInterval(() => {
      if (mountedRef.current && !isPaginatedRef.current && document.visibilityState === 'visible') {
        loadBlocks()
      }
    }, 5000)

    return () => {
      mountedRef.current = false
      clearInterval(intervalId)
    }
  }, [loadBlocks])

  useEffect(() => {
    const tick = setInterval(() => {
      if (document.visibilityState === 'visible') {
        setNowMs(Date.now())
      }
    }, 1000)
    return () => clearInterval(tick)
  }, [])

  useEffect(() => {
    const newestHash = blocks[0]?.hash ?? null
    if (!newestHash) return
    const observedAt = Date.now()

    if (!latestHashRef.current) {
      latestHashRef.current = newestHash
      previousArrivalAtRef.current = observedAt
      setLastBlockSeenAtMs(observedAt)
      return
    }

    if (latestHashRef.current && latestHashRef.current !== newestHash) {
      if (previousArrivalAtRef.current) {
        const deltaMs = observedAt - previousArrivalAtRef.current
        if (deltaMs > 0) {
          recentArrivalIntervalsRef.current = [...recentArrivalIntervalsRef.current.slice(-5), deltaMs]
        }
      }
      previousArrivalAtRef.current = observedAt
      setLastBlockSeenAtMs(observedAt)
      setJustLanded(true)
      if (landedTimeoutRef.current) clearTimeout(landedTimeoutRef.current)
      landedTimeoutRef.current = setTimeout(() => {
        setJustLanded(false)
        landedTimeoutRef.current = null
      }, 1200)
    }

    latestHashRef.current = newestHash
  }, [blocks])

  useEffect(() => {
    return () => {
      if (landedTimeoutRef.current) {
        clearTimeout(landedTimeoutRef.current)
      }
    }
  }, [])
  const hasBlockData = blocks.length > 0
  const elapsedMs = lastBlockSeenAtMs ? Math.max(0, nowMs - lastBlockSeenAtMs) : 0
  const progressRatio = hasBlockData && lastBlockSeenAtMs
    ? Math.min(1, elapsedMs / (FIXED_BLOCK_PROGRESS_SECONDS * 1000))
    : 0
  const progressPercent = Math.round((justLanded ? 1 : progressRatio) * 100)
  const recentAvgArrivalMs =
    recentArrivalIntervalsRef.current.length > 0
      ? Math.round(
          recentArrivalIntervalsRef.current.reduce((sum, value) => sum + value, 0) /
            recentArrivalIntervalsRef.current.length
        )
      : null
  const cadenceText = recentAvgArrivalMs
    ? `${Math.floor(recentAvgArrivalMs / 60000)}m ${Math.floor((recentAvgArrivalMs % 60000) / 1000)}s`
    : 'collecting'
  const progressLabel = !hasBlockData
    ? 'Waiting for block data'
    : justLanded
      ? 'Block landed'
      : progressRatio >= 1
        ? 'Next block imminent'
        : 'Next block in progress'

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

      <section className="rounded-xl border border-day-200 bg-white p-4 shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-slate-400">Next Block</p>
          <p className="text-sm font-medium text-gray-700 dark:text-slate-200">{progressLabel}</p>
        </div>
        <p className="mt-2 text-xs text-gray-500 dark:text-slate-400">
          Fixed cycle: {Math.floor(FIXED_BLOCK_PROGRESS_SECONDS / 60)}m. Recent arrival cadence: {cadenceText}.
        </p>
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-day-100 dark:bg-night-800">
          <div
            className={`h-full transition-[width,background-color] duration-700 ease-linear ${justLanded ? 'bg-emerald-500' : 'bg-gradient-to-r from-cyan-500 via-animica-500 to-sky-500'}`}
            style={{ width: `${progressPercent}%` }}
          />
        </div>
      </section>

      <div className="overflow-hidden rounded-xl border border-day-200 bg-white shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-day-200 bg-day-50 text-xs font-semibold uppercase tracking-wider text-gray-600 dark:border-night-800 dark:bg-night-800 dark:text-slate-400">
              <tr>
                <th className="px-4 py-3 sm:px-6">Height</th>
                <th className="hidden px-4 py-3 sm:table-cell sm:px-6">Hash</th>
                <th className="hidden px-4 py-3 lg:table-cell lg:px-6">Miner</th>
                <th className="px-4 py-3 sm:px-6">Txs</th>
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
                    {block.canonicalHeight !== undefined && block.canonicalHeight !== block.height && (
                      <span className="ml-2 inline-flex items-center rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800 dark:bg-blue-900/30 dark:text-blue-300">
                        instant
                      </span>
                    )}
                  </td>
                  <td className="hidden px-4 py-3 font-mono text-gray-600 dark:text-slate-300 sm:table-cell sm:px-6">
                    {shorten(block.hash, 10, 8)}
                  </td>
                  <td className="hidden px-4 py-3 font-mono text-gray-600 dark:text-slate-300 lg:table-cell lg:px-6">
                    {block.miner ? (
                      <Link
                        className="text-animica-600 hover:underline dark:text-animica-400"
                        to={`/address/${block.miner}`}
                        title={block.miner}
                      >
                        {shorten(block.miner, 12, 8)}
                      </Link>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-700 dark:text-slate-200 sm:px-6">{formatNumber(block.txCount)}</td>
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
