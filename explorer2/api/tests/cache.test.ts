import { describe, expect, it } from 'vitest'
import { RequestCoalescer, TtlCache } from '../src/cache'

describe('TtlCache', () => {
  it('returns cached values until expiry', () => {
    const cache = new TtlCache()
    cache.set('a', 1, 1000)
    expect(cache.get('a')).toBe(1)
  })
})

describe('RequestCoalescer', () => {
  it('coalesces concurrent requests', async () => {
    const coalescer = new RequestCoalescer()
    let calls = 0
    const task = () => {
      calls += 1
      return Promise.resolve('ok')
    }
    const [a, b] = await Promise.all([coalescer.run('k', task), coalescer.run('k', task)])
    expect(a).toBe('ok')
    expect(b).toBe('ok')
    expect(calls).toBe(1)
  })
})
