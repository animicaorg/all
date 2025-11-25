import { describe, test, expect } from 'vitest'

/**
 * This test exercises the event decoder in a tolerant way:
 *  - We probe for common helper names exported by ../src/contracts/events
 *  - If no encoder is exported, we fall back to CBOR for data and sha3_256 for topic
 *  - We accept several output shapes (args as array or object; bigint or number)
 */

type EventABI = {
  name: string
  inputs: { name: string; type: string; indexed?: boolean }[]
}

const ABI = {
  version: '1',
  contractName: 'Counter',
  functions: [],
  events: [
    { name: 'Changed', inputs: [{ name: 'value', type: 'uint64', indexed: false }] }
  ],
  errors: []
}

function normalizeNumber(x: any): number {
  if (typeof x === 'bigint') return Number(x)
  if (typeof x === 'number') return x
  if (typeof x === 'string' && /^-?\d+$/.test(x)) return Number(x)
  throw new Error('not a numeric-ish value: ' + x)
}

async function loadEventsMod(): Promise<any> {
  return await import('../src/contracts/events')
}

function pickEncoder(mod: any): ((ev: EventABI, args: Record<string, any> | any[]) => Uint8Array) | null {
  const cands = ['encodeEventData', 'encodeEvent', 'packEventData', 'encodeArgs']
  for (const k of cands) {
    const fn = mod[k] || mod?.default?.[k]
    if (typeof fn === 'function') {
      return (ev: EventABI, args: any) => {
        try {
          return fn(ev, args)
        } catch {
          // Some APIs accept (abi, evName, args) or (name, args)
          try { return fn(ABI, ev, args) } catch {}
          try { return fn(ABI, ev.name, args) } catch {}
          try { return fn(ev.name, args) } catch {}
          throw new Error('encode function signature not recognized')
        }
      }
    }
  }
  return null
}

function pickTopic(mod: any): ((ev: EventABI) => string) | null {
  const cands = ['computeEventTopic', 'eventTopic', 'topicForEvent', 'idForEvent', 'eventId']
  for (const k of cands) {
    const fn = mod[k] || mod?.default?.[k]
    if (typeof fn === 'function') {
      return (ev: EventABI) => {
        try { return fn(ev) } catch {}
        try { return fn(ABI, ev) } catch {}
        try { return fn(ev.name, ev.inputs) } catch {}
      }
    }
  }
  return null
}

function pickDecoder(mod: any): ((abiOrEv: any, log: any) => any) {
  const cands = ['decodeEventLog', 'decodeLog', 'parseLog', 'decode']
  for (const k of cands) {
    const fn = mod[k] || mod?.default?.[k]
    if (typeof fn === 'function') {
      return (abiOrEv: any, log: any) => {
        // Try common call shapes
        try { return fn(abiOrEv, log) } catch {}
        try { return fn(ABI, abiOrEv, log) } catch {}
        try { return fn({ abi: ABI, event: abiOrEv, log }) } catch {}
        try { return fn({ abi: ABI, log }) } catch {}
        try { return fn(log, abiOrEv) } catch {}
        throw new Error('decode function signature not recognized')
      }
    }
  }
  throw new Error('No decode function exported by contracts/events')
}

async function fallbackTopic(ev: EventABI): Promise<string> {
  // Topic fallback: sha3_256 of canonical signature "Name(type1,type2,...)"
  const { sha3_256 } = await import('../src/utils/hash')
  const { bytesToHex } = await import('../src/utils/bytes')
  const sig = `${ev.name}(${ev.inputs.map(i => i.type).join(',')})`
  const digest = sha3_256(new TextEncoder().encode(sig))
  return '0x' + bytesToHex(digest)
}

async function fallbackData(args: Record<string, any> | any[]): Promise<Uint8Array> {
  const { encodeCanonicalCBOR } = await import('../src/utils/cbor')
  return encodeCanonicalCBOR(args)
}

describe('contracts/events — decode event logs', () => {
  test('decodes a simple uint64 Changed event', async () => {
    const mod = await loadEventsMod()
    const encode = pickEncoder(mod)
    const topicFn = pickTopic(mod)
    const decode = pickDecoder(mod)

    const ev: EventABI = ABI.events[0]
    const argsObj = { value: 123 }
    const data = encode ? encode(ev, argsObj) : await fallbackData([argsObj.value])

    const topic = topicFn ? topicFn(ev) : await fallbackTopic(ev)
    const logVariants = [
      { topics: [topic], data },
      { topics: [topic], data: '0x' + Buffer.from(data).toString('hex') },
      { address: 'anim1zzzzzzzzzz', topics: [topic], data }
    ]

    // Try multiple log shapes until we get a decoded result
    let out: any | null = null
    for (const log of logVariants) {
      try {
        out = decode(ev, log)
        if (out) break
      } catch {
        // try next variant
      }
    }
    if (!out) throw new Error('decoder did not return a result for any log variant')

    // Normalize common shapes: {name, args:{value}} or {event:'Changed', values:[123]} …
    const name = out.name || out.event || out.eventName || out.type || ''
    expect(String(name)).toMatch(/Changed/i)

    // Extract value
    let val: any
    if (out.args && typeof out.args === 'object') {
      val = out.args.value ?? out.args['0'] ?? out.value
    } else if (Array.isArray(out.values)) {
      val = out.values[0]
    } else if (out.value != null) {
      val = out.value
    }
    expect(normalizeNumber(val)).toBe(123)
  })
})
