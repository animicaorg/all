import { useEffect, useState } from 'react'

interface DiagnosticsData {
  mode: string
  rpcUrl: string | null
  chainDbPath: string | null
  chainId: number | null
  detectedHead: number | null
  currentHead: {
    height: number
    hash: string
    time: number
  } | null
  database: {
    exists: boolean
    sizeBytes: number | null
    lastModified: string | null
  }
  startupTime: string | null
  currentTime: string
}

export default function DiagnosticsPage() {
  const [data, setData] = useState<DiagnosticsData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/diagnostics')
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then(setData)
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h1 className="mb-4 text-2xl font-semibold">Node Diagnostics</h1>
        <p className="text-gray-600 dark:text-slate-400">Loading...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h1 className="mb-4 text-2xl font-semibold">Node Diagnostics</h1>
        <div className="rounded-lg bg-red-50 p-4 dark:bg-red-900/20">
          <p className="text-sm text-red-600 dark:text-red-400">Error: {error}</p>
        </div>
      </div>
    )
  }

  if (!data) return null

  const formatBytes = (bytes: number | null) => {
    if (bytes === null) return 'N/A'
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
  }

  const formatDate = (isoString: string | null) => {
    if (!isoString) return 'N/A'
    try {
      return new Date(isoString).toLocaleString()
    } catch {
      return isoString
    }
  }

  const isHealthy = data.mode === 'RPC' || (data.mode === 'Local DB' && data.database.exists)

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h1 className="mb-4 text-2xl font-semibold">Node Diagnostics</h1>
        
        <div className={`mb-4 rounded-lg p-4 ${isHealthy ? 'bg-green-50 dark:bg-green-900/20' : 'bg-yellow-50 dark:bg-yellow-900/20'}`}>
          <p className={`text-sm font-medium ${isHealthy ? 'text-green-800 dark:text-green-300' : 'text-yellow-800 dark:text-yellow-300'}`}>
            Status: {isHealthy ? '✓ Connected' : '⚠ Limited functionality'}
          </p>
        </div>

        <div className="space-y-4">
          <Section title="Connection">
            <Item label="Mode" value={data.mode} />
            {data.mode === 'RPC' && (
              <Item label="RPC URL" value={data.rpcUrl || 'N/A'} />
            )}
            {data.mode === 'Local DB' && (
              <Item label="Database Path" value={data.chainDbPath || 'N/A'} />
            )}
            <Item label="Chain ID" value={data.chainId?.toString() || 'N/A'} />
          </Section>

          <Section title="Chain State">
            <Item label="Detected Head (at startup)" value={data.detectedHead !== null ? `#${data.detectedHead}` : 'N/A'} />
            {data.currentHead && (
              <>
                <Item label="Current Head Height" value={`#${data.currentHead.height}`} />
                <Item label="Current Head Hash" value={data.currentHead.hash} mono />
                <Item label="Current Head Time" value={formatDate(new Date(data.currentHead.time * 1000).toISOString())} />
              </>
            )}
          </Section>

          {data.mode === 'Local DB' && (
            <Section title="Database">
              <Item label="Exists" value={data.database.exists ? 'Yes' : 'No'} />
              <Item label="Size" value={formatBytes(data.database.sizeBytes)} />
              <Item label="Last Modified" value={formatDate(data.database.lastModified)} />
            </Section>
          )}

          <Section title="Server">
            <Item label="Started At" value={formatDate(data.startupTime)} />
            <Item label="Current Time" value={formatDate(data.currentTime)} />
          </Section>
        </div>
      </div>

      <div className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900">
        <h2 className="mb-3 text-lg font-semibold">Configuration Guide</h2>
        <div className="space-y-3 text-sm text-gray-600 dark:text-slate-400">
          <p>
            <strong className="text-gray-900 dark:text-slate-100">RPC Mode (Recommended):</strong> Set the <code className="rounded bg-gray-100 px-1 py-0.5 dark:bg-night-800">EXPLORER2_RPC_URL</code> environment variable to your node's RPC endpoint (e.g., <code className="rounded bg-gray-100 px-1 py-0.5 dark:bg-night-800">http://127.0.0.1:8545/rpc</code>).
          </p>
          <p>
            <strong className="text-gray-900 dark:text-slate-100">Local DB Mode (Fallback):</strong> If RPC is unavailable, the explorer will attempt to read from the local database at <code className="rounded bg-gray-100 px-1 py-0.5 dark:bg-night-800">~/.animica/chain-{'{chainId}'}/animica.db</code>.
          </p>
          <p>
            <strong className="text-gray-900 dark:text-slate-100">Default behavior:</strong> The explorer automatically tries to connect to <code className="rounded bg-gray-100 px-1 py-0.5 dark:bg-night-800">http://127.0.0.1:8545/rpc</code> if no RPC URL is configured.
          </p>
        </div>
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-gray-700 dark:text-slate-300">{title}</h3>
      <div className="space-y-2">
        {children}
      </div>
    </div>
  )
}

function Item({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-day-100 pb-2 last:border-0 dark:border-night-800">
      <span className="text-sm text-gray-600 dark:text-slate-400">{label}</span>
      <span className={`text-right text-sm ${mono ? 'font-mono' : 'font-medium'} text-gray-900 dark:text-slate-100`}>
        {value}
      </span>
    </div>
  )
}
