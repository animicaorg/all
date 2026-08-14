import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { L2BatchDetail } from '../lib/api'
import { formatBalance, formatNumber, formatTimestamp, timeAgo } from '../lib/format'
import CopyButton from '../components/CopyButton'
import JsonViewer from '../components/JsonViewer'
import Skeleton from '../components/Skeleton'

function proofBadge(hasProof: boolean | undefined) {
  if (hasProof) {
    return (
      <span className="inline-flex items-center rounded-full bg-green-100 px-3 py-1 text-xs font-medium text-green-800 dark:bg-green-900/30 dark:text-green-400">
        Proven
      </span>
    )
  }
  return (
    <span className="inline-flex items-center rounded-full bg-yellow-100 px-3 py-1 text-xs font-medium text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400">
      Pending proof
    </span>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">{label}</p>
      <div className="mt-2 break-all font-mono text-sm text-gray-900 dark:text-slate-200">{children}</div>
    </div>
  )
}

function anm(value?: string | null): string {
  if (value === undefined || value === null) return '—'
  return `${formatBalance(value).anm} ANM`
}

export default function L2BatchPage() {
  const { n } = useParams()
  const [batch, setBatch] = useState<L2BatchDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (n === undefined) return
    setBatch(null)
    setError(null)
    api
      .getL2Batch(n)
      .then((res) => setBatch(res))
      .catch((err) => setError(String(err)))
  }, [n])

  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-900/10 dark:text-red-100">
        <strong className="font-semibold">Error:</strong> {error}
      </div>
    )
  }

  if (!batch) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-60" />
      </div>
    )
  }

  const num = batch.number ?? Number(n)

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 className="flex flex-wrap items-center gap-3 text-2xl font-bold text-gray-900 dark:text-slate-100">
            L2 Batch #{formatNumber(num)}
            {proofBadge(batch.proof?.hasProof)}
          </h1>
          {batch.batchId && <CopyButton value={batch.batchId} />}
        </div>

        <div className="mt-6 grid gap-6 sm:grid-cols-2">
          <Field label="Batch Number">{formatNumber(num)}</Field>
          <Field label="L2 Chain ID">{batch.l2ChainId ?? '—'}</Field>
          <Field label="Batch ID">{batch.batchId ?? '—'}</Field>
          <Field label="Transactions">{formatNumber(batch.txCount ?? 0)}</Field>
          <Field label="Timestamp">
            {batch.timestampMs
              ? `${timeAgo(Math.floor(batch.timestampMs / 1000))} · ${formatTimestamp(Math.floor(batch.timestampMs / 1000))}`
              : '—'}
          </Field>
          <Field label="Fees Collected">{anm(batch.feesCollected)}</Field>
          <Field label="Deposited">{anm(batch.deposited)}</Field>
          <Field label="Withdrawn">{anm(batch.withdrawn)}</Field>
          <Field label="Previous State Root">{batch.prevStateRoot ?? '—'}</Field>
          <Field label="New State Root">{batch.newStateRoot ?? '—'}</Field>
          <Field label="Transactions Root">{batch.transactionsRoot ?? '—'}</Field>
          <Field label="Receipts Root">{batch.receiptsRoot ?? '—'}</Field>
          <Field label="Escrow Root">{batch.escrowRoot ?? '—'}</Field>
          <Field label="Data Root">{batch.dataRoot ?? '—'}</Field>
        </div>
      </div>

      <div className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <h2 className="mb-4 text-lg font-semibold text-gray-900 dark:text-slate-100">Proof Status</h2>
        <div className="grid gap-6 sm:grid-cols-2">
          <Field label="Status">{proofBadge(batch.proof?.hasProof)}</Field>
          <Field label="Backend">{batch.proof?.backend ?? '—'}</Field>
          <Field label="Public Inputs Digest">{batch.proof?.publicInputsDigest ?? '—'}</Field>
        </div>
      </div>

      <div className="flex flex-wrap gap-3">
        {num > 0 && (
          <Link
            to={`/l2/batch/${num - 1}`}
            className="rounded-lg border border-day-300 bg-day-50 px-4 py-2 text-sm font-medium text-gray-700 hover:border-animica-500 hover:text-animica-700 dark:border-night-700 dark:bg-night-800 dark:text-slate-300 dark:hover:border-animica-500 dark:hover:text-animica-400"
          >
            ← Batch #{formatNumber(num - 1)}
          </Link>
        )}
        <Link
          to={`/l2/batch/${num + 1}`}
          className="rounded-lg border border-day-300 bg-day-50 px-4 py-2 text-sm font-medium text-gray-700 hover:border-animica-500 hover:text-animica-700 dark:border-night-700 dark:bg-night-800 dark:text-slate-300 dark:hover:border-animica-500 dark:hover:text-animica-400"
        >
          Batch #{formatNumber(num + 1)} →
        </Link>
        <Link
          to="/l2"
          className="rounded-lg border border-day-300 bg-day-50 px-4 py-2 text-sm font-medium text-gray-700 hover:border-animica-500 hover:text-animica-700 dark:border-night-700 dark:bg-night-800 dark:text-slate-300 dark:hover:border-animica-500 dark:hover:text-animica-400"
        >
          L2 Overview
        </Link>
      </div>

      <JsonViewer data={batch} />
    </div>
  )
}
