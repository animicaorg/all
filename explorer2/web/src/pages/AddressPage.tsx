import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import type { AddressSummary, TxSummary } from '@animica/explorer2-shared'
import { api } from '../lib/api'
import { formatNumber, shorten } from '../lib/format'
import CopyButton from '../components/CopyButton'
import Skeleton from '../components/Skeleton'

export default function AddressPage() {
  const { address } = useParams()
  const [summary, setSummary] = useState<AddressSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = async (cursor?: string | null) => {
    if (!address) return
    setLoading(true)
    try {
      const res = await api.getAddress(address, 15, cursor ?? undefined)
      setSummary((prev) => {
        if (!prev || !cursor) return res
        return {
          ...res,
          txs: [...prev.txs, ...res.txs]
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
  }, [address])

  if (error) {
    return (
      <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-6 text-sm text-red-100">
        Failed to load address. {error}
      </div>
    )
  }

  if (!summary) {
    return <Skeleton className="h-40" />
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-night-800 bg-night-900 p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-xl font-semibold">Address</h1>
          <CopyButton value={summary.address} />
        </div>
        <p className="mt-2 text-sm text-slate-400">{summary.address}</p>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div>
            <p className="text-xs uppercase text-slate-500">Confirmed balance</p>
            <p className="mt-1 text-sm text-slate-200">{summary.confirmedBalance ?? '—'}</p>
          </div>
          <div>
            <p className="text-xs uppercase text-slate-500">Pending balance</p>
            <p className="mt-1 text-sm text-slate-200">{summary.pendingBalance ?? '—'}</p>
          </div>
        </div>
        {summary.partial && (
          <p className="mt-4 text-xs text-slate-500">
            Showing recent activity by scanning the last {formatNumber(summary.scannedBlocks ?? 0)} blocks.
          </p>
        )}
      </div>

      <div className="rounded-xl border border-night-800 bg-night-900">
        <div className="border-b border-night-800 px-6 py-4">
          <h2 className="text-lg font-semibold">Recent transactions</h2>
        </div>
        <div className="divide-y divide-night-800">
          {summary.txs.length === 0 && (
            <div className="px-6 py-4 text-sm text-slate-400">No transactions found in the scan window.</div>
          )}
          {summary.txs.map((tx: TxSummary) => (
            <div key={tx.hash} className="flex flex-wrap items-center justify-between gap-3 px-6 py-4">
              <div>
                <Link to={`/tx/${tx.hash}`} className="text-animica-400 hover:underline">
                  {shorten(tx.hash)}
                </Link>
                <div className="text-xs text-slate-500">{tx.from ?? '—'} → {tx.to ?? '—'}</div>
              </div>
              <span className="text-xs text-slate-400">{tx.status ?? 'confirmed'}</span>
            </div>
          ))}
        </div>
      </div>

      <button
        type="button"
        disabled={!summary.nextCursor || loading}
        onClick={() => summary.nextCursor && load(summary.nextCursor)}
        className="rounded-lg border border-night-700 px-4 py-2 text-sm text-slate-300 hover:border-animica-500 disabled:opacity-40"
      >
        {summary.nextCursor ? 'Load more' : 'No more results'}
      </button>
    </div>
  )
}
