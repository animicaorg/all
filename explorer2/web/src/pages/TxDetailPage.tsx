import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import type { TxDetail } from '@animica/explorer2-shared'
import { api } from '../lib/api'
import { formatNumber, shorten } from '../lib/format'
import CopyButton from '../components/CopyButton'
import JsonViewer from '../components/JsonViewer'
import Skeleton from '../components/Skeleton'

export default function TxDetailPage() {
  const { hash } = useParams()
  const [tx, setTx] = useState<TxDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!hash) return
    api
      .getTx(hash)
      .then((res) => setTx(res))
      .catch((err) => setError(String(err)))
  }, [hash])

  if (error) {
    return (
      <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-6 text-sm text-red-100">
        Failed to load transaction. {error}
      </div>
    )
  }

  if (!tx) {
    return <Skeleton className="h-40" />
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-night-800 bg-night-900 p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-xl font-semibold">Transaction</h1>
          <CopyButton value={tx.hash} />
        </div>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div>
            <p className="text-xs uppercase text-slate-500">Hash</p>
            <p className="mt-1 text-sm text-slate-200">{tx.hash}</p>
          </div>
          <div>
            <p className="text-xs uppercase text-slate-500">Status</p>
            <p className="mt-1 text-sm text-slate-200">
              {tx.status === 'confirmed' ? 'Confirmed' : tx.status === 'failed' ? 'Failed' : 'Pending'}
            </p>
          </div>
          <div>
            <p className="text-xs uppercase text-slate-500">From</p>
            <p className="mt-1 text-sm text-slate-200">{tx.from ?? '—'}</p>
          </div>
          <div>
            <p className="text-xs uppercase text-slate-500">To</p>
            <p className="mt-1 text-sm text-slate-200">{tx.to ?? '—'}</p>
          </div>
          <div>
            <p className="text-xs uppercase text-slate-500">Block</p>
            {tx.blockHeight ? (
              <Link className="mt-1 text-sm text-animica-400 hover:underline" to={`/block/${tx.blockHeight}`}>
                #{formatNumber(tx.blockHeight)}
              </Link>
            ) : (
              <p className="mt-1 text-sm text-slate-200">Pending</p>
            )}
          </div>
          <div>
            <p className="text-xs uppercase text-slate-500">Fee paid</p>
            <p className="mt-1 text-sm text-slate-200">{tx.feePaid ?? tx.gasUsed ?? '—'}</p>
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-night-800 bg-night-900 p-6">
        <h2 className="text-lg font-semibold">Summary</h2>
        <div className="mt-3 text-sm text-slate-400">
          <p>Inputs/outputs are displayed based on available Animica transaction fields.</p>
          <p className="mt-2">Transaction: {shorten(tx.hash)}</p>
        </div>
      </div>

      <JsonViewer data={tx.raw} />
      {tx.receipt && <JsonViewer data={tx.receipt} label="Receipt" />}
    </div>
  )
}
