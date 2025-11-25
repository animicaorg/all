import { describe, it, expect } from 'vitest'

// Import the AICF state/selectors module (we'll adapt to different export names/signatures)
import * as AICF from '../../src/state/aicf'

type Provider = { id: string; name?: string }
type Job = {
  id: string
  providerId: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'timeout'
  startedAt: number
  completedAt?: number
  deadlineAt?: number
  units: number
}

type Settlement = Record<string, unknown>

// ---------- Sample dataset (deterministic) ----------
const t0 = Date.UTC(2023, 0, 1, 0, 0, 0) // fixed epoch to avoid flakiness

const providers: Provider[] = [
  { id: 'P1', name: 'Alpha Compute' },
  { id: 'P2', name: 'Beta Labs' },
]

const jobs: Job[] = [
  // P1 completed (mix of on-time and late)
  { id: 'j1', providerId: 'P1', status: 'completed', startedAt: t0 + 0_000,  completedAt: t0 + 20_000,  deadlineAt: t0 + 30_000,  units: 10 },
  { id: 'j2', providerId: 'P1', status: 'completed', startedAt: t0 + 40_000, completedAt: t0 + 70_000,  deadlineAt: t0 + 65_000,  units: 5  },
  { id: 'j3', providerId: 'P1', status: 'completed', startedAt: t0 + 90_000, completedAt: t0 + 140_000, deadlineAt: t0 + 200_000, units: 20 },
  { id: 'j4', providerId: 'P1', status: 'completed', startedAt: t0 + 220_000,completedAt: t0 + 260_000, deadlineAt: t0 + 300_000, units: 15 },
  { id: 'j5', providerId: 'P1', status: 'completed', startedAt: t0 + 300_000,completedAt: t0 + 390_000, deadlineAt: t0 + 380_000, units: 8  },
  // P1 failed
  { id: 'j6', providerId: 'P1', status: 'failed',    startedAt: t0 + 410_000,                                     units: 12 },
  // P2 completed (mix)
  { id: 'k1', providerId: 'P2', status: 'completed', startedAt: t0 + 10_000, completedAt: t0 + 50_000,  deadlineAt: t0 + 60_000,  units: 12 },
  { id: 'k2', providerId: 'P2', status: 'completed', startedAt: t0 + 120_000,completedAt: t0 + 200_000, deadlineAt: t0 + 180_000, units: 30 },
  { id: 'k3', providerId: 'P2', status: 'completed', startedAt: t0 + 240_000,completedAt: t0 + 320_000, deadlineAt: t0 + 360_000, units: 25 },
  // P2 failed
  { id: 'k4', providerId: 'P2', status: 'failed',    startedAt: t0 + 260_000,                                     units: 10 },
  { id: 'k5', providerId: 'P2', status: 'failed',    startedAt: t0 + 360_000,                                     units: 18 },
]

const settlements: Settlement[] = []

// Construct common shapes some selectors might expect
const slice = { providers, jobs, settlements }
const state = { aicf: slice }

// ---------- Local reference implementation (expected values) ----------

function percentile(sorted: number[], p: number) {
  if (sorted.length === 0) return 0
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil(p * sorted.length) - 1))
  return sorted[idx]
}

function computeWindowMs(js: Job[]) {
  const starts = js.map(j => j.startedAt).filter(Boolean)
  const ends = js.map(j => j.completedAt ?? j.startedAt).filter(Boolean)
  if (starts.length === 0 || ends.length === 0) return 60_000 // guard
  const minS = Math.min(...starts)
  const maxE = Math.max(...ends)
  const span = Math.max(60_000, maxE - minS) // at least 1 minute to avoid infinity
  return span
}

function expectedSLA(js: Job[]) {
  const total = js.length
  const completed = js.filter(j => j.status === 'completed')
  const failed = js.filter(j => j.status !== 'completed') // includes failed/timeout/running/queued
  const durations = completed
    .map(j => (j.completedAt! - j.startedAt))
    .sort((a, b) => a - b)
  const onTime = completed.filter(j => j.deadlineAt != null && j.completedAt! <= (j.deadlineAt!))
  const unitsTotal = js.reduce((acc, j) => acc + (j.units || 0), 0)
  const winMs = computeWindowMs(js)

  return {
    total,
    completed: completed.length,
    failed: failed.length,
    successRate: total === 0 ? 0 : completed.length / total,
    onTimeRate: completed.length === 0 ? 0 : onTime.length / completed.length,
    avgLatencyMs: durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : 0,
    p95LatencyMs: percentile(durations, 0.95),
    unitsTotal,
    jobsPerMin: (total * 60_000) / winMs,
    unitsPerMin: (unitsTotal * 60_000) / winMs,
  }
}

function filterByProvider(js: Job[], pid: string) {
  return js.filter(j => j.providerId === pid)
}

const expectedGlobal = expectedSLA(jobs)
const expectedP1 = expectedSLA(filterByProvider(jobs, 'P1'))
const expectedP2 = expectedSLA(filterByProvider(jobs, 'P2'))

// ---------- Adapter helpers to call whatever the SDK exports ----------

const SLA_FN_CANDIDATES = [
  'selectGlobalSLA',
  'selectSystemSLA',
  'selectSLA',
  'computeSLA',
  'aggregateSLA',
  'calcSLA',
]

function findSLAFn(mod: Record<string, any>) {
  for (const k of SLA_FN_CANDIDATES) {
    if (typeof (mod as any)[k] === 'function') return (mod as any)[k]
  }
  // also accept default export if it's a function
  if (typeof (mod as any).default === 'function') return (mod as any).default
  return null
}

function tryInvokeGlobal(fn: Function) {
  const shapes: any[] = [
    [state],             // selector(state)
    [slice],             // selector(slice)
    [jobs],              // selector(jobs)
    [providers, jobs, settlements], // selector(providers,jobs,settlements)
    [{ jobs }],          // selector({jobs})
    [state, undefined],  // selector(state, undefined)
  ]
  for (const args of shapes) {
    try {
      const out = fn(...args)
      if (out) return out
    } catch (_e) { /* try next */ }
  }
  throw new Error('Could not invoke SLA selector with known shapes')
}

// If providerId is not accepted as param, we fallback to filtering input before calling
function tryInvokeProvider(fn: Function, providerId: string) {
  const shapesWithId: any[] = [
    [state, providerId],
    [slice, providerId],
    [jobs, providerId],
    [providers, jobs, settlements, providerId],
    [{ jobs }, providerId],
  ]
  for (const args of shapesWithId) {
    try {
      const out = fn(...args)
      if (out) return out
    } catch (_e) { /* keep trying */ }
  }
  // fallback: filter jobs ourselves, then call global forms
  const filtered = filterByProvider(jobs, providerId)
  const fallbackShapes: any[] = [
    [{ aicf: { ...slice, jobs: filtered } }],
    [{ ...slice, jobs: filtered }],
    [filtered],
    [providers, filtered, settlements],
    [{ jobs: filtered }],
  ]
  for (const args of fallbackShapes) {
    try {
      const out = fn(...args)
      if (out) return out
    } catch (_e) { /* keep trying */ }
  }
  throw new Error('Could not invoke provider SLA selector with known shapes')
}

// normalize keys (support a few aliases)
function pickNumber(obj: any, keys: string[], fallback = 0) {
  for (const k of keys) {
    if (typeof obj?.[k] === 'number' && Number.isFinite(obj[k])) return obj[k]
  }
  return fallback
}

function normalizeSLA(obj: any) {
  return {
    total: pickNumber(obj, ['total', 'totalJobs', 'count']),
    completed: pickNumber(obj, ['completed', 'success', 'ok']),
    failed: pickNumber(obj, ['failed', 'errors']),
    successRate: pickNumber(obj, ['successRate', 'sr', 'success_ratio']),
    onTimeRate: pickNumber(obj, ['onTimeRate', 'otr', 'on_time_ratio', 'onTime']),
    avgLatencyMs: pickNumber(obj, ['avgLatencyMs', 'latencyAvgMs', 'avg_ms', 'avgLatency']),
    p95LatencyMs: pickNumber(obj, ['p95LatencyMs', 'latencyP95Ms', 'p95_ms', 'p95Latency']),
    unitsTotal: pickNumber(obj, ['unitsTotal', 'units', 'sumUnits']),
    jobsPerMin: pickNumber(obj, ['jobsPerMin', 'jobsPerMinute', 'jpm']),
    unitsPerMin: pickNumber(obj, ['unitsPerMin', 'unitsPerMinute', 'upm']),
  }
}

// ---------- Tests ----------

describe('AICF selectors — SLA/units aggregations', () => {
  it('module exposes an SLA aggregation function', () => {
    const fn = findSLAFn(AICF)
    expect(fn, 'Expected one of the SLA functions to be exported').toBeTruthy()
  })

  it('computes global SLA close to expected', () => {
    const fn = findSLAFn(AICF)
    expect(fn, 'SLA aggregation function required').toBeTruthy()

    const raw = tryInvokeGlobal(fn!)
    const got = normalizeSLA(raw)

    const exp = expectedGlobal

    expect(got.total).toBe(exp.total)
    expect(got.completed).toBe(exp.completed)
    expect(got.failed).toBe(exp.failed)

    expect(got.successRate).toBeGreaterThan(0)
    expect(got.successRate).toBeCloseTo(exp.successRate, 5)

    expect(got.onTimeRate).toBeCloseTo(exp.onTimeRate, 5)

    expect(got.avgLatencyMs).toBeGreaterThan(0)
    expect(got.avgLatencyMs).toBeCloseTo(exp.avgLatencyMs, -1) // within ~10ms absolute

    expect(got.p95LatencyMs).toBeGreaterThanOrEqual(exp.avgLatencyMs)
    // allow some leeway: percentile methods may vary slightly
    expect(got.p95LatencyMs).toBeCloseTo(exp.p95LatencyMs, -1)

    expect(got.unitsTotal).toBe(exp.unitsTotal)

    expect(got.jobsPerMin).toBeGreaterThan(0)
    expect(got.jobsPerMin).toBeCloseTo(exp.jobsPerMin, 5)

    expect(got.unitsPerMin).toBeGreaterThan(0)
    expect(got.unitsPerMin).toBeCloseTo(exp.unitsPerMin, 5)
  })

  it('computes per-provider SLA (P1) close to expected', () => {
    const fn = findSLAFn(AICF)
    expect(fn).toBeTruthy()

    const raw = tryInvokeProvider(fn!, 'P1')
    const got = normalizeSLA(raw)
    const exp = expectedP1

    expect(got.total).toBe(exp.total)
    expect(got.completed).toBe(exp.completed)
    expect(got.failed).toBe(exp.failed)
    expect(got.successRate).toBeCloseTo(exp.successRate, 5)
    expect(got.onTimeRate).toBeCloseTo(exp.onTimeRate, 5)
    expect(got.unitsTotal).toBe(exp.unitsTotal)
    expect(got.jobsPerMin).toBeCloseTo(exp.jobsPerMin, 5)
    expect(got.unitsPerMin).toBeCloseTo(exp.unitsPerMin, 5)
  })

  it('computes per-provider SLA (P2) close to expected', () => {
    const fn = findSLAFn(AICF)
    expect(fn).toBeTruthy()

    const raw = tryInvokeProvider(fn!, 'P2')
    const got = normalizeSLA(raw)
    const exp = expectedP2

    expect(got.total).toBe(exp.total)
    expect(got.completed).toBe(exp.completed)
    expect(got.failed).toBe(exp.failed)
    expect(got.successRate).toBeCloseTo(exp.successRate, 5)
    expect(got.onTimeRate).toBeCloseTo(exp.onTimeRate, 5)
    expect(got.unitsTotal).toBe(exp.unitsTotal)
    expect(got.jobsPerMin).toBeCloseTo(exp.jobsPerMin, 5)
    expect(got.unitsPerMin).toBeCloseTo(exp.unitsPerMin, 5)
  })
})
