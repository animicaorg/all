import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { formatNumber, shorten, timeAgo } from '../lib/format'
import StatCard from '../components/StatCard'
import Skeleton from '../components/Skeleton'
import ErrorDisplay from '../components/ErrorDisplay'
import ThetaMicroChart from '../components/ThetaMicroChart'

export default function HomePage() {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.getHead>> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refetchTrigger, setRefetchTrigger] = useState(0)

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
  }, [refetchTrigger])

  if (error) {
    return (
      <ErrorDisplay 
        error={error}
        onRetry={() => {
          setError(null)
          setData(null)
          setRefetchTrigger(prev => prev + 1)
        }}
      />
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
              <StatCard
                label="Theta (µnats)"
                value={data.head.thetaMicro !== undefined ? formatNumber(data.head.thetaMicro) : '—'}
              />
            </>
          ) : (
            Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-20" />)
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

      {data ? (
        <ThetaMicroChart points={data.thetaHistory ?? []} />
      ) : (
        <Skeleton className="h-80" />
      )}

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
