import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { formatNumber, formatTimestamp, shorten, timeAgo } from '../lib/format'
import StatCard from '../components/StatCard'
import Skeleton from '../components/Skeleton'

export default function HomePage() {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.getHead>> | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    
    const fetchData = () => {
      if (!mounted) return
      api
        .getHead()
        .then((res) => {
          if (mounted) {
            setData(res)
            setError(null)
          }
        })
        .catch((err) => {
          if (mounted) {
            setError(String(err))
          }
        })
    }

    // Initial fetch
    fetchData()

    // Poll every 5 seconds for new blocks, but only when page is visible
    const intervalId = setInterval(() => {
      if (mounted && document.visibilityState === 'visible') {
        fetchData()
      }
    }, 5000)

    return () => {
      mounted = false
      clearInterval(intervalId)
    }
  }, [])

  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-900/10 dark:text-red-100">
        <div className="flex items-start gap-3">
          <svg className="mt-0.5 h-5 w-5 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
          <div>
            <strong className="font-semibold">RPC Connection Error</strong>
            <p className="mt-1">{error}</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <section>
        <h2 className="mb-4 text-lg font-semibold text-gray-900 dark:text-slate-100">Chain Status</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {data ? (
            <>
              <StatCard 
                label="Current Block" 
                value={
                  <span>
                    #{formatNumber(data.head.height)}
                    {data.head.canonicalHeight !== undefined && data.head.canonicalHeight !== data.head.height && (
                      <span className="block text-xs text-gray-500 dark:text-slate-400 mt-1">
                        Canonical: #{formatNumber(data.head.canonicalHeight)}
                      </span>
                    )}
                  </span>
                }
              />
              <StatCard label="Block Hash" value={<span className="truncate text-sm">{shorten(data.head.hash)}</span>} />
              <StatCard label="Last Block" value={`${timeAgo(data.head.time)}`} />
            </>
          ) : (
            Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-20" />)
          )}
        </div>
      </section>

      <section>
        <h2 className="mb-4 text-lg font-semibold text-gray-900 dark:text-slate-100">Network Stats</h2>
        <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-5">
          {data ? (
            <>
              <StatCard label="Peers" value={formatNumber(data.stats.peerCount ?? 0)} />
              <StatCard label="Inbound" value={formatNumber(data.stats.inboundPeers ?? 0)} />
              <StatCard label="Outbound" value={formatNumber(data.stats.outboundPeers ?? 0)} />
              <StatCard label="Mempool" value={formatNumber(data.stats.mempoolSize ?? 0)} />
              <StatCard
                label="Avg Block Time"
                value={data.stats.avgBlockTime ? `${data.stats.avgBlockTime.toFixed(1)}s` : '—'}
              />
            </>
          ) : (
            Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-20" />)
          )}
        </div>
      </section>

      <section className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-slate-100">Recent Blocks</h2>
          <Link 
            to="/blocks" 
            className="text-sm font-medium text-animica-600 hover:underline dark:text-animica-400"
          >
            View all →
          </Link>
        </div>
        {data && data.recentBlocks && data.recentBlocks.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-day-200 text-xs font-semibold uppercase tracking-wider text-gray-600 dark:border-night-700 dark:text-slate-400">
                <tr>
                  <th className="px-2 py-2">Height</th>
                  <th className="hidden px-2 py-2 sm:table-cell">Hash</th>
                  <th className="px-2 py-2">Miner</th>
                  <th className="hidden px-2 py-2 lg:table-cell">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-day-200 dark:divide-night-700">
                {data.recentBlocks.slice(0, 10).map((block) => (
                  <tr key={block.hash} className="hover:bg-day-50 dark:hover:bg-night-800/50">
                    <td className="whitespace-nowrap px-2 py-2">
                      <Link 
                        className="font-mono text-animica-600 hover:underline dark:text-animica-400" 
                        to={`/block/${block.height}`}
                      >
                        #{formatNumber(block.height)}
                      </Link>
                    </td>
                    <td className="hidden px-2 py-2 font-mono text-xs text-gray-600 dark:text-slate-300 sm:table-cell">
                      {shorten(block.hash, 8, 6)}
                    </td>
                    <td className="px-2 py-2">
                      {block.miner ? (
                        <Link 
                          to={`/address/${block.miner}`}
                          className="font-mono text-xs text-animica-600 hover:underline dark:text-animica-400"
                        >
                          {shorten(block.miner, 8, 6)}
                        </Link>
                      ) : (
                        <span className="text-xs text-gray-400 dark:text-slate-500">—</span>
                      )}
                    </td>
                    <td className="hidden px-2 py-2 text-xs text-gray-500 dark:text-slate-400 lg:table-cell">
                      {timeAgo(block.time)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        )}
      </section>

      <section className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-slate-100">Welcome to Animica Explorer</h2>
        <p className="mt-3 text-sm leading-relaxed text-gray-600 dark:text-slate-400">
          Use the search bar above to explore blocks, transactions, and addresses on the Animica blockchain.
          The explorer automatically refreshes data and displays real-time network statistics.
        </p>
        <div className="mt-4 flex flex-wrap gap-4 text-sm">
          <div className="flex items-center gap-2 text-gray-700 dark:text-slate-300">
            <svg className="h-4 w-4 text-animica-600 dark:text-animica-400" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
            </svg>
            Live blockchain data
          </div>
          <div className="flex items-center gap-2 text-gray-700 dark:text-slate-300">
            <svg className="h-4 w-4 text-animica-600 dark:text-animica-400" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
            Fast search
          </div>
          <div className="flex items-center gap-2 text-gray-700 dark:text-slate-300">
            <svg className="h-4 w-4 text-animica-600 dark:text-animica-400" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z" />
            </svg>
            Mobile-friendly
          </div>
        </div>
      </section>
    </div>
  )
}
