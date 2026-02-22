import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { TxDetail } from '@animica/explorer2-shared'
import { api } from '../lib/api'
import { formatBalance, formatNumber, shorten } from '../lib/format'
import Skeleton from '../components/Skeleton'
import ErrorDisplay from '../components/ErrorDisplay'

export default function MempoolPage() {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.getMempool>> | null>(null)
  const [txDetails, setTxDetails] = useState<Record<string, Pick<TxDetail, 'from' | 'to' | 'value'>>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refetchTrigger, setRefetchTrigger] = useState(0)

  useEffect(() => {
    let mounted = true

    const fetchData = () => {
      if (!mounted) return
      setLoading(true)
      api
        .getMempool(100)
        .then((res) => {
          if (mounted) {
            setData(res)
            setError(null)
            setLoading(false)
          }
        })
        .catch((err) => {
          if (mounted) {
            setError(String(err))
            setLoading(false)
          }
        })
    }

    fetchData()

    // Poll every 5 seconds for mempool updates
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

  useEffect(() => {
    if (!data?.entries.length) return
    const missingHashes = data.entries
      .map((entry) => entry.hash)
      .filter((hash) => !txDetails[hash])

    if (!missingHashes.length) return

    let cancelled = false
    Promise.all(
      missingHashes.map(async (hash) => {
        try {
          const detail = await api.getTx(hash)
          return [hash, { from: detail.from, to: detail.to, value: detail.value }] as const
        } catch {
          return [hash, {}] as const
        }
      })
    ).then((records) => {
      if (cancelled) return
      const next = Object.fromEntries(records)
      setTxDetails((prev) => ({ ...prev, ...next }))
    })

    return () => {
      cancelled = true
    }
  }, [data, txDetails])

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
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-slate-100">Mempool</h1>
        {data && (
          <div className="text-sm text-gray-600 dark:text-slate-400">
            Auto-refresh every 5s
          </div>
        )}
      </div>

      {data?.stats && (
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="rounded-xl border border-day-200 bg-white p-4 shadow-sm dark:border-night-800 dark:bg-night-900">
            <div className="text-sm font-medium text-gray-500 dark:text-slate-400">Pending Transactions</div>
            <div className="mt-2 text-2xl font-bold text-gray-900 dark:text-slate-100">
              {formatNumber(data.stats.count)}
            </div>
          </div>
          <div className="rounded-xl border border-day-200 bg-white p-4 shadow-sm dark:border-night-800 dark:bg-night-900">
            <div className="text-sm font-medium text-gray-500 dark:text-slate-400">Total Size</div>
            <div className="mt-2 text-2xl font-bold text-gray-900 dark:text-slate-100">
              {formatBytes(data.stats.totalBytes)}
            </div>
          </div>
          <div className="rounded-xl border border-day-200 bg-white p-4 shadow-sm dark:border-night-800 dark:bg-night-900">
            <div className="text-sm font-medium text-gray-500 dark:text-slate-400">Oldest Transaction</div>
            <div className="mt-2 text-2xl font-bold text-gray-900 dark:text-slate-100">
              {data.stats.oldestAgeSec !== null ? `${data.stats.oldestAgeSec}s` : '—'}
            </div>
          </div>
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-day-200 bg-white shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="border-b border-day-200 bg-day-50 px-6 py-4 dark:border-night-800 dark:bg-night-800">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-600 dark:text-slate-400">
            Pending Transactions
          </h2>
        </div>
        <div className="divide-y divide-day-200 dark:divide-night-800">
          {loading && !data ? (
            Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="px-6 py-4">
                <Skeleton className="h-6 w-full" />
              </div>
            ))
          ) : data && data.entries.length > 0 ? (
            data.entries.map((entry) => (
              <div key={entry.hash} className="px-6 py-4 hover:bg-day-50 dark:hover:bg-night-800/50">
                <Link
                  to={`/tx/${entry.hash}`}
                  className="font-mono text-sm text-animica-600 hover:underline dark:text-animica-400"
                >
                  {shorten(entry.hash, 16, 12)}
                </Link>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-600 dark:text-slate-400">
                  <span>From: {shorten(txDetails[entry.hash]?.from ?? '—', 8, 6)}</span>
                  <span>To: {shorten(txDetails[entry.hash]?.to ?? '—', 8, 6)}</span>
                  <span>Amount: {txDetails[entry.hash]?.value ? `${formatBalance(txDetails[entry.hash]?.value).anm} ANM` : '—'}</span>
                </div>
              </div>
            ))
          ) : (
            <div className="px-6 py-8 text-center text-gray-500 dark:text-slate-400">
              No pending transactions
            </div>
          )}
        </div>
      </div>

      {data && data.total > data.entries.length && (
        <div className="rounded-xl border border-day-200 bg-white p-4 text-center text-sm text-gray-600 dark:border-night-800 dark:bg-night-900 dark:text-slate-400">
          Showing {data.entries.length} of {formatNumber(data.total)} transactions
        </div>
      )}
    </div>
  )
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
