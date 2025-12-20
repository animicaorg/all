import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import type { BlockDetail } from '@animica/explorer2-shared'
import { api } from '../lib/api'
import { formatNumber, formatTimestamp, shorten, timeAgo } from '../lib/format'
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
      <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-6 text-sm text-red-100">
        Failed to load block. {error}
      </div>
    )
  }

  if (!block) {
    return <Skeleton className="h-40" />
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-night-800 bg-night-900 p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-xl font-semibold">Block #{formatNumber(block.height)}</h1>
          <CopyButton value={block.hash} />
        </div>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div>
            <p className="text-xs uppercase text-slate-500">Hash</p>
            <p className="mt-1 text-sm text-slate-200">{block.hash}</p>
          </div>
          <div>
            <p className="text-xs uppercase text-slate-500">Parent</p>
            <p className="mt-1 text-sm text-slate-200">{block.parentHash ? shorten(block.parentHash) : '—'}</p>
          </div>
          <div>
            <p className="text-xs uppercase text-slate-500">Timestamp</p>
            <p className="mt-1 text-sm text-slate-200">
              {timeAgo(block.time)} · {formatTimestamp(block.time)}
            </p>
          </div>
          <div>
            <p className="text-xs uppercase text-slate-500">Difficulty / Target</p>
            <p className="mt-1 text-sm text-slate-200">{block.difficulty ?? '—'}</p>
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-night-800 bg-night-900">
        <div className="border-b border-night-800 px-6 py-4">
          <h2 className="text-lg font-semibold">Transactions</h2>
        </div>
        <div className="divide-y divide-night-800">
          {block.txs.length === 0 && (
            <div className="px-6 py-4 text-sm text-slate-400">No transactions in this block.</div>
          )}
          {block.txs.map((tx) => (
            <div key={tx.hash} className="flex flex-wrap items-center justify-between gap-3 px-6 py-4">
              <div>
                <Link to={`/tx/${tx.hash}`} className="text-animica-400 hover:underline">
                  {shorten(tx.hash)}
                </Link>
                <div className="text-xs text-slate-500">{tx.from ?? '—'} → {tx.to ?? '—'}</div>
              </div>
              <CopyButton value={tx.hash} />
            </div>
          ))}
        </div>
      </div>

      <JsonViewer data={block.raw} />
    </div>
  )
}
