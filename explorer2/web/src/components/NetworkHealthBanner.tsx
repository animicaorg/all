import { useEffect, useState } from 'react'
import { api } from '../lib/api'

interface ServiceEntry {
  name: string
  status: 'ok' | 'degraded' | 'down' | 'unknown'
  hint?: string
  remediation?: string
}

const STATUS_COLOR: Record<string, string> = {
  ok: 'bg-green-50 border-green-200 text-green-800 dark:bg-green-900/20 dark:border-green-800 dark:text-green-300',
  degraded: 'bg-yellow-50 border-yellow-200 text-yellow-800 dark:bg-yellow-900/20 dark:border-yellow-800 dark:text-yellow-300',
  down: 'bg-red-50 border-red-200 text-red-800 dark:bg-red-900/20 dark:border-red-800 dark:text-red-300',
  unknown: 'bg-gray-50 border-gray-200 text-gray-700 dark:bg-night-800 dark:border-night-700 dark:text-slate-300',
}

const STATUS_ICON: Record<string, string> = {
  ok: '✓',
  degraded: '⚠',
  down: '✗',
  unknown: '?',
}

export default function NetworkHealthBanner() {
  const [services, setServices] = useState<ServiceEntry[]>([])
  const [expanded, setExpanded] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.getNetworkStatus()
      .then(data => setServices(data.services as ServiceEntry[]))
      .catch(() => { /* banner fails silently */ })
      .finally(() => setLoading(false))
  }, [])

  if (loading || services.length === 0) return null

  const hasIssues = services.some(s => s.status === 'down' || s.status === 'degraded')
  const allOk = services.every(s => s.status === 'ok')

  if (allOk) return null // Don't show banner when everything is healthy

  const worstStatus = services.some(s => s.status === 'down')
    ? 'down'
    : services.some(s => s.status === 'degraded')
    ? 'degraded'
    : 'unknown'

  return (
    <div className={`border-b ${STATUS_COLOR[worstStatus]} px-4 py-2`}>
      <div className="mx-auto max-w-7xl">
        <button
          className="flex w-full items-center justify-between gap-2 text-sm font-medium"
          onClick={() => setExpanded(e => !e)}
          aria-expanded={expanded}
        >
          <span>
            {STATUS_ICON[worstStatus]}{' '}
            {hasIssues
              ? 'Some services are unavailable or degraded'
              : 'Service status unknown for some components'}
          </span>
          <span className="text-xs opacity-70">{expanded ? 'Hide details ▲' : 'Show details ▼'}</span>
        </button>

        {expanded && (
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            {services.map(svc => (
              <div key={svc.name} className="rounded border bg-white/50 p-2 dark:bg-black/20">
                <div className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide">
                  <span>{STATUS_ICON[svc.status]}</span>
                  <span>{svc.name}</span>
                </div>
                {svc.hint && (
                  <p className="mt-1 text-xs opacity-80">{svc.hint}</p>
                )}
                {svc.remediation && (
                  <p className="mt-1 text-xs italic opacity-70">{svc.remediation}</p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
