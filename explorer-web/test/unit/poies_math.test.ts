import { describe, it, expect } from 'vitest'
import * as math from '../../src/utils/poies_math'

/**
 * These tests are written to be resilient to slight naming differences.
 * We pick from a set of common export names used across our utils.
 */
function pickFn<T extends (...args: any[]) => any>(...candidates: string[]): T {
  for (const name of candidates) {
    const fn = (math as any)[name]
    if (typeof fn === 'function') return fn as T
  }
  throw new Error(`None of the candidate functions found: ${candidates.join(', ')}`)
}

const normalize = pickFn<Record<string, number>, [Record<string, number>]>(
  'normalizeMix',
  'normalize',
  'toPercentages',
  'mixToShares'
)

const applyCaps = pickFn<
  Record<string, number>,
  [Record<string, number>, Record<string, number>]
>('clipCaps', 'applyCaps', 'capMix', 'enforceCaps')

const gini = pickFn<number, [number[]]>('gini', 'giniIndex')
const hhi = pickFn<number, [number[]]>('hhi', 'hhiIndex', 'herfindahl')

function sum(values: number[]) {
  return values.reduce((a, b) => a + b, 0)
}
function nearly(a: number, b: number, eps = 1e-9) {
  expect(Math.abs(a - b)).toBeLessThanOrEqual(eps)
}

describe('PoIES mix calculations', () => {
  it('normalize turns absolute counts into shares that sum to 1', () => {
    const mix = { hash: 40, ai: 30, quantum: 30 }
    const out = normalize(mix)

    const values = Object.values(out)
    nearly(sum(values), 1)

    // Expected shares
    nearly(out.hash, 0.4)
    nearly(out.ai, 0.3)
    nearly(out.quantum, 0.3)

    // No negatives
    for (const v of values) {
      expect(v).toBeGreaterThanOrEqual(0)
    }
  })

  it('apply caps enforces per-type limits and rebalances the remainder', () => {
    // Start with a skew
    const shares = { a: 0.7, b: 0.2, c: 0.1 }
    // Reasonable caps (water-filling should re-distribute beyond caps)
    const caps = { a: 0.5, b: 0.4, c: 0.2 }

    const clipped = applyCaps(shares, caps)
    const vals = Object.values(clipped)

    // Still a valid share vector
    nearly(sum(vals), 1)

    // Respect caps
    expect(clipped.a).toBeLessThanOrEqual(0.5)
    expect(clipped.b).toBeLessThanOrEqual(0.4)
    expect(clipped.c).toBeLessThanOrEqual(0.2)

    // With simple proportional redistribution, the expected result is:
    // a clipped to 0.5; leftover 0.5 distributed across b and c
    // proportionally to their original 0.2:0.1 ratio → 2:1
    // b = (2/3)*0.5 = 0.333..., c = (1/3)*0.5 = 0.166...
    nearly(clipped.a, 0.5)
    nearly(clipped.b, 1 / 3) // ~0.3333333333
    nearly(clipped.c, 1 / 6) // ~0.1666666667
  })
})

describe('Fairness metrics (Gini, HHI)', () => {
  it('Gini: perfectly equal distribution → 0', () => {
    const eq4 = [0.25, 0.25, 0.25, 0.25]
    nearly(gini(eq4), 0)
  })

  it('Gini: single winner among 4 → 0.75', () => {
    const extreme = [1, 0, 0, 0]
    nearly(gini(extreme), 0.75)
  })

  it('Gini: mixed distribution [0.5, 0.3, 0.2] → ~0.2', () => {
    const mixed = [0.5, 0.3, 0.2]
    nearly(gini(mixed), 0.2, 1e-9)
  })

  it('HHI: perfectly equal distribution among 4 → 0.25', () => {
    const eq4 = [0.25, 0.25, 0.25, 0.25]
    nearly(hhi(eq4), 0.25)
  })

  it('HHI: single winner among 4 → 1', () => {
    const extreme = [1, 0, 0, 0]
    nearly(hhi(extreme), 1)
  })

  it('HHI: mixed distribution [0.5, 0.3, 0.2] → 0.38', () => {
    const mixed = [0.5, 0.3, 0.2]
    nearly(hhi(mixed), 0.38)
  })
})
