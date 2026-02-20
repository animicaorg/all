import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { formatError, LOCAL_RPC } from '../lib/rpcUtils'

interface DAData {
  available: boolean
  status?: Record<string, unknown>
  quotas?: Record<string, unknown>
}

interface DAHistoryEntry {
  commitment?: string
  size?: number
  timestamp?: number | string
  namespace?: string
}

export default function DAPage() {
  const [info, setInfo] = useState<DAData | null>(null)
  const [history, setHistory] = useState<DAHistoryEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<{ message: string; hint: string; remediation: string } | null>(null)

  // Blob get/put state
  const [getCommitment, setGetCommitment] = useState('')
  const [blobResult, setBlobResult] = useState<unknown>(null)
  const [blobError, setBlobError] = useState<string | null>(null)
  const [proofResult, setProofResult] = useState<unknown>(null)
  const [proofError, setProofError] = useState<string | null>(null)

  // Put state
  const [putNamespace, setPutNamespace] = useState('')
  const [putData, setPutData] = useState('')
  const [putResult, setPutResult] = useState<unknown>(null)
  const [putError, setPutError] = useState<string | null>(null)
  const [putting, setPutting] = useState(false)

  useEffect(() => {
    Promise.all([
      api.getDAInfo().then(d => setInfo(d as DAData)).catch(err => setError(formatError(err))),
      api.getDAHistory(20)
        .then(items => setHistory(items as DAHistoryEntry[]))
        .catch(() => {}),
    ]).finally(() => setLoading(false))
  }, [])

  const handleGetBlob = async (e: React.FormEvent) => {
    e.preventDefault()
    setBlobResult(null)
    setBlobError(null)
    try {
      const result = await api.getDABlob(getCommitment)
      setBlobResult(result)
    } catch (err) {
      setBlobError(err instanceof Error ? err.message : String(err))
    }
  }

  const handleGetProof = async () => {
    setProofResult(null)
    setProofError(null)
    try {
      const result = await api.getDAProof(getCommitment)
      setProofResult(result)
    } catch (err) {
      setProofError(err instanceof Error ? err.message : String(err))
    }
  }

  const handlePut = async (e: React.FormEvent) => {
    e.preventDefault()
    setPutResult(null)
    setPutError(null)
    setPutting(true)
    try {
      const result = await api.putDABlob(putNamespace, btoa(putData))
      setPutResult(result)
    } catch (err) {
      setPutError(err instanceof Error ? err.message : String(err))
    } finally {
      setPutting(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h1 className="mb-1 text-2xl font-semibold">Data Availability (DA)</h1>
        <p className="text-sm text-gray-600 dark:text-slate-400">
          Put and get blobs, view proofs, and browse history.
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

      {!loading && info && !info.available && !error && (
        <div className="rounded-xl border border-yellow-200 bg-yellow-50 p-4 dark:border-yellow-800 dark:bg-yellow-900/20">
          <p className="font-medium text-yellow-800 dark:text-yellow-300">
            ⚠ DA is not available on this node
          </p>
          <p className="mt-1 text-sm text-yellow-700 dark:text-yellow-400">
            The connected node does not expose da.* RPC methods.
          </p>
          <details className="mt-3">
            <summary className="cursor-pointer text-sm text-yellow-600 dark:text-yellow-500">
              Copy curl example
            </summary>
            <pre className="mt-2 overflow-x-auto rounded bg-yellow-100 p-2 text-xs dark:bg-yellow-900/40">
{`curl -X POST \\
  -H 'Content-Type: application/json' \\
  -d '{"jsonrpc":"2.0","id":1,"method":"da.getStatus","params":[]}' \\
  ${LOCAL_RPC}`}
            </pre>
          </details>
        </div>
      )}

      {loading && (
        <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
          <p className="text-sm text-gray-500 dark:text-slate-400">Loading DA info...</p>
        </div>
      )}

      {/* Status + Quotas */}
      {info?.status && (
        <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
          <h2 className="mb-3 text-lg font-semibold">DA Status</h2>
          <pre className="overflow-x-auto rounded-lg bg-gray-50 p-3 text-xs dark:bg-night-800">
            {JSON.stringify(info.status, null, 2)}
          </pre>
          {info.quotas && (
            <>
              <h3 className="mb-2 mt-4 text-sm font-semibold">Quotas</h3>
              <pre className="overflow-x-auto rounded-lg bg-gray-50 p-3 text-xs dark:bg-night-800">
                {JSON.stringify(info.quotas, null, 2)}
              </pre>
            </>
          )}
        </div>
      )}

      {/* Get Blob + Proof */}
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h2 className="mb-3 text-lg font-semibold">Get Blob / Proof</h2>
        <form className="flex gap-2" onSubmit={handleGetBlob}>
          <input
            className="flex-1 rounded-lg border border-day-200 bg-day-50 px-3 py-2 text-sm font-mono focus:border-animica-500 focus:outline-none dark:border-night-700 dark:bg-night-800"
            placeholder="0x... commitment hash"
            value={getCommitment}
            onChange={e => setGetCommitment(e.target.value)}
            aria-label="Commitment hash"
          />
          <button
            type="submit"
            className="rounded-lg bg-animica-600 px-4 py-2 text-sm font-medium text-white hover:bg-animica-700 dark:bg-animica-500 dark:hover:bg-animica-600"
          >
            Get Blob
          </button>
          <button
            type="button"
            onClick={handleGetProof}
            className="rounded-lg border border-animica-600 px-4 py-2 text-sm font-medium text-animica-600 hover:bg-animica-50 dark:border-animica-400 dark:text-animica-400 dark:hover:bg-animica-900/20"
          >
            Get Proof
          </button>
        </form>

        {blobError && <p className="mt-2 text-sm text-red-600 dark:text-red-400">{blobError}</p>}
        {blobResult !== null && (
          <div className="mt-3">
            <h3 className="mb-1 text-sm font-semibold">Blob</h3>
            <pre className="overflow-x-auto rounded-lg bg-gray-50 p-3 text-xs dark:bg-night-800">
              {JSON.stringify(blobResult, null, 2)}
            </pre>
          </div>
        )}
        {proofError && <p className="mt-2 text-sm text-red-600 dark:text-red-400">{proofError}</p>}
        {proofResult !== null && (
          <div className="mt-3">
            <h3 className="mb-1 text-sm font-semibold">Proof</h3>
            <pre className="overflow-x-auto rounded-lg bg-gray-50 p-3 text-xs dark:bg-night-800">
              {JSON.stringify(proofResult, null, 2)}
            </pre>
          </div>
        )}
      </div>

      {/* Put Blob */}
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h2 className="mb-3 text-lg font-semibold">Put Blob</h2>
        <form className="space-y-3" onSubmit={handlePut}>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-slate-300">
              Namespace
            </label>
            <input
              className="w-full rounded-lg border border-day-200 bg-day-50 px-3 py-2 text-sm focus:border-animica-500 focus:outline-none dark:border-night-700 dark:bg-night-800"
              placeholder="e.g. my-namespace"
              value={putNamespace}
              onChange={e => setPutNamespace(e.target.value)}
              required
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-slate-300">
              Data (UTF-8 text, will be base64-encoded)
            </label>
            <textarea
              className="w-full rounded-lg border border-day-200 bg-day-50 px-3 py-2 text-sm focus:border-animica-500 focus:outline-none dark:border-night-700 dark:bg-night-800"
              rows={4}
              placeholder="Paste data here..."
              value={putData}
              onChange={e => setPutData(e.target.value)}
              required
            />
          </div>
          <button
            type="submit"
            disabled={putting}
            className="rounded-lg bg-animica-600 px-4 py-2 text-sm font-medium text-white hover:bg-animica-700 disabled:opacity-50 dark:bg-animica-500 dark:hover:bg-animica-600"
          >
            {putting ? 'Putting...' : 'Put Blob'}
          </button>
        </form>
        {putError && <p className="mt-2 text-sm text-red-600 dark:text-red-400">{putError}</p>}
        {putResult !== null && (
          <div className="mt-3">
            <h3 className="mb-1 text-sm font-semibold text-green-700 dark:text-green-300">✓ Blob submitted</h3>
            <pre className="overflow-x-auto rounded-lg bg-gray-50 p-3 text-xs dark:bg-night-800">
              {JSON.stringify(putResult, null, 2)}
            </pre>
          </div>
        )}
      </div>

      {/* History */}
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h2 className="mb-3 text-lg font-semibold">Blob History</h2>
        {history.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-slate-400">No history available.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-day-200 text-left text-xs font-medium uppercase text-gray-500 dark:border-night-700 dark:text-slate-400">
                  <th className="pb-2 pr-4">Commitment</th>
                  <th className="pb-2 pr-4">Namespace</th>
                  <th className="pb-2 pr-4">Size</th>
                  <th className="pb-2">Timestamp</th>
                </tr>
              </thead>
              <tbody>
                {history.map((entry, i) => (
                  <tr key={i} className="border-b border-day-100 dark:border-night-800">
                    <td className="py-2 pr-4 font-mono text-xs">{entry.commitment ?? '—'}</td>
                    <td className="py-2 pr-4">{entry.namespace ?? '—'}</td>
                    <td className="py-2 pr-4">{entry.size !== undefined ? `${entry.size} B` : '—'}</td>
                    <td className="py-2 text-xs text-gray-500 dark:text-slate-400">
                      {entry.timestamp
                        ? new Date(typeof entry.timestamp === 'number' ? entry.timestamp * 1000 : entry.timestamp).toLocaleString()
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
