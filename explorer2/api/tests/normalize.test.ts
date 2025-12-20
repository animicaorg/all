import { describe, expect, it } from 'vitest'
import { normalizeBlockDetail, normalizeHead, normalizeTxDetail } from '../src/normalize'

describe('normalizeHead', () => {
  it('normalizes core fields', () => {
    const head = normalizeHead({ height: '0x0a', hash: '0xabc', time: 123 })
    expect(head.height).toBe(10)
    expect(head.hash).toBe('0xabc')
  })
})

describe('normalizeBlockDetail', () => {
  it('normalizes block detail', () => {
    const block = normalizeBlockDetail({ header: { height: 2, hash: '0x1', parentHash: '0x0', time: 12 }, txs: [] })
    expect(block.height).toBe(2)
    expect(block.hash).toBe('0x1')
  })
})

describe('normalizeTxDetail', () => {
  it('marks confirmed transactions', () => {
    const tx = { hash: '0xtx', from: 'anim1from', to: 'anim1to', value: '0x1' }
    const receipt = { txHash: '0xtx', blockNumber: 5, status: 'SUCCESS' }
    const detail = normalizeTxDetail(tx, receipt)
    expect(detail.status).toBe('confirmed')
  })
})
