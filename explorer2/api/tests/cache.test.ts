import { describe, expect, it } from 'vitest'
import { RequestCoalescer } from '../src/cache'

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
