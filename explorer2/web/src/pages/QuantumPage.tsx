import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { formatError, LOCAL_RPC } from '../lib/rpcUtils'

interface QuantumData {
  available: boolean
  status?: Record<string, unknown>
  workers?: unknown[] | Record<string, unknown>
  jobs?: Record<string, unknown>
  policy?: Record<string, unknown>
}

export default function QuantumPage() {
  const [data, setData] = useState<QuantumData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<{ message: string; hint: string; remediation: string } | null>(null)

  useEffect(() => {
    api.getQuantumInfo()
      .then(d => setData(d as QuantumData))
      .catch(err => setError(formatError(err)))
      .finally(() => setLoading(false))
  }, [])

  const workerList = data?.workers
    ? Array.isArray(data.workers)
      ? data.workers as Record<string, unknown>[]
      : Array.isArray((data.workers as Record<string, unknown>).items)
      ? (data.workers as Record<string, unknown[]>).items as Record<string, unknown>[]
      : []
    : []

  const jobList = data?.jobs
    ? Array.isArray((data.jobs as Record<string, unknown>).items)
      ? (data.jobs as Record<string, unknown[]>).items as Record<string, unknown>[]
      : Array.isArray(data.jobs)
      ? data.jobs as Record<string, unknown>[]
      : []
    : []

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h1 className="mb-1 text-2xl font-semibold">Quantum Workers</h1>
        <p className="text-sm text-gray-600 dark:text-slate-400">
          Worker registration, jobs, contribution attribution, and credits.
        </p>
        <div className="mt-2 rounded border border-yellow-200 bg-yellow-50 px-3 py-2 text-sm text-yellow-800 dark:border-yellow-800 dark:bg-yellow-900/20 dark:text-yellow-300">
          ⚠ Private keys are never stored in Explorer. Use your CLI or wallet for signing operations.
        </div>
      </div>

      {loading && (
        <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
          <p className="text-sm text-gray-500 dark:text-slate-400">Loading quantum info...</p>
        </div>
      )}

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
            ⚠ Quantum RPC methods are not available on this node
          </p>
          <p className="mt-1 text-sm text-yellow-700 dark:text-yellow-400">
            The connected node does not expose quantum.* RPC methods.
          </p>
          <details className="mt-3">
            <summary className="cursor-pointer text-sm text-yellow-600 dark:text-yellow-500">
              Copy curl example
            </summary>
            <pre className="mt-2 overflow-x-auto rounded bg-yellow-100 p-2 text-xs dark:bg-yellow-900/40">
{`curl -X POST \\
  -H 'Content-Type: application/json' \\
  -d '{"jsonrpc":"2.0","id":1,"method":"quantum.getStatus","params":[]}' \\
  ${LOCAL_RPC}`}
            </pre>
          </details>
        </div>
      )}

      {/* Policy */}
      {data?.policy && (
        <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
          <h2 className="mb-3 text-lg font-semibold">Policy</h2>
          <pre className="overflow-x-auto rounded-lg bg-gray-50 p-3 text-xs dark:bg-night-800">
            {JSON.stringify(data.policy, null, 2)}
          </pre>
        </div>
      )}

      {/* Workers */}
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h2 className="mb-3 text-lg font-semibold">Registered Workers</h2>
        {workerList.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-slate-400">
            {data?.available === false ? 'Not available on this node.' : 'No workers registered.'}
          </p>
        ) : (
          <div className="space-y-2">
            {workerList.map((w, i) => (
              <div key={i} className="rounded-lg border border-day-100 p-3 dark:border-night-800">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-sm">{String(w.id ?? `Worker ${i + 1}`)}</span>
                  {w.status !== undefined && w.status !== null && (
                    <span className={`rounded px-2 py-0.5 text-xs font-medium ${
                      w.status === 'active' ? 'bg-green-100 text-green-800 dark:bg-green-900/20 dark:text-green-300' : 'bg-gray-100 text-gray-600 dark:bg-night-800 dark:text-slate-400'
                    }`}>
                      {String(w.status)}
                    </span>
                  )}
                </div>
                {w.capabilities !== undefined && w.capabilities !== null && (
                  <p className="mt-1 text-xs text-gray-500 dark:text-slate-400">
                    Capabilities: {String(w.capabilities)}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Jobs */}
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h2 className="mb-3 text-lg font-semibold">Recent Jobs</h2>
        {jobList.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-slate-400">
            {data?.available === false ? 'Not available on this node.' : 'No jobs found.'}
          </p>
        ) : (
          <div className="space-y-2">
            {jobList.map((job, i) => (
              <div key={i} className="rounded-lg border border-day-100 p-3 text-sm dark:border-night-800">
                <pre className="overflow-x-auto text-xs">{JSON.stringify(job, null, 2)}</pre>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* CLI equivalents */}
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h2 className="mb-3 text-lg font-semibold">CLI Equivalents</h2>
        <p className="mb-3 text-sm text-gray-600 dark:text-slate-400">
          Signing operations (worker registration, contributions) must be done via CLI with your private key.
        </p>
        <div className="space-y-3 text-sm">
          <CliExample label="List quantum workers" cmd={`curl -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"quantum.listWorkers","params":[]}' ${LOCAL_RPC}`} />
          <CliExample label="List quantum jobs" cmd={`curl -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"quantum.listJobs","params":[{"limit":20}]}' ${LOCAL_RPC}`} />
          <CliExample label="Get quantum policy" cmd={`curl -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"quantum.getPolicy","params":[]}' ${LOCAL_RPC}`} />
        </div>
      </div>
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
