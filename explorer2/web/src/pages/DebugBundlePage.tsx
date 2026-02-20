import { useState } from 'react'
import { api } from '../lib/api'
import { bigIntSafeStringify, LOCAL_RPC } from '../lib/rpcUtils'

export default function DebugBundlePage() {
  const [loading, setLoading] = useState(false)
  const [bundle, setBundle] = useState<unknown>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const fetchBundle = async () => {
    setLoading(true)
    setError(null)
    setBundle(null)
    try {
      const data = await api.getDebugBundle()
      setBundle(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  const bundleText = bundle ? bigIntSafeStringify(bundle, 2) : ''

  const downloadBundle = () => {
    const blob = new Blob([bundleText], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `animica-debug-${new Date().toISOString().replace(/[:.]/g, '-')}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const copyBundle = () => {
    navigator.clipboard?.writeText(bundleText)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h1 className="mb-1 text-2xl font-semibold">Debug Bundle</h1>
        <p className="text-sm text-gray-600 dark:text-slate-400">
          Export a sanitized snapshot of explorer config, RPC discover, service status, and current head.
          Useful for support and debugging — no private keys or secrets are included.
        </p>
      </div>

      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h2 className="mb-3 text-lg font-semibold">What's included</h2>
        <ul className="list-inside list-disc space-y-1 text-sm text-gray-600 dark:text-slate-400">
          <li>Explorer version and current profile (mode, chain ID, RPC URL — credentials redacted)</li>
          <li>rpc.discover snapshot (available methods, version)</li>
          <li>Service status (chain, mempool, AICF, DA, miner, quantum)</li>
          <li>Current chain head</li>
          <li>Export timestamp</li>
        </ul>
        <p className="mt-3 text-sm text-yellow-700 dark:text-yellow-400">
          ⚠ Review the bundle before sharing. RPC URLs may contain hostnames that identify your infrastructure.
        </p>
      </div>

      <div className="flex flex-wrap gap-3">
        <button
          onClick={fetchBundle}
          disabled={loading}
          className="rounded-lg bg-animica-600 px-4 py-2 text-sm font-medium text-white hover:bg-animica-700 disabled:opacity-50 dark:bg-animica-500 dark:hover:bg-animica-600"
        >
          {loading ? 'Collecting...' : '↻ Collect Bundle'}
        </button>

        {bundle !== null && (
          <>
            <button
              onClick={downloadBundle}
              className="rounded-lg border border-animica-600 px-4 py-2 text-sm font-medium text-animica-600 hover:bg-animica-50 dark:border-animica-400 dark:text-animica-400 dark:hover:bg-animica-900/20"
            >
              ↓ Download JSON
            </button>
            <button
              onClick={copyBundle}
              className="rounded-lg border border-day-200 px-4 py-2 text-sm font-medium hover:bg-gray-50 dark:border-night-700 dark:hover:bg-night-800"
            >
              {copied ? '✓ Copied' : 'Copy to Clipboard'}
            </button>
          </>
        )}
      </div>

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 dark:border-red-800 dark:bg-red-900/20">
          <p className="font-medium text-red-800 dark:text-red-300">Failed to collect debug bundle</p>
          <p className="mt-1 text-sm text-red-700 dark:text-red-400">{error}</p>
        </div>
      )}

      {bundle !== null && (
        <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
          <h2 className="mb-3 text-lg font-semibold">Bundle Preview</h2>
          <pre className="max-h-96 overflow-x-auto rounded-lg bg-gray-50 p-4 text-xs dark:bg-night-800">
            {bundleText}
          </pre>
        </div>
      )}

      {/* Operator checklist */}
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h2 className="mb-3 text-lg font-semibold">Operator Checklist</h2>
        <p className="mb-3 text-sm text-gray-600 dark:text-slate-400">
          Manual checks to verify node health:
        </p>
        <ul className="space-y-2 text-sm">
          {[
            { label: 'Peers connected', cmd: `curl -s -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"p2p.getPeers","params":[]}' ${LOCAL_RPC} | python3 -m json.tool` },
            { label: 'Sync status (head matches peers)', cmd: `curl -s -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' ${LOCAL_RPC} | python3 -m json.tool` },
            { label: 'Mempool writable (submit test tx via CLI)', cmd: 'python -m omni_sdk.cli.tx --help' },
            { label: 'AICF credits moving', cmd: `curl -s -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"aicf.getStatus","params":[]}' ${LOCAL_RPC} | python3 -m json.tool` },
            { label: 'DA put/get working', cmd: `curl -s -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"da.getStatus","params":[]}' ${LOCAL_RPC} | python3 -m json.tool` },
            { label: 'Mining template TTL valid', cmd: `curl -s -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"miner.getBlockTemplate","params":[]}' ${LOCAL_RPC} | python3 -m json.tool` },
          ].map(({ label, cmd }) => (
            <ChecklistItem key={label} label={label} cmd={cmd} />
          ))}
        </ul>
      </div>
    </div>
  )
}

function ChecklistItem({ label, cmd }: { label: string; cmd: string }) {
  const [checked, setChecked] = useState(false)
  const [copied, setCopied] = useState(false)

  return (
    <li className={`flex items-start gap-3 rounded-lg border p-3 ${checked ? 'border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-900/10' : 'border-day-100 dark:border-night-800'}`}>
      <input
        type="checkbox"
        checked={checked}
        onChange={e => setChecked(e.target.checked)}
        className="mt-0.5 h-4 w-4 rounded"
        aria-label={`Mark ${label} as done`}
      />
      <div className="flex-1 min-w-0">
        <p className={`font-medium ${checked ? 'line-through text-gray-400 dark:text-slate-500' : ''}`}>{label}</p>
        <div className="mt-1 flex items-start gap-2">
          <pre className="flex-1 overflow-x-auto text-xs text-gray-500 dark:text-slate-400">{cmd}</pre>
          <button
            onClick={() => { navigator.clipboard?.writeText(cmd); setCopied(true); setTimeout(() => setCopied(false), 2000) }}
            className="shrink-0 rounded border border-day-200 px-2 py-0.5 text-xs hover:bg-gray-50 dark:border-night-700 dark:hover:bg-night-800"
          >
            {copied ? '✓' : 'Copy'}
          </button>
        </div>
      </div>
    </li>
  )
}
