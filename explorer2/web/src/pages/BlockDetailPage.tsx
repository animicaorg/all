import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import type { BlockDetail } from '@animica/explorer2-shared'
import { api } from '../lib/api'
import { formatBalance, formatNumber, formatTimestamp, shorten, timeAgo } from '../lib/format'
import CopyButton from '../components/CopyButton'
import JsonViewer from '../components/JsonViewer'
import Skeleton from '../components/Skeleton'

export default function BlockDetailPage() {
  const { hashOrHeight } = useParams()
  const [block, setBlock] = useState<BlockDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!hashOrHeight) return
    api
      .getBlock(hashOrHeight)
      .then((res) => setBlock(res))
      .catch((err) => setError(String(err)))
  }, [hashOrHeight])

  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-900/10 dark:text-red-100">
        <strong className="font-semibold">Error:</strong> {error}
      </div>
    )
  }

  if (!block) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-40" />
        <Skeleton className="h-60" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-slate-100">
            Block #{formatNumber(block.height)}
            {block.nonce === 0 && (
              <span className="ml-3 inline-flex items-center rounded-full bg-blue-100 px-3 py-1 text-xs font-medium text-blue-800 dark:bg-blue-900/30 dark:text-blue-300">
                Instant Block
              </span>
            )}
          </h1>
          <CopyButton value={block.hash} />
        </div>
        
        <div className="mt-6 grid gap-6 sm:grid-cols-2">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">Block Height</p>
            <p className="mt-2 font-mono text-sm text-gray-900 dark:text-slate-200">
              {formatNumber(block.height)}
              {block.canonicalHeight !== undefined && block.canonicalHeight !== block.height && (
                <span className="ml-2 text-xs text-gray-500 dark:text-slate-400">
                  (canonical: {formatNumber(block.canonicalHeight)})
                </span>
              )}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">Block Hash</p>
            <p className="mt-2 break-all font-mono text-sm text-gray-900 dark:text-slate-200">{block.hash}</p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">Parent Hash</p>
            <p className="mt-2 font-mono text-sm text-gray-600 dark:text-slate-300">
              {block.parentHash ? (
                <Link to={`/block/${block.parentHash}`} className="text-animica-600 hover:underline dark:text-animica-400">
                  {shorten(block.parentHash)}
                </Link>
              ) : (
                '—'
              )}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">Timestamp</p>
            <p className="mt-2 text-sm text-gray-700 dark:text-slate-200">
              {timeAgo(block.time)} · {formatTimestamp(block.time)}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">Difficulty / Target</p>
            <p className="mt-2 font-mono text-sm text-gray-700 dark:text-slate-200">{block.difficulty ?? '—'}</p>
          </div>
          {block.nonce !== undefined && (
            <div>
              <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">Nonce</p>
              <p className="mt-2 font-mono text-sm text-gray-700 dark:text-slate-200">
                {block.nonce === 0 ? (
                  <span className="text-blue-600 dark:text-blue-400">0 (instant)</span>
                ) : (
                  block.nonce
                )}
              </p>
            </div>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-day-200 bg-white shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="border-b border-day-200 px-6 py-4 dark:border-night-800">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-slate-100">
            Transactions ({block.txs.length})
          </h2>
        </div>
        <div className="divide-y divide-day-200 dark:divide-night-800">
          {block.txs.length === 0 && (
            <div className="px-6 py-8 text-center text-sm text-gray-500 dark:text-slate-400">
              No transactions in this block.
            </div>
          )}
          {block.txs.map((tx, idx) => (
            <div key={tx.hash} className="flex flex-wrap items-center justify-between gap-3 px-6 py-4 hover:bg-day-50 dark:hover:bg-night-800/50">
              <div className="min-w-0 flex-1">
                <Link 
                  to={`/tx/${tx.hash}`} 
                  className="block font-mono text-sm text-animica-600 hover:underline dark:text-animica-400"
                >
                  {shorten(tx.hash, 10, 8)}
                </Link>
                <div className="mt-1 flex flex-wrap gap-2 text-xs text-gray-500 dark:text-slate-500">
                  <span>Index: {idx}</span>
                  {tx.from && <span>From: {shorten(tx.from, 8, 6)}</span>}
                  {tx.to && <span>To: {shorten(tx.to, 8, 6)}</span>}
                  <span>Amount: {tx.value ? `${formatBalance(tx.value).anm} ANM` : '—'}</span>
                </div>
              </div>
              <CopyButton value={tx.hash} className="flex-shrink-0" />
            </div>
          ))}
        </div>
      </div>

      <JsonViewer data={block.raw} />
    </div>
  )
}
