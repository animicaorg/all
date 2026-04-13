import { createHash } from 'node:crypto'
import type { DecodedEvent, DecodedFunctionCall, DecodedValue } from '@animica/explorer2-shared'

interface AbiParam {
  name: string
  type: string
  indexed?: boolean
}

interface AbiFunction {
  name: string
  inputs: AbiParam[]
  outputs: AbiParam[]
  stateMutability?: string
}

interface AbiEvent {
  name: string
  inputs: AbiParam[]
  anonymous?: boolean
}

interface NormalizedAbi {
  functions: AbiFunction[]
  events: AbiEvent[]
}

type TypeDesc =
  | { kind: 'scalar'; type: string }
  | { kind: 'array'; element: TypeDesc; dims: Array<number | null> }
  | { kind: 'tuple'; items: TypeDesc[]; dims: Array<number | null> }

function sha3(data: Buffer | string): Buffer {
  const h = createHash('sha3-256')
  h.update(data)
  return h.digest()
}

function asHex(data: Buffer): string {
  return `0x${data.toString('hex')}`
}

function parseHex(value: string): Buffer<ArrayBufferLike> {
  const raw = value.trim().toLowerCase().replace(/^0x/, '')
  if (!raw.length) return Buffer.alloc(0)
  const padded = raw.length % 2 === 1 ? `0${raw}` : raw
  if (/[^0-9a-f]/i.test(padded)) {
    throw new Error('invalid hex')
  }
  return Buffer.from(padded, 'hex')
}

function canonicalType(input: string): string {
  const text = String(input || '').replace(/\s+/g, '').toLowerCase()
  if (!text) return 'bytes'
  if (text === 'uint') return 'uint256'
  if (text === 'int') return 'int256'
  return text
}

function splitTopLevelTypes(value: string): string[] {
  const out: string[] = []
  let depthParen = 0
  let depthBracket = 0
  let cursor = ''
  for (const ch of value) {
    if (ch === ',' && depthParen === 0 && depthBracket === 0) {
      out.push(cursor)
      cursor = ''
      continue
    }
    cursor += ch
    if (ch === '(') depthParen += 1
    if (ch === ')') depthParen = Math.max(0, depthParen - 1)
    if (ch === '[') depthBracket += 1
    if (ch === ']') depthBracket = Math.max(0, depthBracket - 1)
  }
  if (cursor.length) out.push(cursor)
  return out.map((item) => item.trim()).filter(Boolean)
}

function parseType(typeName: string): TypeDesc {
  const normalized = canonicalType(typeName)
  const dims: Array<number | null> = []
  let cursor = normalized
  while (cursor.endsWith(']')) {
    const left = cursor.lastIndexOf('[')
    if (left < 0) break
    const size = cursor.slice(left + 1, cursor.length - 1)
    dims.unshift(size.length ? Number.parseInt(size, 10) : null)
    cursor = cursor.slice(0, left)
  }

  let base: TypeDesc
  if (cursor.startsWith('(') && cursor.endsWith(')')) {
    const inside = cursor.slice(1, -1)
    const items = splitTopLevelTypes(inside).map(parseType)
    base = { kind: 'tuple', items, dims: [] }
  } else {
    base = { kind: 'scalar', type: cursor }
  }

  if (dims.length) {
    return { kind: 'array', element: base, dims }
  }
  return base
}

function decodeUvarint(data: Buffer, offset: number): [number, number] {
  let value = 0
  let shift = 0
  let index = offset
  for (let i = 0; i < 10; i += 1) {
    if (index >= data.length) throw new Error('truncated varint')
    const byte = data[index]
    index += 1
    value |= (byte & 0x7f) << shift
    if ((byte & 0x80) === 0) return [value >>> 0, index]
    shift += 7
  }
  throw new Error('varint too large')
}

function readExact(data: Buffer, offset: number, length: number): [Buffer, number] {
  if (offset + length > data.length) throw new Error('truncated payload')
  return [data.subarray(offset, offset + length), offset + length]
}

function toSignedBigInt(payload: Buffer): bigint {
  if (!payload.length) return 0n
  const sign = payload[0]
  const magnitude = payload.subarray(1)
  const value = magnitude.length ? BigInt(`0x${magnitude.toString('hex')}`) : 0n
  return sign === 0x01 ? -value : value
}

function formatInteger(value: bigint): string {
  return value.toString()
}

function decodeScalar(data: Buffer, typeName: string, offset: number): [unknown, number] {
  const type = canonicalType(typeName)
  if (type.startsWith('uint')) {
    const [length, afterLen] = decodeUvarint(data, offset)
    const [payload, next] = readExact(data, afterLen, length)
    const value = payload.length ? BigInt(`0x${payload.toString('hex')}`) : 0n
    return [formatInteger(value), next]
  }

  if (type.startsWith('int')) {
    const [length, afterLen] = decodeUvarint(data, offset)
    const [payload, next] = readExact(data, afterLen, length)
    return [formatInteger(toSignedBigInt(payload)), next]
  }

  if (type === 'bool') {
    const [payload, next] = readExact(data, offset, 1)
    if (payload[0] !== 0 && payload[0] !== 1) throw new Error('invalid bool')
    return [payload[0] === 1, next]
  }

  if (type === 'address' || type === 'string' || type === 'bytes') {
    const [length, afterLen] = decodeUvarint(data, offset)
    const [payload, next] = readExact(data, afterLen, length)
    if (type === 'address' || type === 'string') {
      return [payload.toString('utf-8'), next]
    }
    return [asHex(payload), next]
  }

  if (type === 'hash') {
    const [payload, next] = readExact(data, offset, 32)
    return [asHex(payload), next]
  }

  if (type.startsWith('bytes') && type.length > 5) {
    const size = Number.parseInt(type.slice(5), 10)
    const [payload, next] = readExact(data, offset, size)
    return [asHex(payload), next]
  }

  throw new Error(`unsupported scalar type: ${type}`)
}

function decodeByDesc(data: Buffer, desc: TypeDesc, offset: number): [unknown, number] {
  if (desc.kind === 'scalar') return decodeScalar(data, desc.type, offset)

  if (desc.kind === 'array') {
    const [count, afterCount] = decodeUvarint(data, offset)
    const expected = desc.dims[0]
    if (expected !== null && expected !== count) {
      throw new Error(`array length mismatch expected ${expected} got ${count}`)
    }
    const nextDesc: TypeDesc =
      desc.dims.length > 1 ? { kind: 'array', element: desc.element, dims: desc.dims.slice(1) } : desc.element
    const out: unknown[] = []
    let cursor = afterCount
    for (let i = 0; i < count; i += 1) {
      const [value, next] = decodeByDesc(data, nextDesc, cursor)
      out.push(value)
      cursor = next
    }
    return [out, cursor]
  }

  const [count, afterCount] = decodeUvarint(data, offset)
  if (count !== desc.items.length) {
    throw new Error(`tuple length mismatch expected ${desc.items.length} got ${count}`)
  }
  const out: unknown[] = []
  let cursor = afterCount
  for (const item of desc.items) {
    const [value, next] = decodeByDesc(data, item, cursor)
    out.push(value)
    cursor = next
  }
  return [out, cursor]
}

function canonicalFnSignature(fn: AbiFunction): string {
  const inputs = fn.inputs.map((param) => canonicalType(param.type)).join(',')
  return `${fn.name}(${inputs})`
}

function functionSelectorHex(fn: AbiFunction): string {
  return asHex(sha3(`animica:abi:v1|${canonicalFnSignature(fn)}`).subarray(0, 8))
}

function eventSignature(ev: AbiEvent): string {
  const args = ev.inputs.map((input) => canonicalType(input.type)).join(',')
  return `${ev.name}(${args})`
}

function eventTopicHex(ev: AbiEvent): string {
  return asHex(sha3(eventSignature(ev)))
}

function decodeIndexedTopic(topicHex: string, typeName: string): unknown {
  const topic = parseHex(topicHex)
  const type = canonicalType(typeName)
  if (type.startsWith('uint')) {
    return formatInteger(BigInt(`0x${topic.subarray(Math.max(0, topic.length - 32)).toString('hex') || '0'}`))
  }
  if (type.startsWith('int')) {
    const bytes = topic.subarray(Math.max(0, topic.length - 32))
    const unsigned = BigInt(`0x${bytes.toString('hex') || '0'}`)
    const bits = Number.parseInt(type.slice(3), 10) || 256
    const signBit = 1n << BigInt(bits - 1)
    const full = 1n << BigInt(bits)
    const signed = unsigned >= signBit ? unsigned - full : unsigned
    return formatInteger(signed)
  }
  if (type === 'bool') return topic[topic.length - 1] !== 0
  return asHex(topic)
}

function toParam(entry: any, index: number): AbiParam | null {
  if (!entry || typeof entry !== 'object') return null
  const type = canonicalType(String(entry.type ?? 'bytes'))
  return {
    name: String(entry.name ?? `${index}`),
    type,
    indexed: Boolean(entry.indexed)
  }
}

function normalizeAbi(abiLike: unknown): NormalizedAbi {
  const root = (abiLike && typeof abiLike === 'object' && 'abi' in (abiLike as any))
    ? (abiLike as any).abi
    : abiLike

  let functionEntries: any[] = []
  let eventEntries: any[] = []

  if (Array.isArray(root)) {
    functionEntries = root.filter((item) => item && typeof item === 'object' && String(item.type || '').toLowerCase() === 'function')
    eventEntries = root.filter((item) => item && typeof item === 'object' && String(item.type || '').toLowerCase() === 'event')
  } else if (root && typeof root === 'object') {
    const record = root as Record<string, unknown>
    const functions = Array.isArray(record.functions) ? record.functions : []
    const events = Array.isArray(record.events) ? record.events : []
    functionEntries = functions.filter((item) => item && typeof item === 'object')
    eventEntries = events.filter((item) => item && typeof item === 'object')
  }

  const functions: AbiFunction[] = functionEntries
    .map((item: any) => ({
      name: String(item.name ?? ''),
      inputs: (Array.isArray(item.inputs) ? item.inputs : []).map((input: any, index: number) => toParam(input, index)).filter(Boolean) as AbiParam[],
      outputs: (Array.isArray(item.outputs) ? item.outputs : []).map((output: any, index: number) => toParam(output, index)).filter(Boolean) as AbiParam[],
      stateMutability: typeof item.stateMutability === 'string' ? item.stateMutability : undefined
    }))
    .filter((fn) => fn.name.length > 0)

  const events: AbiEvent[] = eventEntries
    .map((item: any) => ({
      name: String(item.name ?? ''),
      inputs: (Array.isArray(item.inputs) ? item.inputs : []).map((input: any, index: number) => toParam(input, index)).filter(Boolean) as AbiParam[],
      anonymous: Boolean(item.anonymous)
    }))
    .filter((ev) => ev.name.length > 0)

  return { functions, events }
}

export function decodeCallWithAbi(inputHex: string, abiLike: unknown): DecodedFunctionCall | null {
  let raw: Buffer
  try {
    raw = parseHex(inputHex)
  } catch {
    return null
  }
  if (!raw.length) return null

  const selector = asHex(raw.subarray(0, Math.min(8, raw.length)))
  const abi = normalizeAbi(abiLike)
  if (!abi.functions.length) {
    return { selector, rawData: inputHex }
  }

  const fn = abi.functions.find((candidate) => functionSelectorHex(candidate) === selector)
  if (!fn) {
    return { selector, rawData: inputHex }
  }

  const decoded: DecodedFunctionCall = {
    selector,
    signature: canonicalFnSignature(fn),
    name: fn.name,
    rawData: inputHex
  }

  try {
    let cursor = Math.min(8, raw.length)
    const [count, afterCount] = decodeUvarint(raw, cursor)
    cursor = afterCount
    if (count !== fn.inputs.length) {
      decoded.args = fn.inputs.map((input, index) => ({
        name: input.name,
        type: input.type,
        value: index < count ? '(decode skipped: arg count mismatch)' : '(missing)'
      }))
      return decoded
    }

    const args: DecodedValue[] = []
    for (const input of fn.inputs) {
      const [value, next] = decodeByDesc(raw, parseType(input.type), cursor)
      args.push({ name: input.name, type: input.type, value })
      cursor = next
    }
    decoded.args = args
    return decoded
  } catch {
    return decoded
  }
}

export function decodeEventsWithAbi(logsLike: unknown, abiLike: unknown): DecodedEvent[] {
  if (!Array.isArray(logsLike)) return []
  const abi = normalizeAbi(abiLike)
  if (!abi.events.length) return []

  const events: DecodedEvent[] = []
  for (let index = 0; index < logsLike.length; index += 1) {
    const rawLog = logsLike[index]
    if (!rawLog || typeof rawLog !== 'object') continue
    const topics = Array.isArray((rawLog as any).topics) ? (rawLog as any).topics : []
    const topicHexes = topics.filter((entry: unknown): entry is string => typeof entry === 'string')
    const dataHex = typeof (rawLog as any).data === 'string' ? (rawLog as any).data : '0x'
    const topic0 = topicHexes[0]
    if (!topic0) continue

    const eventDef = abi.events.find((candidate) => !candidate.anonymous && eventTopicHex(candidate) === topic0)
    if (!eventDef) continue

    const args: DecodedValue[] = []
    let topicCursor = eventDef.anonymous ? 0 : 1
    let dataCursor = 0
    let dataBuf: Buffer<ArrayBufferLike> = Buffer.alloc(0)
    try {
      dataBuf = parseHex(dataHex)
    } catch {
      dataBuf = Buffer.alloc(0)
    }

    try {
      for (let paramIndex = 0; paramIndex < eventDef.inputs.length; paramIndex += 1) {
        const input = eventDef.inputs[paramIndex]
        if (input.indexed) {
          const topicValue = topicHexes[topicCursor]
          if (topicValue === undefined) throw new Error('missing indexed topic')
          args.push({
            name: input.name,
            type: input.type,
            value: decodeIndexedTopic(topicValue, input.type)
          })
          topicCursor += 1
        } else {
          const [value, next] = decodeByDesc(dataBuf, parseType(input.type), dataCursor)
          args.push({ name: input.name, type: input.type, value })
          dataCursor = next
        }
      }
    } catch {
      // keep partial payload below
    }

    events.push({
      index,
      name: eventDef.name,
      signature: eventSignature(eventDef),
      topic0,
      args,
      raw: rawLog
    })
  }
  return events
}

export function extractMethodSelector(inputHex: string | null | undefined): string | null {
  if (!inputHex || typeof inputHex !== 'string') return null
  try {
    const buf = parseHex(inputHex)
    if (!buf.length) return null
    return asHex(buf.subarray(0, Math.min(8, buf.length)))
  } catch {
    return null
  }
}

export function inferViewFunctionsFromAbi(abiLike: unknown): AbiFunction[] {
  const abi = normalizeAbi(abiLike)
  return abi.functions.filter((fn) => {
    const mut = String(fn.stateMutability || '').toLowerCase()
    return mut === 'view' || mut === 'pure' || mut === 'readonly'
  })
}

export function inferWriteFunctionsFromAbi(abiLike: unknown): AbiFunction[] {
  const abi = normalizeAbi(abiLike)
  return abi.functions.filter((fn) => {
    const mut = String(fn.stateMutability || '').toLowerCase()
    return !(mut === 'view' || mut === 'pure' || mut === 'readonly')
  })
}
