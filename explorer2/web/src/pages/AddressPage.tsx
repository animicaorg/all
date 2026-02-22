import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import type { AddressSummary, TxSummary } from '@animica/explorer2-shared'
import { api } from '../lib/api'
import { formatNumber, shorten, formatBalance } from '../lib/format'
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
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-slate-100">Address</h1>
          <CopyButton value={summary.address} />
        </div>
        <p className="mt-3 break-all font-mono text-sm text-gray-600 dark:text-slate-400">{summary.address}</p>
        <div className="mt-6 grid gap-4 sm:grid-cols-2">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">Confirmed Balance</p>
            <p className="mt-2 font-mono text-lg font-semibold text-gray-900 dark:text-slate-200">
              {formatBalance(summary.confirmedBalance).anm} <span className="text-base font-normal text-gray-600 dark:text-slate-400">ANM</span>
            </p>
            {summary.confirmedBalance && summary.confirmedBalance !== '—' && (
              <>
                <p className="mt-1 font-mono text-xs text-gray-500 dark:text-slate-500" title="Native units (nano-ANM)">
                  {formatBalance(summary.confirmedBalance).nanm} nANM
                </p>
                <p className="mt-1 font-mono text-xs text-gray-500 dark:text-slate-500" title="Hexadecimal representation">
                  {formatBalance(summary.confirmedBalance).hex}
                </p>
              </>
            )}
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">Pending Balance</p>
            <p className="mt-2 font-mono text-lg font-semibold text-gray-900 dark:text-slate-200">
              {formatBalance(summary.pendingBalance).anm} <span className="text-base font-normal text-gray-600 dark:text-slate-400">ANM</span>
            </p>
            {summary.pendingBalance && summary.pendingBalance !== '—' && (
              <>
                <p className="mt-1 font-mono text-xs text-gray-500 dark:text-slate-500" title="Native units (nano-ANM)">
                  {formatBalance(summary.pendingBalance).nanm} nANM
                </p>
                <p className="mt-1 font-mono text-xs text-gray-500 dark:text-slate-500" title="Hexadecimal representation">
                  {formatBalance(summary.pendingBalance).hex}
                </p>
              </>
            )}
          </div>
        </div>
        {summary.partial && (
          <p className="mt-4 text-xs text-gray-500 dark:text-slate-500">
            Showing recent activity by scanning the last {formatNumber(summary.scannedBlocks ?? 0)} blocks.
          </p>
        )}
      </div>

      <div className="rounded-xl border border-day-200 bg-white shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="border-b border-day-200 px-6 py-4 dark:border-night-800">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-slate-100">Recent Transactions</h2>
        </div>
        <div className="divide-y divide-day-200 dark:divide-night-800">
          {summary.txs.length === 0 && (
            <div className="px-6 py-8 text-center text-sm text-gray-500 dark:text-slate-400">
              No transactions found in the scan window.
            </div>
          )}
          {summary.txs.map((tx: TxSummary) => (
            <div key={tx.hash} className="flex flex-wrap items-center justify-between gap-3 px-6 py-4 hover:bg-day-50 dark:hover:bg-night-800/50">
              <div className="min-w-0 flex-1">
                <Link to={`/tx/${tx.hash}`} className="block font-mono text-sm text-animica-600 hover:underline dark:text-animica-400">
                  {shorten(tx.hash, 10, 8)}
                </Link>
                <div className="mt-1 text-xs text-gray-500 dark:text-slate-500">
                  {shorten(tx.from ?? '—', 8, 6)} → {shorten(tx.to ?? '—', 8, 6)}
                  <span className="ml-2">• Amount: {tx.value ? `${formatBalance(tx.value).anm} ANM` : '—'}</span>
                </div>
              </div>
              <span className="text-xs font-medium text-gray-600 dark:text-slate-400">{tx.status ?? 'confirmed'}</span>
            </div>
          ))}
        </div>
      </div>

      {summary.nextCursor && (
        <div className="flex justify-center">
          <button
            type="button"
            disabled={!summary.nextCursor || loading}
            onClick={() => summary.nextCursor && load(summary.nextCursor)}
            className="rounded-lg border border-day-300 bg-white px-6 py-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-day-50 disabled:opacity-40 dark:border-night-700 dark:bg-night-800 dark:text-slate-300 dark:hover:bg-night-700"
          >
            {loading ? 'Loading...' : 'Load More'}
          </button>
        </div>
      )}
    </div>
  )
}
