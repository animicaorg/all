import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import type { L2Overview } from '../lib/api'
import { formatBalance, formatNumber, shorten } from '../lib/format'
import StatCard from '../components/StatCard'
import Skeleton from '../components/Skeleton'
import ErrorDisplay from '../components/ErrorDisplay'

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

function anm(value?: string | null): string {
  if (value === undefined || value === null) return '—'
  return `${formatBalance(value).anm} ANM`
}

export default function L2Page() {
  const [data, setData] = useState<L2Overview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refetchTrigger, setRefetchTrigger] = useState(0)

  useEffect(() => {
    let mounted = true

    const fetchData = () => {
      if (!mounted) return
      api
        .getL2Overview()
        .then((res) => {
          if (mounted) {
            setData(res)
            setError(null)
          }
        })
        .catch((err) => {
          if (mounted) setError(String(err))
        })
    }

    fetchData()
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

  if (error) {
    return (
      <ErrorDisplay
        error={error}
        onRetry={() => {
          setError(null)
          setData(null)
          setRefetchTrigger((prev) => prev + 1)
        }}
      />
    )
  }

  if (!data) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-48" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      </div>
    )
  }

  if (!data.enabled) {
    return (
      <div className="space-y-6">
        <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
          <h1 className="mb-1 text-2xl font-semibold text-gray-900 dark:text-slate-100">ANM Instant (L2)</h1>
          <p className="text-sm text-gray-600 dark:text-slate-400">
            The ANM-native Layer 2 for instant, low-fee transfers of the same ANM asset.
          </p>
        </div>
        <div className="rounded-xl border border-yellow-200 bg-yellow-50 p-4 dark:border-yellow-800 dark:bg-yellow-900/20">
          <p className="font-medium text-yellow-800 dark:text-yellow-300">Animica L2 is not active on this node</p>
          <p className="mt-1 text-sm text-yellow-700 dark:text-yellow-400">
            The connected node does not have ANM Instant (L2) enabled, so no settlement, batch, or
            throughput data is available.
          </p>
        </div>
      </div>
    )
  }

  const tps = data.tps ?? {}
  const softTps = tps.softConfirmedTps
  const settledTps = tps.settledTps

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="mb-1 text-2xl font-semibold text-gray-900 dark:text-slate-100">ANM Instant (L2)</h1>
            <p className="text-sm text-gray-600 dark:text-slate-400">
              Settlement mode <span className="font-mono">{data.settlementMode ?? '—'}</span>
              {data.sigBackend ? (
                <>
                  {' '}· signatures <span className="font-mono">{data.sigBackend}</span>
                </>
              ) : null}
            </p>
          </div>
          <div className="flex items-center gap-3">
            {proofBadge(data.proofStatus?.hasProof)}
            {data.headBatch !== undefined && data.headBatch >= 0 && (
              <Link
                to={`/l2/batch/${data.headBatch}`}
                className="rounded-lg border border-day-300 bg-day-50 px-3 py-1.5 text-xs font-medium text-gray-700 hover:border-animica-500 hover:text-animica-700 dark:border-night-700 dark:bg-night-800 dark:text-slate-300 dark:hover:border-animica-500 dark:hover:text-animica-400"
              >
                Head batch #{formatNumber(data.headBatch)}
              </Link>
            )}
          </div>
        </div>
      </div>

      <section>
        <h2 className="mb-4 text-lg font-semibold text-gray-900 dark:text-slate-100">Settlement</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <StatCard label="Settlement Mode" value={<span className="font-mono text-base">{data.settlementMode ?? '—'}</span>} />
          <StatCard
            label="Head Batch"
            value={
              data.headBatch !== undefined && data.headBatch >= 0 ? (
                <Link to={`/l2/batch/${data.headBatch}`} className="text-animica-600 hover:underline dark:text-animica-400">
                  #{formatNumber(data.headBatch)}
                </Link>
              ) : (
                '—'
              )
            }
          />
          <StatCard
            label="Proof Status"
            value={<span className="text-base">{proofBadge(data.proofStatus?.hasProof)}</span>}
          />
          <StatCard
            label="Latest State Root"
            value={
              <span className="truncate font-mono text-sm" title={data.latestStateRoot ?? undefined}>
                {data.latestStateRoot ? shorten(data.latestStateRoot, 10, 8) : '—'}
              </span>
            }
          />
          <StatCard label="Pending" value={formatNumber(data.pending ?? 0)} />
          <StatCard label="Pending Proofs" value={formatNumber(data.pendingProofs ?? 0)} />
        </div>
      </section>

      <section>
        <h2 className="mb-4 text-lg font-semibold text-gray-900 dark:text-slate-100">Throughput</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Soft TPS"
            value={softTps !== undefined ? softTps.toFixed(2) : '—'}
          />
          <StatCard
            label="Settled TPS"
            value={settledTps !== undefined ? settledTps.toFixed(2) : '—'}
          />
          <StatCard label="Total L2 Transactions" value={formatNumber(data.totalL2Transactions ?? 0)} />
          <StatCard label="Latest Batch Tx Count" value={formatNumber(data.batchTransactions ?? 0)} />
        </div>
      </section>

      <section>
        <h2 className="mb-4 text-lg font-semibold text-gray-900 dark:text-slate-100">Value & Data</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <StatCard label="ANM Locked (L1 bridge)" value={<span className="text-base">{anm(data.anmLocked)}</span>} />
          <StatCard label="L2 Supply" value={<span className="text-base">{anm(data.l2Supply)}</span>} />
          <StatCard
            label="Compression Ratio"
            value={
              data.compressionRatio !== undefined && data.compressionRatio !== null
                ? `${data.compressionRatio.toFixed(2)}×`
                : '—'
            }
          />
          <StatCard label="Deposits" value={formatNumber(data.deposits ?? 0)} />
          <StatCard label="Withdrawals" value={formatNumber(data.withdrawals ?? 0)} />
          <StatCard label="Settled Total" value={formatNumber(data.settledTotal ?? 0)} />
        </div>
      </section>

      <section className="rounded-xl border border-day-200 bg-white p-6 shadow-sm dark:border-night-800 dark:bg-night-900">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-slate-100">About ANM Instant</h2>
        <p className="mt-3 text-sm leading-relaxed text-gray-600 dark:text-slate-400">
          ANM Instant is the Animica Layer 2. It settles the same ANM asset as the base chain: ANM
          deposited to the bridge is locked on L1 and credited on L2, and withdrawals unlock it back on
          L1. Sequencer acceptance (soft confirmation) is fast, but transactions are only final once
          their batch is <span className="font-medium">proven</span> and settled to L1.
        </p>
      </section>
    </div>
  )
}
