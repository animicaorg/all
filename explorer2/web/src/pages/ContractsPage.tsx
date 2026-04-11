import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { ContractDeployment, ContractDeploymentKind } from '@animica/explorer2-shared'
import { api } from '../lib/api'
import { formatNumber, formatTimestamp, shorten, timeAgo } from '../lib/format'
import ErrorDisplay from '../components/ErrorDisplay'
import Skeleton from '../components/Skeleton'

const POLL_INTERVAL_MS = 8000

export default function ContractsPage() {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.getContractDeployments>> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [retryToken, setRetryToken] = useState(0)

  useEffect(() => {
    let mounted = true

    const fetchData = async () => {
      if (!mounted) return
      try {
        const result = await api.getContractDeployments(30, 320)
        if (!mounted) return
        setData(result)
        setError(null)
      } catch (err) {
        if (!mounted) return
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (mounted) setLoading(false)
      }
    }

    fetchData()
    const interval = setInterval(() => {
      if (document.visibilityState === 'visible') fetchData()
    }, POLL_INTERVAL_MS)

    return () => {
      mounted = false
      clearInterval(interval)
    }
  }, [retryToken])

  if (error) {
    return (
      <ErrorDisplay
        error={error}
        onRetry={() => {
          setLoading(true)
          setData(null)
          setError(null)
          setRetryToken((value) => value + 1)
        }}
      />
    )
  }

  const spotlight = data?.spotlight ?? null
  const deployments = data?.items ?? []

  return (
    <div className="space-y-6">
      <section className="relative overflow-hidden rounded-3xl border border-day-200 bg-gradient-to-br from-cyan-50 via-white to-sky-100 p-6 shadow-sm dark:border-night-800 dark:bg-gradient-to-br dark:from-night-900 dark:via-night-900 dark:to-slate-900 sm:p-8">
        <div className="pointer-events-none absolute -right-24 -top-20 h-56 w-56 rounded-full bg-animica-500/20 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-20 -left-16 h-48 w-48 rounded-full bg-emerald-300/20 blur-3xl dark:bg-emerald-500/10" />
        <div className="relative grid gap-6 lg:grid-cols-[1.2fr_0.8fr] lg:items-center">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-700 dark:text-cyan-300">
              Launch Radar
            </p>
            <h1 className="mt-2 text-3xl font-bold text-gray-900 dark:text-slate-100 sm:text-4xl">
              Deployed Contracts, Live
            </h1>
            <p className="mt-3 max-w-2xl text-sm leading-relaxed text-gray-700 dark:text-slate-300">
              Breakthrough view over contract creation and package deployment flow, rebuilt every few seconds directly
              from chain blocks and receipts.
            </p>
            <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <MetricCard label="Contracts Tracked" value={data ? formatNumber(data.stats.total) : '—'} />
              <MetricCard label="Successful" value={data ? formatNumber(data.stats.successful) : '—'} />
              <MetricCard label="Unique Deployers" value={data ? formatNumber(data.stats.uniqueDeployers) : '—'} />
              <MetricCard label="Blocks Scanned" value={data ? formatNumber(data.scannedBlocks) : '—'} />
            </div>
          </div>
          <div className="mx-auto flex w-full max-w-sm items-center justify-center rounded-2xl border border-cyan-200/70 bg-white/70 p-5 backdrop-blur dark:border-night-700 dark:bg-night-900/60">
            {loading && !data ? (
              <Skeleton className="h-44 w-full rounded-xl" />
            ) : (
              <SpotlightDial deployment={spotlight} />
            )}
          </div>
        </div>
      </section>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        {loading && !data
          ? Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-24 rounded-xl" />)
          : (
            <>
              <QuickStat title="Head Height" value={data ? `#${formatNumber(data.headHeight)}` : '—'} />
              <QuickStat title="Failed Deploys" value={data ? formatNumber(data.stats.failed) : '—'} />
              <QuickStat title="Unique Contracts" value={data ? formatNumber(data.stats.uniqueContracts) : '—'} />
              <QuickStat title="Success Rate" value={data ? formatRate(data.stats.successful, data.stats.total) : '—'} />
              <QuickStat title="Refresh Cycle" value="8s" />
            </>
          )}
      </section>

      <section className="overflow-hidden rounded-2xl border border-day-200 bg-white shadow-sm dark:border-night-800 dark:bg-night-900">
        <div className="border-b border-day-200 bg-day-50 px-5 py-4 dark:border-night-800 dark:bg-night-800">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-700 dark:text-slate-300">
            Deployment Stream
          </h2>
        </div>
        <div className="divide-y divide-day-200 dark:divide-night-800">
          {loading && !data ? (
            Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="px-5 py-4">
                <Skeleton className="h-12 w-full rounded-lg" />
              </div>
            ))
          ) : deployments.length > 0 ? (
            deployments.map((item) => (
              <DeploymentRow key={`${item.txHash}-${item.blockHeight}`} item={item} />
            ))
          ) : (
            <div className="px-5 py-10 text-center text-sm text-gray-600 dark:text-slate-400">
              No deployments detected in the scanned range.
            </div>
          )}
        </div>
      </section>
    </div>
  )
}

function SpotlightDial({ deployment }: { deployment: ContractDeployment | null }) {
  return (
    <div className="flex w-full flex-col items-center gap-4 text-center">
      <div className="relative h-36 w-36">
        <div className="absolute inset-0 rounded-full border border-cyan-300/70 dark:border-cyan-500/40" />
        <div className="absolute inset-4 rounded-full border border-animica-400/60 dark:border-animica-500/50" />
        <div className="absolute inset-8 rounded-full border border-emerald-300/70 dark:border-emerald-500/35" />
        <div className="absolute left-1/2 top-2 h-[calc(50%-0.5rem)] w-px -translate-x-1/2 origin-bottom animate-[spin_8s_linear_infinite] bg-gradient-to-t from-transparent to-animica-500/90" />
        <div className="absolute left-1/2 top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-animica-600 shadow-[0_0_24px_rgba(59,167,255,0.75)] dark:bg-animica-400" />
      </div>
      {deployment ? (
        <div className="space-y-1">
          <p className="text-xs font-semibold uppercase tracking-[0.15em] text-cyan-700 dark:text-cyan-300">
            Latest Highlight
          </p>
          <Link className="font-mono text-sm text-animica-700 hover:underline dark:text-animica-300" to={`/tx/${deployment.txHash}`}>
            {shorten(deployment.txHash, 14, 10)}
          </Link>
          <p className="text-xs text-gray-600 dark:text-slate-400">
            {deployment.label ?? kindLabel(deployment.kind)} • {timeAgo(deployment.blockTime)}
          </p>
        </div>
      ) : (
        <p className="text-sm text-gray-600 dark:text-slate-400">Waiting for a deployment signal</p>
      )}
    </div>
  )
}

function DeploymentRow({ item }: { item: ContractDeployment }) {
  const kindClass = kindBadgeClasses(item.kind)
  return (
    <div className="px-5 py-4 transition-colors hover:bg-day-50 dark:hover:bg-night-800/60">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className={kindClass}>{kindLabel(item.kind)}</span>
            <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${item.status === 'failed' ? 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300' : 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'}`}>
              {item.status}
            </span>
            <Link className="font-mono text-xs text-animica-700 hover:underline dark:text-animica-300" to={`/tx/${item.txHash}`}>
              {shorten(item.txHash, 14, 10)}
            </Link>
          </div>
          <p className="text-sm font-medium text-gray-800 dark:text-slate-200">
            {item.label ?? 'Unnamed deployment'} at block{' '}
            <Link className="text-animica-700 hover:underline dark:text-animica-300" to={`/block/${item.blockHeight}`}>
              #{formatNumber(item.blockHeight)}
            </Link>
          </p>
          <p className="text-xs text-gray-600 dark:text-slate-400">
            {item.blockTime ? `${timeAgo(item.blockTime)} • ${formatTimestamp(item.blockTime)}` : 'Timestamp unavailable'}
          </p>
        </div>
        <div className="grid min-w-[210px] gap-1 text-xs text-gray-700 dark:text-slate-300">
          <span>
            Deployer:{' '}
            {item.deployer ? (
              <Link className="font-mono text-animica-700 hover:underline dark:text-animica-300" to={`/address/${item.deployer}`}>
                {shorten(item.deployer, 10, 8)}
              </Link>
            ) : '—'}
          </span>
          <span>
            Contract:{' '}
            {item.contractAddress ? (
              <Link className="font-mono text-animica-700 hover:underline dark:text-animica-300" to={`/address/${item.contractAddress}`}>
                {shorten(item.contractAddress, 12, 8)}
              </Link>
            ) : '—'}
          </span>
          <span>
            Fee: <span className="font-mono">{item.feePaid ?? '—'}</span>
          </span>
          <span>
            Code: {item.codeSizeBytes !== null && item.codeSizeBytes !== undefined ? formatBytes(item.codeSizeBytes) : '—'}
          </span>
        </div>
      </div>
    </div>
  )
}

function QuickStat({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-xl border border-day-200 bg-white p-4 shadow-sm dark:border-night-800 dark:bg-night-900">
      <p className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-slate-400">{title}</p>
      <p className="mt-2 text-xl font-bold text-gray-900 dark:text-slate-100">{value}</p>
    </div>
  )
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-cyan-200/70 bg-white/80 px-3 py-2 backdrop-blur dark:border-night-700 dark:bg-night-900/70">
      <p className="text-[11px] uppercase tracking-wide text-gray-600 dark:text-slate-400">{label}</p>
      <p className="mt-1 text-lg font-semibold text-gray-900 dark:text-slate-100">{value}</p>
    </div>
  )
}

function kindLabel(kind: ContractDeploymentKind): string {
  switch (kind) {
    case 'contract_create':
      return 'Contract Create'
    case 'manifest_deploy':
      return 'Manifest Deploy'
    case 'package_publish':
      return 'Package Publish'
    default:
      return 'Deployment'
  }
}

function kindBadgeClasses(kind: ContractDeploymentKind): string {
  switch (kind) {
    case 'contract_create':
      return 'rounded-full bg-animica-100 px-2 py-0.5 text-xs font-semibold text-animica-700 dark:bg-animica-900/40 dark:text-animica-300'
    case 'manifest_deploy':
      return 'rounded-full bg-cyan-100 px-2 py-0.5 text-xs font-semibold text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-300'
    case 'package_publish':
      return 'rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
    default:
      return 'rounded-full bg-gray-100 px-2 py-0.5 text-xs font-semibold text-gray-700 dark:bg-gray-800 dark:text-gray-300'
  }
}

function formatRate(successful: number, total: number): string {
  if (!total) return '—'
  return `${((successful / total) * 100).toFixed(1)}%`
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
