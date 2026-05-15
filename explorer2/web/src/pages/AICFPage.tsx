import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { formatError, LOCAL_RPC } from '../lib/rpcUtils'
import AICFComputePanel from '../components/AICFComputePanel'

interface AICFData {
  available: boolean
  status?: Record<string, unknown>
  credits?: Record<string, unknown>
  jobs?: Record<string, unknown>
  plans?: unknown[]
}

export default function AICFPage() {
  const [data, setData] = useState<AICFData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<{ message: string; hint: string; remediation: string } | null>(null)
  const [address, setAddress] = useState('')
  const [searchAddress, setSearchAddress] = useState<string | undefined>(undefined)

  useEffect(() => {
    setLoading(true)
    setError(null)
    api.getAICFInfo(searchAddress)
      .then(d => setData(d as AICFData))
      .catch(err => setError(formatError(err)))
      .finally(() => setLoading(false))
  }, [searchAddress])

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h1 className="mb-1 text-2xl font-semibold">AICF — AI Compute Flywheel</h1>
        <p className="text-sm text-gray-600 dark:text-slate-400">
          View AICF credits, jobs, plans, and claim status, plus live network
          compute capacity (workers per tier, jobs/hr, latency).
        </p>
      </div>

      <AICFComputePanel />

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
            ⚠ AICF is not available on this node
          </p>
          <p className="mt-1 text-sm text-yellow-700 dark:text-yellow-400">
            The connected node does not expose AICF RPC methods.
            Use the <a href="/rpc-inspector" className="underline">RPC Inspector</a> to see what's available.
          </p>
          <details className="mt-3">
            <summary className="cursor-pointer text-sm text-yellow-600 dark:text-yellow-500">
              Copy curl example to check manually
            </summary>
            <pre className="mt-2 overflow-x-auto rounded bg-yellow-100 p-2 text-xs dark:bg-yellow-900/40">
{`curl -X POST \\
  -H 'Content-Type: application/json' \\
  -d '{"jsonrpc":"2.0","id":1,"method":"aicf.getStatus","params":[]}' \\
  ${LOCAL_RPC}`}
            </pre>
          </details>
        </div>
      )}

      {/* Credits lookup */}
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h2 className="mb-3 text-lg font-semibold">Credits Lookup</h2>
        <form
          className="flex gap-2"
          onSubmit={e => {
            e.preventDefault()
            setSearchAddress(address.trim() || undefined)
          }}
        >
          <input
            className="flex-1 rounded-lg border border-day-200 bg-day-50 px-3 py-2 text-sm focus:border-animica-500 focus:outline-none dark:border-night-700 dark:bg-night-800 dark:focus:border-animica-400"
            placeholder="Enter anim1... address"
            value={address}
            onChange={e => setAddress(e.target.value)}
            aria-label="Address for credits lookup"
          />
          <button
            type="submit"
            className="rounded-lg bg-animica-600 px-4 py-2 text-sm font-medium text-white hover:bg-animica-700 dark:bg-animica-500 dark:hover:bg-animica-600"
          >
            Look up
          </button>
        </form>

        {loading && <p className="mt-3 text-sm text-gray-500 dark:text-slate-400">Loading...</p>}

        {data?.credits && (
          <div className="mt-4 space-y-2">
            <h3 className="text-sm font-semibold text-gray-700 dark:text-slate-300">Credits</h3>
            <pre className="overflow-x-auto rounded-lg bg-gray-50 p-3 text-xs dark:bg-night-800">
              {JSON.stringify(data.credits, null, 2)}
            </pre>
          </div>
        )}
      </div>

      {/* Global status */}
      {data?.status && (
        <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
          <h2 className="mb-3 text-lg font-semibold">Global AICF Status</h2>
          <pre className="overflow-x-auto rounded-lg bg-gray-50 p-3 text-xs dark:bg-night-800">
            {JSON.stringify(data.status, null, 2)}
          </pre>
        </div>
      )}

      {/* Plans */}
      {data?.plans && Array.isArray(data.plans) && data.plans.length > 0 && (
        <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
          <h2 className="mb-3 text-lg font-semibold">Available Plans</h2>
          <div className="space-y-2">
            {(data.plans as Record<string, unknown>[]).map((plan, i) => (
              <div key={i} className="flex items-center justify-between rounded-lg border border-day-100 p-3 dark:border-night-800">
                <span className="font-mono text-sm">{String(plan.id ?? `Plan ${i + 1}`)}</span>
                {plan.cost !== undefined && (
                  <span className="text-sm text-gray-600 dark:text-slate-400">
                    Cost: {String(plan.cost)}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Jobs */}
      {data?.jobs && (
        <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
          <h2 className="mb-3 text-lg font-semibold">Recent Jobs</h2>
          {(() => {
            const items = Array.isArray((data.jobs as Record<string, unknown>)?.items)
              ? ((data.jobs as Record<string, unknown[]>).items as unknown[])
              : Array.isArray(data.jobs)
              ? (data.jobs as unknown[])
              : []
            if (items.length === 0) {
              return <p className="text-sm text-gray-500 dark:text-slate-400">No jobs found.</p>
            }
            return (
              <div className="space-y-2">
                {items.map((job, i) => (
                  <div key={i} className="rounded-lg border border-day-100 p-3 text-sm dark:border-night-800">
                    <pre className="overflow-x-auto text-xs">{JSON.stringify(job, null, 2)}</pre>
                  </div>
                ))}
              </div>
            )
          })()}
        </div>
      )}

      {/* CLI equivalents */}
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h2 className="mb-3 text-lg font-semibold">CLI Equivalents</h2>
        <div className="space-y-3 text-sm">
          <CliExample label="Get AICF status" cmd="python -m aicf.cli.queue_list" />
          <CliExample label="Check credits" cmd={`curl -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"aicf.getCredits","params":["${searchAddress ?? 'YOUR_ADDRESS'}"]}' ${LOCAL_RPC}`} />
          <CliExample label="Submit a job" cmd="python -m aicf.cli.queue_submit --ai --prompt 'hello' --max-units 100" />
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
