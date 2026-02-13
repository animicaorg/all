/**
 * Submit raw/signed transactions and await receipts.
 */

import type { UnsignedTx } from './build'
import { makeSignBytes, encodeSignedTx } from './encode'
import type { AlgorithmId, Signer } from '../wallet/signer'
import type { Receipt } from '../types/core'
import { bytesToHex } from '../utils/bytes'

export interface RpcClient {
  call<T = unknown>(method: string, params?: any): Promise<T>
}

export interface WaitOpts {
  pollIntervalMs?: number
  timeoutMs?: number
}

type BroadcastVariant = { method: string; params: unknown[] }
type Capabilities = { methods: string[]; positional: string[] }

const BROADCAST_VARIANTS: BroadcastVariant[] = [
  { method: 'tx.sendRawTransaction', params: [] },
  { method: 'tx_sendRawTransaction', params: [] },
  { method: 'tx.sendRawTransaction', params: [{ rawTx: '__RAW_TX__' }] },
  { method: 'tx_sendRawTransaction', params: [{ rawTx: '__RAW_TX__' }] },
  { method: 'tx.submitRawTransaction', params: [] },
  { method: 'tx2.sendRawTransaction', params: [] }
]

const TERMINAL_CODES = new Set([-32010, -32011, -32012])
const FALLBACK_CODES = new Set([-32602, -32601])
const capabilityCache = new WeakMap<RpcClient, Capabilities>()

function hexlify(b: Uint8Array): string {
  return '0x' + bytesToHex(b)
}

function normalizeRawTx(raw: Uint8Array | string): string {
  const hex = typeof raw === 'string' ? raw.trim() : hexlify(raw)
  if (!hex.startsWith('0x') && !hex.startsWith('0X')) {
    throw new Error('Invalid raw transaction: must start with 0x')
  }

  let payload = hex.slice(2)
  if (!/^[0-9a-f]*$/i.test(payload)) {
    throw new Error('Invalid raw transaction: must be hex')
  }
  if (payload.length === 0 || /^0+$/i.test(payload)) {
    throw new Error('Invalid raw transaction: payload too short')
  }
  if (payload.length % 2 !== 0) payload = `0${payload}`
  if (payload.length <= 2) throw new Error('Invalid raw transaction: payload too short')
  return `0x${payload.toLowerCase()}`
}

async function getCapabilities(client: RpcClient): Promise<Capabilities | undefined> {
  const cached = capabilityCache.get(client)
  if (cached) return cached

  try {
    const discover = await client.call<any>('rpc.discover', [])
    const methods = Array.isArray(discover?.methods)
      ? discover.methods.map((m: any) => m?.name).filter((x: unknown): x is string => typeof x === 'string')
      : []
    const positional = Array.isArray(discover?.methods)
      ? discover.methods.filter((m: any) => Array.isArray(m?.params)).map((m: any) => m.name).filter((x: unknown): x is string => typeof x === 'string')
      : []
    const parsed = { methods, positional }
    capabilityCache.set(client, parsed)
    return parsed
  } catch {
    try {
      const listMethods = await client.call<any>('rpc.listMethods', [])
      const methods = Array.isArray(listMethods) ? listMethods.filter((x: unknown): x is string => typeof x === 'string') : []
      const parsed = { methods, positional: [] }
      capabilityCache.set(client, parsed)
      return parsed
    } catch {
      return undefined
    }
  }
}

function rankVariants(capabilities: Capabilities | undefined, rawTx: string): BroadcastVariant[] {
  const hydrated = BROADCAST_VARIANTS.map((variant) => {
    const params = variant.params.length === 0 ? [rawTx] : [{ rawTx }]
    return { method: variant.method, params }
  })

  if (!capabilities) return hydrated
  const methodSet = new Set(capabilities.methods)
  const positionalSet = new Set(capabilities.positional)

  return hydrated
    .map((variant, idx) => {
      let score = 0
      if (methodSet.has(variant.method)) score += 100
      if (variant.params.length === 1 && typeof variant.params[0] === 'string' && positionalSet.has(variant.method)) score += 50
      score -= idx
      return { variant, score }
    })
    .sort((a, b) => b.score - a.score)
    .map((x) => x.variant)
}

function debugBroadcast(event: string, payload: Record<string, unknown>) {
  const g = globalThis as any
  const enabled = g?.process?.env?.ANIMICA_DEBUG_TX_BROADCAST === '1' || g?.__ANIMICA_DEBUG_TX_BROADCAST__ === true
  if (!enabled) return
  // eslint-disable-next-line no-console
  console.debug(`[animica-sdk][tx] ${event}`, payload)
}

function asRpcLikeError(error: unknown): { code?: number; message: string; data?: unknown } {
  const anyErr = error as any
  return {
    code: typeof anyErr?.code === 'number' ? anyErr.code : undefined,
    message: typeof anyErr?.message === 'string' ? anyErr.message : String(error),
    data: anyErr?.data
  }
}

function decorateTxError(err: { code?: number; message: string; data?: unknown }): Error {
  if (err.code === -32011) {
    const data = (err.data ?? {}) as any
    return new Error(`ChainId mismatch. Expected ${data.expectedChainId ?? data.expected_chain_id ?? 'unknown'}, detected ${data.detectedChainId ?? data.detected_chain_id ?? 'unknown'}.`)
  }
  if (err.code === -32012) {
    const data = (err.data ?? {}) as any
    return new Error(`Fee too low. Increase gas/fee and retry.${data?.fee ? ` Current fee: ${JSON.stringify(data.fee)}` : ''}`)
  }
  if (err.code === -32010) {
    return new Error('Transaction decode failed. Verify signed transaction bytes and retry.')
  }
  return new Error(err.message)
}

export async function sendRawTransaction(client: RpcClient, raw: Uint8Array | string): Promise<string> {
  const normalized = normalizeRawTx(raw)
  const capabilities = await getCapabilities(client)
  const attempts = rankVariants(capabilities, normalized)

  let lastErr: { code?: number; message: string; data?: unknown } | undefined

  for (const attempt of attempts) {
    try {
      debugBroadcast('attempt', {
        method: attempt.method,
        paramsShape: Array.isArray(attempt.params[0]) || typeof attempt.params[0] === 'object' ? 'object' : 'array',
        rawTx: `${normalized.slice(0, 10)}... (len=${normalized.length})`
      })
      const txHash = await client.call<string>(attempt.method, attempt.params)
      if (typeof txHash !== 'string' || !txHash.startsWith('0x')) {
        throw new Error('Unexpected sendRawTransaction response')
      }
      return txHash
    } catch (e) {
      const err = asRpcLikeError(e)
      lastErr = err
      debugBroadcast('error', { method: attempt.method, code: err.code, message: err.message })
      if (typeof err.code === 'number' && TERMINAL_CODES.has(err.code)) {
        throw decorateTxError(err)
      }
      if (!FALLBACK_CODES.has(err.code ?? Number.NaN)) {
        throw new Error(err.message)
      }
    }
  }

  throw new Error(lastErr?.message ?? 'Failed to broadcast transaction')
}

export async function getTransactionReceipt(client: RpcClient, txHash: string): Promise<Receipt | null> {
  return client.call<Receipt | null>('tx.getTransactionReceipt', [txHash])
}

export async function getTransactionByHash<Tx = unknown>(client: RpcClient, txHash: string): Promise<Tx | null> {
  return client.call<Tx | null>('tx.getTransactionByHash', [txHash])
}

export async function awaitReceipt(client: RpcClient, txHash: string, opts: WaitOpts = {}): Promise<Receipt> {
  const interval = Math.max(200, opts.pollIntervalMs ?? 1000)
  const timeout = Math.max(interval, opts.timeoutMs ?? 120_000)
  const deadline = Date.now() + timeout

  {
    const maybe = await getTransactionReceipt(client, txHash)
    if (maybe) return maybe
  }

  while (Date.now() < deadline) {
    await sleep(interval)
    const rec = await getTransactionReceipt(client, txHash)
    if (rec) return rec
  }
  throw new Error(`Timed out waiting for receipt of ${txHash}`)
}

export async function signSendAndWait(
  client: RpcClient,
  tx: UnsignedTx,
  signer: Pick<Signer, 'getPublicKey' | 'sign' | 'alg'>,
  wait: WaitOpts = {}
): Promise<{ txHash: string; receipt: Receipt }> {
  const signBytes = makeSignBytes(tx)
  const domain = text('animica/tx-sign/v1')
  const signature = await signer.sign(signBytes, domain)
  const publicKey = await signer.getPublicKey()
  const raw = encodeSignedTx(tx, signature, publicKey, signer.alg as AlgorithmId)
  const txHash = await sendRawTransaction(client, raw)
  const receipt = await awaitReceipt(client, txHash, wait)
  return { txHash, receipt }
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function text(s: string): Uint8Array {
  return new TextEncoder().encode(s)
}

export default {
  sendRawTransaction,
  getTransactionReceipt,
  getTransactionByHash,
  awaitReceipt,
  signSendAndWait
}
