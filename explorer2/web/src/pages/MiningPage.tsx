import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { formatError, LOCAL_RPC } from '../lib/rpcUtils'

interface MiningData {
  available: boolean
  status?: Record<string, unknown>
  template?: Record<string, unknown>
  metrics?: Record<string, unknown>
}

export default function MiningPage() {
  const [data, setData] = useState<MiningData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<{ message: string; hint: string; remediation: string } | null>(null)

  const refresh = () => {
    setLoading(true)
    setError(null)
    api.getMiningInfo()
      .then(d => setData(d as MiningData))
      .catch(err => setError(formatError(err)))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    refresh()
    // Auto-refresh every 30 seconds
    const interval = setInterval(refresh, 30000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <div>
          <h1 className="mb-1 text-2xl font-semibold">Mining Dashboard</h1>
          <p className="text-sm text-gray-600 dark:text-slate-400">
            Template lifecycle, stale counters, and mining metrics.
          </p>
        </div>
        <button
          onClick={refresh}
          disabled={loading}
          className="rounded-lg border border-day-200 px-3 py-1.5 text-sm hover:bg-gray-50 disabled:opacity-50 dark:border-night-700 dark:hover:bg-night-800"
        >
          {loading ? 'Refreshing...' : '↻ Refresh'}
        </button>
      </div>

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 dark:border-red-800 dark:bg-red-900/20">
          <p className="font-medium text-red-800 dark:text-red-300">{error.message}</p>
          {error.hint && <p className="mt-1 text-sm text-red-700 dark:text-red-400">{error.hint}</p>}
          {error.remediation && (
            <p className="mt-1 text-sm italic text-red-600 dark:text-red-500">{error.remediation}</p>
          )}
        </div>
      )}

      {data && !data.available && !error && (
        <div className="rounded-xl border border-yellow-200 bg-yellow-50 p-4 dark:border-yellow-800 dark:bg-yellow-900/20">
          <p className="font-medium text-yellow-800 dark:text-yellow-300">
            ⚠ Mining RPC methods are not available on this node
          </p>
          <p className="mt-1 text-sm text-yellow-700 dark:text-yellow-400">
            The connected node does not expose miner.* RPC methods.
          </p>
          <details className="mt-3">
            <summary className="cursor-pointer text-sm text-yellow-600 dark:text-yellow-500">
              Copy curl example
            </summary>
            <pre className="mt-2 overflow-x-auto rounded bg-yellow-100 p-2 text-xs dark:bg-yellow-900/40">
{`curl -X POST \\
  -H 'Content-Type: application/json' \\
  -d '{"jsonrpc":"2.0","id":1,"method":"miner.getStatus","params":[]}' \\
  ${LOCAL_RPC}`}
            </pre>
          </details>
        </div>
      )}

      {/* Miner status */}
      {data?.status && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {data.status.active !== undefined && (
            <StatCard label="Miner Active" value={data.status.active ? 'Yes' : 'No'} />
          )}
          {data.status.hashrate !== undefined && (
            <StatCard label="Hashrate" value={`${data.status.hashrate} H/s`} />
          )}
          {data.status.staleTemplates !== undefined && (
            <StatCard label="Stale Templates" value={String(data.status.staleTemplates)} warn={Number(data.status.staleTemplates) > 0} />
          )}
          {data.status.acceptedShares !== undefined && (
            <StatCard label="Accepted Shares" value={String(data.status.acceptedShares)} />
          )}
          {data.status.rejectedShares !== undefined && (
            <StatCard label="Rejected Shares" value={String(data.status.rejectedShares)} warn={Number(data.status.rejectedShares) > 0} />
          )}
        </div>
      )}

      {/* Block template */}
      {data?.template && (
        <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
          <h2 className="mb-3 text-lg font-semibold">Current Block Template</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {data.template.height !== undefined && (
              <InfoRow label="Height" value={`#${data.template.height}`} />
            )}
            {data.template.difficulty !== undefined && (
              <InfoRow label="Difficulty" value={String(data.template.difficulty)} mono />
            )}
            {data.template.templateId !== undefined && (
              <InfoRow label="Template ID" value={String(data.template.templateId)} mono />
            )}
            {data.template.ttl !== undefined && (
              <InfoRow label="TTL" value={`${data.template.ttl}s`} />
            )}
          </div>
          <details className="mt-3">
            <summary className="cursor-pointer text-sm text-gray-500 dark:text-slate-400">Raw template</summary>
            <pre className="mt-2 overflow-x-auto rounded bg-gray-50 p-2 text-xs dark:bg-night-800">
              {JSON.stringify(data.template, null, 2)}
            </pre>
          </details>
        </div>
      )}

      {/* Metrics */}
      {data?.metrics && (
        <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
          <h2 className="mb-3 text-lg font-semibold">Mining Metrics</h2>
          <pre className="overflow-x-auto rounded-lg bg-gray-50 p-3 text-xs dark:bg-night-800">
            {JSON.stringify(data.metrics, null, 2)}
          </pre>
        </div>
      )}

      {/* CLI equivalents */}
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h2 className="mb-3 text-lg font-semibold">CLI Equivalents</h2>
        <div className="space-y-3 text-sm">
          <CliExample label="Get miner status" cmd={`curl -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"miner.getStatus","params":[]}' ${LOCAL_RPC}`} />
          <CliExample label="Get block template" cmd={`curl -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"miner.getBlockTemplate","params":[]}' ${LOCAL_RPC}`} />
          <CliExample label="Get work" cmd={`curl -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"miner.getWork","params":[]}' ${LOCAL_RPC}`} />
        </div>
      </div>
    </div>
  )
}

function StatCard({ label, value, warn = false }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className={`rounded-xl border p-4 ${warn ? 'border-yellow-200 bg-yellow-50 dark:border-yellow-800 dark:bg-yellow-900/20' : 'border-day-200 bg-white dark:border-night-800 dark:bg-night-900'}`}>
      <p className="text-xs text-gray-500 dark:text-slate-400">{label}</p>
      <p className={`mt-1 text-xl font-semibold ${warn ? 'text-yellow-800 dark:text-yellow-300' : ''}`}>{value}</p>
    </div>
  )
}

function InfoRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-day-100 pb-2 last:border-0 dark:border-night-800">
      <span className="text-sm text-gray-600 dark:text-slate-400">{label}</span>
      <span className={`text-right text-sm font-medium ${mono ? 'font-mono' : ''}`}>{value}</span>
    </div>
  )
}

function CliExample({ label, cmd }: { label: string; cmd: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-gray-600 dark:text-slate-400">{label}</p>
      <div className="flex items-start gap-2">
        <pre className="flex-1 overflow-x-auto rounded-lg bg-gray-50 p-2 text-xs dark:bg-night-800">{cmd}</pre>
        <button
          onClick={() => { navigator.clipboard?.writeText(cmd); setCopied(true); setTimeout(() => setCopied(false), 2000) }}
          className="rounded border border-day-200 px-2 py-1 text-xs hover:bg-gray-50 dark:border-night-700 dark:hover:bg-night-800"
          aria-label="Copy command"
        >
          {copied ? '✓' : 'Copy'}
        </button>
      </div>
    </div>
  )
}
