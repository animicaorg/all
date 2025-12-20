import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { formatNumber, formatTimestamp, shorten, timeAgo } from '../lib/format'
import StatCard from '../components/StatCard'
import Skeleton from '../components/Skeleton'

export default function HomePage() {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.getHead>> | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    api
      .getHead()
      .then((res) => mounted && setData(res))
      .catch((err) => mounted && setError(String(err)))
    return () => {
      mounted = false
    }
  }, [])

  if (error) {
    return (
      <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-6 text-sm text-red-100">
        RPC unavailable. {error}
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <section className="grid gap-4 md:grid-cols-3">
        {data ? (
          <>
            <StatCard label="Chain head" value={`#${formatNumber(data.head.height)}`} />
            <StatCard label="Head hash" value={<span className="text-sm">{shorten(data.head.hash)}</span>} />
            <StatCard label="Head time" value={`${timeAgo(data.head.time)} · ${formatTimestamp(data.head.time)}`} />
          </>
        ) : (
          Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-20" />)
        )}
      </section>

      <section className="grid gap-4 md:grid-cols-5">
        {data ? (
          <>
            <StatCard label="Peers" value={formatNumber(data.stats.peerCount ?? 0)} />
            <StatCard label="Inbound" value={formatNumber(data.stats.inboundPeers ?? 0)} />
            <StatCard label="Outbound" value={formatNumber(data.stats.outboundPeers ?? 0)} />
            <StatCard label="Mempool" value={formatNumber(data.stats.mempoolSize ?? 0)} />
            <StatCard
              label="Avg block time"
              value={data.stats.avgBlockTime ? `${data.stats.avgBlockTime.toFixed(1)}s` : '—'}
            />
          </>
        ) : (
          Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-20" />)
        )}
      </section>

      <section className="rounded-xl border border-night-800 bg-night-900 p-6">
        <h2 className="text-lg font-semibold">Getting started</h2>
        <p className="mt-2 text-sm text-slate-400">
          Use the search bar above to jump to blocks, transactions, or addresses. The explorer automatically refreshes
          head data and shows RPC health in real time.
        </p>
      </section>
    </div>
  )
}
