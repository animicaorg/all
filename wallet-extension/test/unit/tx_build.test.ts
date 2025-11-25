import { describe, it, expect } from 'vitest'
import * as Encode from '../../src/background/tx/encode'
import * as Build from '../../src/background/tx/build'

// Small helpers ---------------------------------------------------------------
const toHex = (u8: Uint8Array) => Array.from(u8).map(b => b.toString(16).padStart(2, '0')).join('')
const utf8 = (s: string) => new TextEncoder().encode(s)
const includesBytes = (hay: Uint8Array, needle: Uint8Array) => {
  // naive search is fine for small test vectors
  for (let i = 0; i + needle.length <= hay.length; i++) {
    let ok = true
    for (let j = 0; j < needle.length; j++) if (hay[i + j] !== needle[j]) { ok = false; break }
    if (ok) return true
  }
  return false
}

describe('encodeSignBytes — canonical CBOR & domain separation', () => {
  const chainIdA = 'animica:devnet:1'
  const chainIdB = 'animica:testnet:2'

  // If the encoder exports a domain constant, use it; otherwise fall back to a sane default.
  const SIGN_DOMAIN: string = (Encode as any).SIGN_DOMAIN ?? 'AnimicaSignedTx'

  const baseTxObj: any = {
    kind: 'transfer',
    from: 'anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqf3cz3t',
    to:   'anim1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx3nl2a',
    amount: '123456',   // value form should not matter for byte-level determinism in CBOR
    nonce: 7,
    gas: { limit: 50_000, price: 1 },
    memo: 'test'
  }

  it('is stable for identical logical tx objects (key order agnostic)', () => {
    const tx1 = { ...baseTxObj }
    // change property insertion order
    const tx2: any = { kind: 'transfer', to: baseTxObj.to, from: baseTxObj.from }
    tx2.amount = baseTxObj.amount
    tx2.gas = baseTxObj.gas
    tx2.nonce = baseTxObj.nonce
    tx2.memo = baseTxObj.memo

    const b1 = Encode.encodeSignBytes(tx1, chainIdA)
    const b2 = Encode.encodeSignBytes(tx2, chainIdA)
    expect(toHex(b1)).toBe(toHex(b2))
  })

  it('changes when any tx field changes (nonce flip)', () => {
    const b1 = Encode.encodeSignBytes(baseTxObj, chainIdA)
    const b2 = Encode.encodeSignBytes({ ...baseTxObj, nonce: baseTxObj.nonce + 1 }, chainIdA)
    expect(toHex(b1)).not.toBe(toHex(b2))
  })

  it('changes when chainId changes (domain separation)', () => {
    const a = Encode.encodeSignBytes(baseTxObj, chainIdA)
    const b = Encode.encodeSignBytes(baseTxObj, chainIdB)
    expect(toHex(a)).not.toBe(toHex(b))
  })

  it('embeds the signing domain string and the chainId inside the CBOR blob', () => {
    const bytes = Encode.encodeSignBytes(baseTxObj, chainIdA)
    expect(includesBytes(bytes, utf8(SIGN_DOMAIN))).toBe(true)
    expect(includesBytes(bytes, utf8(chainIdA))).toBe(true)
  })
})

describe('builders + encoder — transfer path smoke test', () => {
  const chainId = 'animica:devnet:1'

  it('buildTransferTx then encodeSignBytes succeeds and contains key fields', () => {
    // If the builder is present, use it; otherwise fall back to base object.
    const hasBuilder = typeof (Build as any).buildTransferTx === 'function'

    // plausible bech32m addresses (format is not validated here)
    const from = 'anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqf3cz3t'
    const to   = 'anim1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx3nl2a'

    const tx = hasBuilder
      ? (Build as any).buildTransferTx({ from, to, amount: '42', nonce: 1, gasLimit: 30_000, gasPrice: 1 })
      : { kind: 'transfer', from, to, amount: '42', nonce: 1, gas: { limit: 30_000, price: 1 } }

    const signBytes = Encode.encodeSignBytes(tx, chainId)

    // Basic invariants: bytes are non-empty, and CBOR blob still includes human strings for chainId/addresses
    expect(signBytes.length).toBeGreaterThan(10)
    expect(includesBytes(signBytes, utf8(chainId))).toBe(true)
    expect(includesBytes(signBytes, utf8(from))).toBe(true)
    expect(includesBytes(signBytes, utf8(to))).toBe(true)
  })
})
