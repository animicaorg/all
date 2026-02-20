import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { formatError, bigIntSafeStringify, LOCAL_RPC } from '../lib/rpcUtils'

interface DiscoverData {
  available: boolean
  methods: string[]
  version?: string
  servers?: unknown[]
  raw?: unknown
  note?: string
}

export default function RpcInspectorPage() {
  const [data, setData] = useState<DiscoverData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<{ message: string; hint: string; remediation: string } | null>(null)
  const [filter, setFilter] = useState('')
  const [copiedMethod, setCopiedMethod] = useState<string | null>(null)

  useEffect(() => {
    api.getRpcDiscover()
      .then(setData)
      .catch(err => setError(formatError(err)))
      .finally(() => setLoading(false))
  }, [])

  const filtered = data?.methods.filter(m =>
    !filter || m.toLowerCase().includes(filter.toLowerCase())
  ) ?? []

  const curlFor = (method: string) =>
    `curl -X POST \\\n  -H 'Content-Type: application/json' \\\n  -d '${bigIntSafeStringify({ jsonrpc: '2.0', id: 1, method, params: [] })}' \\\n  ${LOCAL_RPC}`

  const copyMethod = (method: string) => {
    navigator.clipboard?.writeText(curlFor(method))
    setCopiedMethod(method)
    setTimeout(() => setCopiedMethod(null), 2000)
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h1 className="mb-1 text-2xl font-semibold">RPC Inspector</h1>
        <p className="text-sm text-gray-600 dark:text-slate-400">
          Discover available RPC methods on the connected node, with one-click curl examples.
        </p>
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

      {loading && (
        <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
          <p className="text-sm text-gray-500 dark:text-slate-400">Calling rpc.discover...</p>
        </div>
      )}

      {data && !data.available && !loading && (
        <div className="rounded-xl border border-yellow-200 bg-yellow-50 p-4 dark:border-yellow-800 dark:bg-yellow-900/20">
          <p className="font-medium text-yellow-800 dark:text-yellow-300">
            ⚠ rpc.discover not available on this node
          </p>
          {data.note && (
            <p className="mt-1 text-sm text-yellow-700 dark:text-yellow-400">{data.note}</p>
          )}
          <p className="mt-2 text-sm text-yellow-700 dark:text-yellow-400">
            The node did not respond to <code className="rounded bg-yellow-100 px-1 py-0.5 dark:bg-yellow-900/40">rpc.discover</code>,{' '}
            <code className="rounded bg-yellow-100 px-1 py-0.5 dark:bg-yellow-900/40">rpc.listMethods</code>, or{' '}
            <code className="rounded bg-yellow-100 px-1 py-0.5 dark:bg-yellow-900/40">node.ping</code>.
          </p>
        </div>
      )}

      {data?.available && (
        <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="rounded bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800 dark:bg-green-900/20 dark:text-green-300">
                ✓ Connected
              </span>
              {data.version && (
                <span className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-600 dark:bg-night-800 dark:text-slate-400">
                  v{data.version}
                </span>
              )}
              <span className="text-sm text-gray-500 dark:text-slate-400">
                {data.methods.length} method{data.methods.length !== 1 ? 's' : ''}
              </span>
            </div>
            <input
              className="ml-auto rounded-lg border border-day-200 bg-day-50 px-3 py-1.5 text-sm focus:border-animica-500 focus:outline-none dark:border-night-700 dark:bg-night-800"
              placeholder="Filter methods..."
              value={filter}
              onChange={e => setFilter(e.target.value)}
              aria-label="Filter methods"
            />
          </div>

          {filtered.length === 0 && filter && (
            <p className="text-sm text-gray-500 dark:text-slate-400">No methods match "{filter}"</p>
          )}

          <div className="space-y-1">
            {filtered.map(method => (
              <div
                key={method}
                className="flex items-center justify-between rounded-lg border border-day-100 px-3 py-2 hover:bg-gray-50 dark:border-night-800 dark:hover:bg-night-800"
              >
                <span className="font-mono text-sm">{method}</span>
                <button
                  onClick={() => copyMethod(method)}
                  className="rounded border border-day-200 px-2 py-0.5 text-xs hover:bg-white dark:border-night-700 dark:hover:bg-night-900"
                  aria-label={`Copy curl example for ${method}`}
                >
                  {copiedMethod === method ? '✓ Copied' : 'Copy curl'}
                </button>
              </div>
            ))}
          </div>

          {data.servers && data.servers.length > 0 && (
            <details className="mt-4">
              <summary className="cursor-pointer text-sm text-gray-500 dark:text-slate-400">
                Server info ({data.servers.length})
              </summary>
              <pre className="mt-2 overflow-x-auto rounded-lg bg-gray-50 p-3 text-xs dark:bg-night-800">
                {JSON.stringify(data.servers, null, 2)}
              </pre>
            </details>
          )}

          {data.raw !== undefined && data.raw !== null && (
            <details className="mt-3">
              <summary className="cursor-pointer text-sm text-gray-500 dark:text-slate-400">
                Raw rpc.discover response
              </summary>
              <pre className="mt-2 overflow-x-auto rounded-lg bg-gray-50 p-3 text-xs dark:bg-night-800">
                {JSON.stringify(data.raw, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  )
}
