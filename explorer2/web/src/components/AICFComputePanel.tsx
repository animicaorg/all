import { useEffect, useState } from 'react'
import { api } from '../lib/api'

type WorkersResponse = Awaited<ReturnType<typeof api.getAICFWorkers>>
type StatsResponse = Awaited<ReturnType<typeof api.getAICFJobStats>>

/**
 * AICFComputePanel
 *
 * Reads the network-wide AICF compute state from the node:
 *   /api/aicf/workers      — registered workers + advertised tiers
 *   /api/aicf/job-stats    — rolling-window job stats (jobs/hr, latencies)
 *
 * Auto-refreshes every 15 seconds while the page is visible. Falls back to
 * a graceful "not available on this node" message when the underlying RPC
 * methods aren't exposed yet — this is production-friendly behavior for
 * staged rollouts where some nodes have AICF surfaced and others don't.
 */
export default function AICFComputePanel() {
  const [workers, setWorkers] = useState<WorkersResponse | null>(null)
  const [stats, setStats] = useState<StatsResponse | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    const load = () => {
      api.getAICFWorkers().then((r) => mounted && setWorkers(r)).catch((e) => mounted && setErr(String(e)))
      api.getAICFJobStats(60).then((r) => mounted && setStats(r)).catch(() => { /* non-fatal */ })
    }
    load()
    const id = setInterval(() => {
      if (document.visibilityState === 'visible') load()
    }, 15_000)
    return () => { mounted = false; clearInterval(id) }
  }, [])

  if (err) {
    return (
      <section className="rounded-xl border border-red-200 bg-red-50 p-4 dark:border-red-800 dark:bg-red-900/20">
        <p className="text-sm text-red-800 dark:text-red-300">Failed to load AICF compute state: {err}</p>
      </section>
    )
  }

  const anyAvail = (workers?.available ?? false) || (stats?.available ?? false)
  if (!anyAvail) {
    return (
      <section className="rounded-xl border border-day-200 bg-white p-4 dark:border-night-800 dark:bg-night-900">
        <h2 className="text-sm font-semibold text-gray-700 dark:text-slate-300">Network compute</h2>
        <p className="mt-1 text-xs text-gray-500 dark:text-slate-400">
          This node hasn't surfaced AICF worker/job stats yet
          {workers?.reason || stats?.reason ? ` (${workers?.reason ?? stats?.reason})` : ''}.
          Worker stats appear once the node exposes <code className="font-mono">aicf.workerList</code> and
          <code className="font-mono"> aicf.jobStats</code>.
        </p>
      </section>
    )
  }

  const tierEntries = Object.entries(workers?.by_tier ?? {}).sort((a, b) => b[1] - a[1])
  const jobTierEntries = Object.entries(stats?.by_tier ?? {}).sort((a, b) => b[1] - a[1])

  return (
    <section className="rounded-xl border border-day-200 bg-white p-6 dark:border-night-800 dark:bg-night-900 space-y-6">
      <header className="flex items-baseline justify-between">
        <h2 className="text-lg font-semibold">Network compute</h2>
        <span className="text-xs text-gray-500 dark:text-slate-400">
          window: last {stats?.window_minutes ?? 60} min · refresh: 15s
        </span>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Active workers" value={workers?.total ?? 0} />
        <Stat label="Jobs completed" value={stats?.jobs_completed ?? '—'} />
        <Stat label="Jobs failed" value={stats?.jobs_failed ?? '—'} />
        <Stat
          label="Latency p50 / p95"
          value={
            stats?.latency_p50_ms != null
              ? `${stats.latency_p50_ms}ms / ${stats?.latency_p95_ms ?? '—'}ms`
              : '—'
          }
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Block title="Workers by tier">
          {tierEntries.length === 0 ? (
            <p className="text-sm text-gray-500">no workers advertising yet</p>
          ) : (
            <ul className="space-y-1">
              {tierEntries.map(([tier, n]) => (
                <li key={tier} className="flex justify-between text-sm font-mono">
                  <span className="text-amber-600 dark:text-amber-400">{tier}</span>
                  <span>{n}</span>
                </li>
              ))}
            </ul>
          )}
        </Block>
        <Block title="Jobs served by tier (window)">
          {jobTierEntries.length === 0 ? (
            <p className="text-sm text-gray-500">no jobs in window</p>
          ) : (
            <ul className="space-y-1">
              {jobTierEntries.map(([tier, n]) => (
                <li key={tier} className="flex justify-between text-sm font-mono">
                  <span className="text-amber-600 dark:text-amber-400">{tier}</span>
                  <span>{n.toLocaleString()} jobs</span>
                </li>
              ))}
            </ul>
          )}
        </Block>
      </div>

      {workers && workers.workers && workers.workers.length > 0 && (
        <details className="rounded border border-day-200 dark:border-night-800">
          <summary className="cursor-pointer p-3 text-sm font-medium">
            Workers ({workers.workers.length})
          </summary>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b border-day-200 dark:border-night-800">
                <th className="p-2 font-medium">Address</th>
                <th className="p-2 font-medium">Tiers</th>
                <th className="p-2 font-medium">Accel</th>
                <th className="p-2 font-medium">RAM</th>
                <th className="p-2 font-medium">Jobs</th>
              </tr>
            </thead>
            <tbody>
              {workers.workers.slice(0, 50).map((w) => (
                <tr key={w.address} className="border-b border-day-100 dark:border-night-900">
                  <td className="p-2 font-mono text-xs">{shorten(w.address)}</td>
                  <td className="p-2 text-xs">{(w.tiers ?? []).join(', ')}</td>
                  <td className="p-2 text-xs">{w.hardware?.accelerator_preferred ?? '—'}</td>
                  <td className="p-2 text-xs">
                    {w.hardware?.ram_gb != null ? `${w.hardware.ram_gb.toFixed(1)} GB` : '—'}
                  </td>
                  <td className="p-2 text-xs">{w.jobs_completed ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </section>
  )
}

function shorten(s: string | undefined | null, n = 10) {
  if (!s) return '—'
  return s.length > n * 2 + 3 ? `${s.slice(0, n)}…${s.slice(-4)}` : s
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-day-200 p-3 dark:border-night-800">
      <div className="text-[10px] uppercase tracking-wide text-gray-500 dark:text-slate-400">
        {label}
      </div>
      <div className="mt-1 text-xl font-semibold font-mono">{value}</div>
    </div>
  )
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-day-200 p-4 dark:border-night-800">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-slate-400 mb-3">
        {title}
      </h3>
      {children}
    </div>
  )
}
