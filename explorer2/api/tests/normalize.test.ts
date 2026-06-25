import { describe, expect, it } from 'vitest'
import { bech32m } from 'bech32'
import {
  canonicalAddressKey,
  normalizeBlockDetail,
  normalizeBlockSummary,
  normalizeHead,
  normalizeTxDetail,
  normalizeTxSummary
} from '../src/normalize'

describe('normalizeHead', () => {
  it('normalizes core fields with height', () => {
    const head = normalizeHead({ height: '0x0a', hash: '0xabc', time: 123 })
    expect(head.height).toBe(10)
    expect(head.hash).toBe('0xabc')
  })

  it('falls back to number field when height is missing', () => {
    const head = normalizeHead({ number: 42, hash: '0xdef', time: 456 })
    expect(head.height).toBe(42)
    expect(head.hash).toBe('0xdef')
  })

  it('includes canonicalHeight when present', () => {
    const head = normalizeHead({ height: 10, canonicalHeight: 8, hash: '0xabc', time: 123 })
    expect(head.height).toBe(10)
    expect(head.canonicalHeight).toBe(8)
  })

  it('handles canonical_height snake_case variant', () => {
    const head = normalizeHead({ height: 10, canonical_height: 8, hash: '0xabc', time: 123 })
    expect(head.height).toBe(10)
    expect(head.canonicalHeight).toBe(8)
  })

  it('includes thetaMicro from top-level and header fields', () => {
    const topLevel = normalizeHead({ height: 10, hash: '0xabc', time: 123, thetaMicro: '2000000' })
    expect(topLevel.thetaMicro).toBe(2_000_000)

    const nested = normalizeHead({
      height: 11,
      hash: '0xdef',
      time: 124,
      header: { theta_micro: '0x1e8480' } // 2,000,000
    })
    expect(nested.thetaMicro).toBe(2_000_000)
  })

  it('falls back to nested header timestamp and normalizes milliseconds to seconds', () => {
    const head = normalizeHead({
      height: 12,
      hash: '0xaaa',
      header: { timestamp: 1_700_000_123_000 }
    })
    expect(head.time).toBe(1_700_000_123)
  })
})

describe('normalizeBlockSummary', () => {
  it('normalizes block with number field in header', () => {
    const block = normalizeBlockSummary({ 
      header: { number: 5, hash: '0xblock5', timestamp: 1000 }, 
      txs: ['0xtx1', '0xtx2'] 
    })
    expect(block.height).toBe(5)
    expect(block.hash).toBe('0xblock5')
    expect(block.txCount).toBe(2)
  })

  it('falls back to top-level number when header.number is missing', () => {
    const block = normalizeBlockSummary({ 
      number: 10, 
      hash: '0xblock10', 
      time: 2000, 
      transactions: [] 
    })
    expect(block.height).toBe(10)
    expect(block.hash).toBe('0xblock10')
  })

  it('includes canonicalHeight when present', () => {
    const block = normalizeBlockSummary({ 
      header: { height: 10, canonicalHeight: 8, hash: '0xblock10', time: 1000 }, 
      txs: [] 
    })
    expect(block.height).toBe(10)
    expect(block.canonicalHeight).toBe(8)
  })

  it('includes thetaMicro from header fields', () => {
    const block = normalizeBlockSummary({
      header: { height: 11, hash: '0xblock11', time: 1001, theta_micro: '0x1e8480' },
      txs: []
    })
    expect(block.thetaMicro).toBe(2_000_000)
  })

  it('falls back to block.timestamp when header time fields are missing', () => {
    const block = normalizeBlockSummary({
      number: 12,
      hash: '0xblock12',
      timestamp: 1_700_000_120,
      transactions: []
    })
    expect(block.height).toBe(12)
    expect(block.time).toBe(1_700_000_120)
  })

  it('normalizes microsecond timestamps to seconds', () => {
    const block = normalizeBlockSummary({
      header: { height: 13, hash: '0xblock13', timestamp: 1_700_000_130_000_000 },
      txs: []
    })
    expect(block.time).toBe(1_700_000_130)
  })
})

describe('normalizeBlockDetail', () => {
  it('normalizes block detail with height', () => {
    const block = normalizeBlockDetail({ header: { height: 2, hash: '0x1', parentHash: '0x0', time: 12 }, txs: [] })
    expect(block.height).toBe(2)
    expect(block.hash).toBe('0x1')
  })

  it('normalizes block detail with number field', () => {
    const block = normalizeBlockDetail({ 
      header: { number: 7, hash: '0x7', parentHash: '0x6', timestamp: 700 }, 
      txs: [] 
    })
    expect(block.height).toBe(7)
    expect(block.hash).toBe('0x7')
  })

  it('includes canonicalHeight and nonce when present', () => {
    const block = normalizeBlockDetail({ 
      header: { height: 10, canonicalHeight: 8, nonce: 12345, hash: '0x1', parentHash: '0x0', time: 12 }, 
      txs: [] 
    })
    expect(block.height).toBe(10)
    expect(block.canonicalHeight).toBe(8)
    expect(block.nonce).toBe(12345)
  })

  it('detects instant blocks with nonce=0', () => {
    const block = normalizeBlockDetail({ 
      header: { height: 10, canonicalHeight: 8, nonce: 0, hash: '0x1', parentHash: '0x0', time: 12 }, 
      txs: [] 
    })
    expect(block.height).toBe(10)
    expect(block.nonce).toBe(0)
  })

  it('falls back to block.timestamp and normalizes nanoseconds to seconds', () => {
    const block = normalizeBlockDetail({
      number: 14,
      hash: '0x14',
      parentHash: '0x13',
      timestamp: 1_700_000_140_000_000_000,
      txs: []
    })
    expect(block.height).toBe(14)
    expect(block.time).toBe(1_700_000_140)
  })
})

describe('normalizeTxDetail', () => {
  it('marks confirmed transactions', () => {
    const tx = { hash: '0xtx', from: 'anim1from', to: 'anim1to', value: '0x1' }
    const receipt = { txHash: '0xtx', blockNumber: 5, status: 'SUCCESS' }
    const detail = normalizeTxDetail(tx, receipt)
    expect(detail.status).toBe('confirmed')
  })

  it('treats genesis blockNumber=0 as confirmed (not pending)', () => {
    const tx = { hash: '0xtx', from: 'anim1from', to: 'anim1to', value: '0x1' }
    const receipt = { txHash: '0xtx', blockNumber: 0, status: 'SUCCESS' }
    const detail = normalizeTxDetail(tx, receipt)
    expect(detail.blockHeight).toBe(0)
    expect(detail.status).toBe('confirmed')
  })

  it('accepts snake_case receipt fields and sender/recipient aliases', () => {
    const tx = { txHash: '0xtx', sender: 'anim1from', recipient: 'anim1to', fee: '0x2' }
    const receipt = { txHash: '0xtx', block_height: '0x2', block_hash: '0xabc', fee: '0x3', status: 'SUCCESS' }
    const detail = normalizeTxDetail(tx, receipt)
    expect(detail.blockHeight).toBe(2)
    expect(detail.blockHash).toBe('0xabc')
    expect(detail.from).toBe('anim1from')
    expect(detail.to).toBe('anim1to')
    expect(detail.feePaid).toBe('0x3')
  })
})

describe('address and amount normalization', () => {
  it('normalizes 32-byte hex addresses into bech32m', () => {
    const digest = '0x' + '11'.repeat(32)
    const summary = normalizeTxSummary({ hash: '0xabc', from: digest, to: digest, value: '0x1' })
    expect(summary.from?.startsWith('anim1')).toBe(true)
    expect(summary.to?.startsWith('anim1')).toBe(true)
  })

  // Decode an anim1… address back to (alg_id, digest) so we can assert the
  // reconstructed alg_id, not just the prefix. payload = alg_id(2 BE) ‖ digest.
  const decodeAddr = (addr: string) => {
    const payload = Buffer.from(bech32m.fromWords(bech32m.decode(addr).words))
    return { algId: (payload[0] << 8) | payload[1], digest: payload.subarray(2).toString('hex') }
  }

  it('reconstructs a digest with the active default alg_id 0x1003 (ml_dsa_65), not deprecated 0x1001', () => {
    const digestHex = 'aa'.repeat(32)
    const summary = normalizeTxSummary({ hash: '0xabc', to: '0x' + digestHex, value: '0x1' })
    const dec = decodeAddr(summary.to as string)
    expect(dec.algId).toBe(0x1003)
    expect(dec.digest).toBe(digestHex)
  })

  it('derives the sender address from its signature alg_id exactly (sphincs+ 0x1002)', () => {
    const digestHex = 'bb'.repeat(32)
    const summary = normalizeTxSummary({
      hash: '0xs',
      from: '0x' + digestHex,
      to: '0x' + 'cc'.repeat(32),
      sigs: [{ alg: 0x1002, pubkey: '0xdead' }]
    })
    const dec = decodeAddr(summary.from as string)
    expect(dec.algId).toBe(0x1002) // not the 0x1003 default — taken from the sig
    expect(dec.digest).toBe(digestHex)
  })

  it('remembers a learned sender alg_id so the same account renders correctly as a recipient', () => {
    const digestHex = 'ab'.repeat(32)
    // First seen as a sender signing with sphincs+ (0x1002) -> learned.
    normalizeTxSummary({ hash: '0xs1', from: '0x' + digestHex, to: '0x' + 'ee'.repeat(32), sigs: [{ alg: 0x1002, pubkey: '0x01' }] })
    // Later seen only as a recipient: should use the learned 0x1002, not the default.
    const recv = normalizeTxSummary({ hash: '0xs2', from: '0x' + 'ff'.repeat(32), to: '0x' + digestHex, sigs: [{ alg: 0x1003, pubkey: '0x02' }] })
    expect(decodeAddr(recv.to as string).algId).toBe(0x1002)
  })

  it('maps equivalent bech32 payloads to the same canonical address key', () => {
    const digest = Uint8Array.from(Buffer.from('22'.repeat(32), 'hex'))
    const payloadA = Uint8Array.from([0x10, 0x01, ...digest])
    const payloadB = Uint8Array.from([0x10, 0x02, ...digest])
    const addrA = bech32m.encode('anim', bech32m.toWords(payloadA))
    const addrB = bech32m.encode('anim', bech32m.toWords(payloadB))
    expect(canonicalAddressKey(addrA)).toBe(canonicalAddressKey(addrB))
  })

  it('extracts nested transfer amounts for summary rendering', () => {
    const summary = normalizeTxSummary({
      hash: '0xabc',
      payload: { v: { amount: '0x2a' } }
    })
    expect(summary.value).toBe('0x2a')
  })

  it('derives miner address from coinbase-like transaction when header miner is missing', () => {
    const block = normalizeBlockDetail({
      header: { height: 10, hash: '0xblock10', time: 1_700_000_010 },
      txs: [
        {
          hash: '0xcoinbase',
          kind: 'COINBASE',
          from: '0x' + '0'.repeat(64),
          to: '0x' + '33'.repeat(32),
          amount: '0x64'
        }
      ]
    })
    expect(block.miner?.startsWith('anim1')).toBe(true)
  })
})
