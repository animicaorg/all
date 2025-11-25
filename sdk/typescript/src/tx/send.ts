/**
 * Submit raw/signed transactions and await receipts.
 *
 * Works with any JSON-RPC client that implements:
 *    client.call<T>(method: string, params?: any): Promise<T>
 *
 * RPC methods expected (see rpc/methods in the node):
 *  - tx.sendRawTransaction(hexCbor: string) -> txHash (0x…)
 *  - tx.getTransactionByHash(txHash: string) -> Tx | null
 *  - tx.getTransactionReceipt(txHash: string) -> Receipt | null
 */

import type { UnsignedTx } from './build'
import { makeSignBytes, encodeSignedTx } from './encode'
import type { AlgorithmId, Signer } from '../wallet/signer'
import type { Receipt } from '../types/core'
import { bytesToHex } from '../utils/bytes'

/** Minimal JSON-RPC caller contract used by this module. */
export interface RpcClient {
  call<T = unknown>(method: string, params?: any): Promise<T>
}

export interface WaitOpts {
  /** Polling interval in milliseconds (default 1000). */
  pollIntervalMs?: number
  /** Timeout in milliseconds (default 120_000). */
  timeoutMs?: number
}

/** Helper: hexify a byte array with 0x prefix. */
function hexlify(b: Uint8Array): string {
  return '0x' + bytesToHex(b)
}

/** Submit a raw signed transaction (CBOR) as hex to the node. Returns txHash. */
export async function sendRawTransaction(client: RpcClient, raw: Uint8Array | string): Promise<string> {
  const hex = typeof raw === 'string' ? raw : hexlify(raw)
  const txHash = await client.call<string>('tx.sendRawTransaction', [hex])
  if (typeof txHash !== 'string' || !txHash.startsWith('0x')) {
    throw new Error('Unexpected sendRawTransaction response')
  }
  return txHash
}

/** Get a transaction receipt once available, or null if pending/not found. */
export async function getTransactionReceipt(client: RpcClient, txHash: string): Promise<Receipt | null> {
  return client.call<Receipt | null>('tx.getTransactionReceipt', [txHash])
}

/** Get a transaction object by hash, or null if unknown. */
export async function getTransactionByHash<Tx = unknown>(client: RpcClient, txHash: string): Promise<Tx | null> {
  return client.call<Tx | null>('tx.getTransactionByHash', [txHash])
}

/**
 * Await a transaction receipt by polling until it appears or timeout occurs.
 * Returns the receipt (which contains status/gasUsed/logs/etc.) or throws on timeout.
 */
export async function awaitReceipt(client: RpcClient, txHash: string, opts: WaitOpts = {}): Promise<Receipt> {
  const interval = Math.max(200, opts.pollIntervalMs ?? 1000)
  const timeout = Math.max(interval, opts.timeoutMs ?? 120_000)
  const deadline = Date.now() + timeout

  // Fast path: check once before entering the loop
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

/**
 * High-level helper: sign an UnsignedTx with a PQ signer, submit it, and wait for the receipt.
 * Returns { txHash, receipt }.
 */
export async function signSendAndWait(
  client: RpcClient,
  tx: UnsignedTx,
  signer: Pick<Signer, 'getPublicKey' | 'sign' | 'alg'>,
  wait: WaitOpts = {}
): Promise<{ txHash: string; receipt: Receipt }> {
  const signBytes = makeSignBytes(tx)
  // Use the tx-signing domain as the signer context to ensure domain separation
  const domain = text('animica/tx-sign/v1')
  const signature = await signer.sign(signBytes, domain)
  const publicKey = await signer.getPublicKey()
  const raw = encodeSignedTx(tx, signature, publicKey, signer.alg as AlgorithmId)
  const txHash = await sendRawTransaction(client, raw)
  const receipt = await awaitReceipt(client, txHash, wait)
  return { txHash, receipt }
}

// ──────────────────────────────────────────────────────────────────────────────
// Tiny utils
// ──────────────────────────────────────────────────────────────────────────────

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
