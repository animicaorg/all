import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { L2TxDetail } from '../lib/api'
import { formatBalance, formatNumber, formatTimestamp, timeAgo } from '../lib/format'
import CopyButton from '../components/CopyButton'
import JsonViewer from '../components/JsonViewer'
import Skeleton from '../components/Skeleton'

const POLL_INTERVAL_MS = 3000
const TERMINAL = new Set(['PROVEN', 'L1_FINALIZED', 'FAILED', 'REVERTED'])

function statusClass(status?: string): string {
  if (status === 'PROVEN' || status === 'L1_FINALIZED' || status === 'L1_SUBMITTED') {
    return 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400'
  }
  if (status === 'FAILED' || status === 'REVERTED') {
    return 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400'
  }
  return 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400'
}

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

function addressField(value?: string) {
  if (!value) return <span>—</span>
  return (
    <Link to={`/address/${value}`} className="text-animica-600 hover:underline dark:text-animica-400">
      {value}
    </Link>
  )
}

export default function L2TxPage() {
  const { hash } = useParams()
  const [tx, setTx] = useState<L2TxDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (!hash) return
    let mounted = true
    setTx(null)
    setError(null)

    const stop = () => {
      if (timerRef.current) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }

    const fetchData = () => {
      api
        .getL2Tx(hash)
        .then((res) => {
          if (!mounted) return
          setTx(res)
          setError(null)
          if (res.status && TERMINAL.has(res.status)) stop()
        })
        .catch((err) => {
          if (mounted) setError(String(err))
        })
    }

    fetchData()
    timerRef.current = setInterval(() => {
      if (mounted && document.visibilityState === 'visible') fetchData()
    }, POLL_INTERVAL_MS)

    return () => {
      mounted = false
      stop()
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
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-60" />
      </div>
    )
  }

  const sender = tx.sender ?? tx.from
  const recipient = tx.recipient ?? tx.to
  const kind = tx.kind ?? tx.type
  const l1Tx = tx.l1SettlementTx ?? tx.l1Tx
  const hasBatch = tx.batch !== undefined && tx.batch >= 0

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-bold text-gray-900 dark:text-slate-100">L2 Transaction</h1>
            <span className="inline-flex items-center rounded-full bg-blue-100 px-3 py-1 text-xs font-medium text-blue-800 dark:bg-blue-900/30 dark:text-blue-300">
              ANM Instant
            </span>
            <span className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-medium ${statusClass(tx.status)}`}>
              {tx.status ?? '—'}
            </span>
          </div>
          {tx.txid && <CopyButton value={tx.txid} />}
        </div>

        <div className="mt-6 grid gap-6 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <Field label="Transaction ID">{tx.txid ?? '—'}</Field>
          </div>
          <Field label="Type">{kind ?? '—'}</Field>
          <Field label="Nonce">{tx.nonce !== undefined ? formatNumber(tx.nonce) : '—'}</Field>
          <Field label="Sender">{addressField(sender)}</Field>
          <Field label="Recipient">{addressField(recipient)}</Field>
          <Field label="Amount">{tx.amount !== undefined ? `${formatBalance(tx.amount).anm} ANM` : '—'}</Field>
          <Field label="Fee">{tx.fee !== undefined ? `${formatBalance(tx.fee).anm} ANM` : '—'}</Field>
          <Field label="Batch">
            {hasBatch ? (
              <Link to={`/l2/batch/${tx.batch}`} className="text-animica-600 hover:underline dark:text-animica-400">
                #{formatNumber(tx.batch as number)}
              </Link>
            ) : (
              'not yet batched'
            )}
          </Field>
          <Field label="Soft-confirmed">
            {tx.receivedMs
              ? `${timeAgo(Math.floor(tx.receivedMs / 1000))} · ${formatTimestamp(Math.floor(tx.receivedMs / 1000))}`
              : '—'}
          </Field>
          {tx.reason && (
            <div className="sm:col-span-2">
              <Field label="Reason">{tx.reason}</Field>
            </div>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <h2 className="mb-4 text-lg font-semibold text-gray-900 dark:text-slate-100">Settlement</h2>
        <div className="grid gap-6 sm:grid-cols-2">
          <Field label="Proof Status">{proofBadge(tx.proof?.hasProof)}</Field>
          <Field label="Proof Backend">{tx.proof?.backend ?? '—'}</Field>
          <Field label="Receipt Status">{tx.receipt?.status ?? '—'}</Field>
          <Field label="L1 Settlement Tx">
            {l1Tx ? (
              <Link to={`/tx/${l1Tx}`} className="text-animica-600 hover:underline dark:text-animica-400">
                {l1Tx}
              </Link>
            ) : (
              'not yet settled to L1'
            )}
          </Field>
        </div>
        <p className="mt-4 text-xs text-gray-500 dark:text-slate-400">
          Sequencer acceptance (soft confirmation) is not L1 settlement. This transaction is only final
          once its batch is proven and submitted to L1.
        </p>
      </div>

      <JsonViewer data={tx} />
    </div>
  )
}
