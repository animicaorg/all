import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import type { DecodedValue, TxClassificationType, TxDetail } from '@animica/explorer2-shared'
import { api } from '../lib/api'
import { formatNumber, shorten } from '../lib/format'
import CopyButton from '../components/CopyButton'
import JsonViewer from '../components/JsonViewer'
import Skeleton from '../components/Skeleton'

const POLL_INTERVAL_MS = 3000

export function txTypeLabel(value: TxClassificationType | undefined): string {
  if (value === 'contract_deployment') return 'Contract Deployment'
  if (value === 'contract_interaction') return 'Contract Call'
  if (value === 'native_transfer') return 'Native Transfer'
  return 'Unknown'
}

function txTypeBadgeClass(value: TxClassificationType | undefined): string {
  if (value === 'contract_deployment') return 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300'
  if (value === 'contract_interaction') return 'bg-cyan-100 text-cyan-800 dark:bg-cyan-900/30 dark:text-cyan-300'
  if (value === 'native_transfer') return 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900/30 dark:text-indigo-300'
  return 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300'
}

function statusClass(status: string): string {
  if (status === 'confirmed') return 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400'
  if (status === 'failed') return 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400'
  return 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400'
}

function renderDecodedValues(values?: DecodedValue[]) {
  if (!values || values.length === 0) {
    return <p className="text-sm text-gray-500 dark:text-slate-400">No decoded arguments.</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b border-day-200 text-left text-xs uppercase tracking-wide text-gray-500 dark:border-night-800 dark:text-slate-400">
            <th className="px-3 py-2">Name</th>
            <th className="px-3 py-2">Type</th>
            <th className="px-3 py-2">Value</th>
          </tr>
        </thead>
        <tbody>
          {values.map((value, index) => (
            <tr key={`${value.name}:${index}`} className="border-b border-day-100 dark:border-night-800">
              <td className="px-3 py-2 font-mono">{value.name || `arg${index}`}</td>
              <td className="px-3 py-2 font-mono text-xs text-gray-600 dark:text-slate-300">{value.type}</td>
              <td className="px-3 py-2 font-mono text-xs text-gray-700 dark:text-slate-200 break-all">
                {typeof value.value === 'string' ? value.value : JSON.stringify(value.value)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

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

  const confirmations = tx.confirmations ?? (tx.blockHeight && head ? Math.max(0, head.height - tx.blockHeight + 1) : 0)
  const classification = tx.classification
  const classificationType = classification?.type
  const classificationLabel = txTypeLabel(classificationType)
  const rawInput = classification?.rawInput ?? null

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-slate-100">Transaction</h1>
          <CopyButton value={String(tx.tx_hash ?? tx.hash)} />
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className={`inline-flex rounded-full px-3 py-1 text-xs font-medium ${statusClass(tx.status)}`}>
            {tx.status === 'confirmed' ? 'Confirmed' : tx.status === 'failed' ? 'Failed' : 'Pending'}
          </span>
          <span className={`inline-flex rounded-full px-3 py-1 text-xs font-medium ${txTypeBadgeClass(classificationType)}`}>
            {classificationLabel}
          </span>
          {classification?.failed ? (
            <span className="inline-flex rounded-full bg-rose-100 px-3 py-1 text-xs font-medium text-rose-700 dark:bg-rose-900/30 dark:text-rose-300">
              Failed
            </span>
          ) : null}
          {confirmations > 0 ? (
            <span className="inline-flex rounded-full bg-blue-100 px-3 py-1 text-xs font-medium text-blue-800 dark:bg-blue-900/30 dark:text-blue-400">
              {formatNumber(confirmations)} confirmation{confirmations !== 1 ? 's' : ''}
            </span>
          ) : null}
        </div>

        <div className="mt-6 grid gap-6 sm:grid-cols-2">
          <Field label="Transaction Hash" value={tx.tx_hash ?? tx.hash} mono />
          <Field label="Method Selector" value={classification?.methodSelector ?? '—'} mono />
          <Field
            label="Included Block Height"
            value={
              tx.included_height || tx.blockHeight ? (
                <Link className="text-animica-600 hover:underline dark:text-animica-400" to={`/block/${tx.included_height ?? tx.blockHeight}`}>
                  #{formatNumber(tx.included_height ?? tx.blockHeight ?? 0)}
                </Link>
              ) : (
                'Pending'
              )
            }
          />
          <Field label="Included Block Hash" value={tx.included_block_hash ?? tx.blockHash ?? '—'} mono />
          <Field label="Timestamp" value={tx.timestamp ? new Date(tx.timestamp * 1000).toISOString() : '—'} mono />
          <Field
            label="From"
            value={
              tx.from ? (
                <Link to={`/address/${tx.from}`} className="text-animica-600 hover:underline dark:text-animica-400">
                  {shorten(tx.from, 10, 8)}
                </Link>
              ) : (
                '—'
              )
            }
            mono
          />
          <Field
            label="To"
            value={
              tx.to ? (
                <Link to={`/address/${tx.to}`} className="text-animica-600 hover:underline dark:text-animica-400">
                  {shorten(tx.to, 10, 8)}
                </Link>
              ) : (
                '—'
              )
            }
            mono
          />
          <Field label="Value Transfer" value={tx.value ?? '0x0'} mono />
          <Field label="Fee / Gas Used" value={tx.feePaid ?? tx.gasUsed ?? '—'} mono />
          <Field label="Failure Reason" value={classification?.reason ?? '—'} />
          <Field
            label="Created Contract"
            value={
              classification?.createdContractAddress ? (
                <Link className="text-animica-600 hover:underline dark:text-animica-400" to={`/address/${classification.createdContractAddress}`}>
                  {classification.createdContractAddress}
                </Link>
              ) : (
                '—'
              )
            }
            mono
          />
        </div>
      </div>

      <div className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-slate-100">Decoded Input</h2>
        {classification?.decodedCall ? (
          <div className="mt-3 space-y-3">
            <p className="text-sm text-gray-600 dark:text-slate-300">
              Method: <span className="font-mono">{classification.decodedCall.signature ?? classification.decodedCall.name ?? classification.decodedCall.selector}</span>
            </p>
            {renderDecodedValues(classification.decodedCall.args)}
          </div>
        ) : (
          <p className="mt-3 text-sm text-gray-500 dark:text-slate-400">No ABI decode available. Showing raw input below.</p>
        )}
      </div>

      <div className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-slate-100">Decoded Events</h2>
        {!classification?.decodedEvents || classification.decodedEvents.length === 0 ? (
          <p className="mt-3 text-sm text-gray-500 dark:text-slate-400">No decoded events.</p>
        ) : (
          <div className="mt-3 space-y-4">
            {classification.decodedEvents.map((event, index) => (
              <div key={`${event.name ?? 'event'}:${index}`} className="rounded-lg border border-day-200 p-4 dark:border-night-800">
                <p className="font-mono text-sm text-gray-900 dark:text-slate-200">{event.signature ?? event.name ?? `Event ${index}`}</p>
                <div className="mt-2">{renderDecodedValues(event.args)}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-slate-100">Raw Payload</h2>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <Field label="Raw Input" value={rawInput ?? '0x'} mono />
          <Field label="Raw Output" value={classification?.rawOutput ?? '0x'} mono />
        </div>
      </div>

      <JsonViewer data={tx.raw} />
      {tx.receipt ? <JsonViewer data={tx.receipt} label="Receipt" /> : null}
    </div>
  )
}

function Field({ label, value, mono = false }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-slate-500">{label}</p>
      <div className={`mt-2 text-sm text-gray-700 dark:text-slate-200 ${mono ? 'font-mono break-all' : ''}`}>{value}</div>
    </div>
  )
}
