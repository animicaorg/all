import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import type { TxDetail } from '@animica/explorer2-shared'
import { api } from '../lib/api'
import { formatNumber, shorten } from '../lib/format'
import CopyButton from '../components/CopyButton'
import JsonViewer from '../components/JsonViewer'
import Skeleton from '../components/Skeleton'

const POLL_INTERVAL_MS = 3000

export default function TxDetailPage() {
  const { hash } = useParams()
  const [tx, setTx] = useState<TxDetail | null>(null)
  const [head, setHead] = useState<{ height: number } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!hash) return
    let interval: ReturnType<typeof setInterval> | undefined

    const loadTx = async () => {
      try {
        const txRes = await api.getTx(hash)
        setTx(txRes)
        setError(null)

        if (txRes.explorer_head_height) {
          setHead({ height: txRes.explorer_head_height })
        } else {
          api.getHead().then((headRes) => setHead({ height: headRes.head.height })).catch(() => undefined)
        }

        if (txRes.status !== 'pending' && interval) {
          clearInterval(interval)
          interval = undefined
        }
      } catch (err) {
        setError(String(err))
      }
    }

    void loadTx()
    interval = setInterval(loadTx, POLL_INTERVAL_MS)

    return () => {
      if (interval) clearInterval(interval)
    }
  }, [hash])

  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-900/10 dark:text-red-100">
        <strong className="font-semibold">Error:</strong> {error}
      </div>
    )
  }

  if (!tx) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40" />
      </div>
    )
  }

  const getStatusColor = (status: string) => {
    if (status === 'confirmed') return 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400'
    if (status === 'failed') return 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400'
    return 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400'
  }

  const confirmations = tx.confirmations ?? (tx.blockHeight && head ? Math.max(0, head.height - tx.blockHeight + 1) : 0)

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-slate-100">Transaction</h1>
          <CopyButton value={String(tx.tx_hash ?? tx.hash)} />
        </div>

        <div className="mt-6 grid gap-6 sm:grid-cols-2">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">Transaction Hash</p>
            <p className="mt-2 break-all font-mono text-sm text-gray-900 dark:text-slate-200">{tx.tx_hash ?? tx.hash}</p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">Status</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <span className={`inline-flex rounded-full px-3 py-1 text-xs font-medium ${getStatusColor(tx.status)}`}>
                {tx.status === 'confirmed' ? 'Confirmed' : tx.status === 'failed' ? 'Failed' : 'Pending'}
              </span>
              {confirmations > 0 && (
                <span className="inline-flex rounded-full bg-blue-100 px-3 py-1 text-xs font-medium text-blue-800 dark:bg-blue-900/30 dark:text-blue-400">
                  {formatNumber(confirmations)} confirmation{confirmations !== 1 ? 's' : ''}
                </span>
              )}
            </div>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">Included Block Height</p>
            {tx.included_height || tx.blockHeight ? (
              <Link className="mt-2 block font-mono text-sm text-animica-600 hover:underline dark:text-animica-400" to={`/block/${tx.included_height ?? tx.blockHeight}`}>
                #{formatNumber(tx.included_height ?? tx.blockHeight ?? 0)}
              </Link>
            ) : (
              <p className="mt-2 text-sm text-gray-500 dark:text-slate-400">Pending</p>
            )}
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">Included Block Hash</p>
            <p className="mt-2 break-all font-mono text-sm text-gray-700 dark:text-slate-200">{tx.included_block_hash ?? tx.blockHash ?? '—'}</p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">Timestamp</p>
            <p className="mt-2 font-mono text-sm text-gray-700 dark:text-slate-200">{tx.timestamp ? new Date(tx.timestamp * 1000).toISOString() : '—'}</p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">From</p>
            <p className="mt-2 break-all font-mono text-sm text-gray-700 dark:text-slate-200">
              {tx.from ? (
                <Link to={`/address/${tx.from}`} className="text-animica-600 hover:underline dark:text-animica-400">
                  {shorten(tx.from, 10, 8)}
                </Link>
              ) : (
                '—'
              )}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">To</p>
            <p className="mt-2 break-all font-mono text-sm text-gray-700 dark:text-slate-200">
              {tx.to ? (
                <Link to={`/address/${tx.to}`} className="text-animica-600 hover:underline dark:text-animica-400">
                  {shorten(tx.to, 10, 8)}
                </Link>
              ) : (
                '—'
              )}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">Fee / Gas Used</p>
            <p className="mt-2 font-mono text-sm text-gray-700 dark:text-slate-200">{tx.feePaid ?? tx.gasUsed ?? '—'}</p>
          </div>
        </div>
      </div>

      <JsonViewer data={tx.raw} />
      {tx.receipt ? <JsonViewer data={tx.receipt} label="Receipt" /> : null}
    </div>
  )
}
