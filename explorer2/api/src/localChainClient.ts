import { createHash } from 'node:crypto'
import { homedir } from 'node:os'
import path from 'node:path'
import Database from 'better-sqlite3'
import { bech32m } from 'bech32'
import * as cbor from 'cbor'

type BalanceTag = 'latest' | 'pending'

const PFX_HDR = Buffer.from([0x10])
const PFX_BLK = Buffer.from([0x11])
const PFX_HIX = Buffer.from([0x12])
const PFX_RXI = Buffer.from([0x22])
const PFX_META = Buffer.from([0x1f])

const META_HEAD_HASH = Buffer.concat([PFX_META, Buffer.from('head_hash')])
const META_HEAD_HEIGHT = Buffer.concat([PFX_META, Buffer.from('head_height')])

const PFX_ACC = Buffer.from([0x01])

const CANONICAL_ENCODER = new cbor.Encoder({ canonical: true })

function encodeCanonical(value: unknown): Buffer {
  if (typeof (cbor as { encodeCanonical?: (input: unknown) => Buffer }).encodeCanonical === 'function') {
    return (cbor as { encodeCanonical: (input: unknown) => Buffer }).encodeCanonical(value)
  }
  return CANONICAL_ENCODER.encode(value) as Buffer
}

function decodeCbor(buffer: Buffer): any {
  return (cbor as { decodeFirstSync: (input: Buffer) => unknown }).decodeFirstSync(buffer)
}

function u64be(value: number): Buffer {
  const big = BigInt(value)
  const bytes = Buffer.alloc(8)
  let cursor = big
  for (let i = 7; i >= 0; i -= 1) {
    bytes[i] = Number(cursor & 0xffn)
    cursor >>= 8n
  }
  return bytes
}

function fromU64be(buffer: Buffer): number {
  let value = 0n
  for (const byte of buffer) {
    value = (value << 8n) | BigInt(byte)
  }
  return Number(value)
}

function toHex(buffer: Buffer | Uint8Array): string {
  return `0x${Buffer.from(buffer).toString('hex')}`
}

function normalizeJson(value: unknown): unknown {
  if (typeof value === 'bigint') return value.toString()
  if (Buffer.isBuffer(value)) return toHex(value)
  if (value instanceof Uint8Array) return toHex(value)
  if (Array.isArray(value)) return value.map((entry) => normalizeJson(entry))
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>).map(([key, entry]) => [key, normalizeJson(entry)])
    return Object.fromEntries(entries)
  }
  return value
}

function decodeAddress(address: string): Buffer {
  const trimmed = address.trim()
  if (trimmed.toLowerCase().startsWith('system:')) {
    return Buffer.from(trimmed.toLowerCase(), 'utf8')
  }
  if (trimmed.toLowerCase().startsWith('anim')) {
    const decoded = bech32m.decode(trimmed)
    const payload = Buffer.from(bech32m.fromWords(decoded.words))
    if (payload.length !== 34) {
      throw new Error(`Invalid bech32 payload length: ${payload.length}`)
    }
    return payload.slice(2)
  }
  if (trimmed.startsWith('0x')) {
    return Buffer.from(trimmed.slice(2), 'hex')
  }
  return Buffer.from(trimmed, 'hex')
}

function formatTxValue(value: unknown): number | string | undefined {
  if (value === undefined || value === null) return undefined
  if (typeof value === 'bigint') return value.toString()
  if (typeof value === 'number') return value
  if (typeof value === 'string') return value
  return undefined
}

function txHashFromObject(txObject: unknown): string {
  const encoded = encodeCanonical(txObject)
  const digest = createHash('sha3-256').update(encoded).digest()
  return toHex(digest)
}

function formatTxView(
  txObject: Record<string, any>,
  txHashHex?: string,
  block: { hash?: string; height?: number; index?: number } = {}
): Record<string, any> {
  const txBody = (txObject.tx ?? txObject.body ?? txObject) as Record<string, any>
  const payload = txBody.payload as Record<string, any> | undefined
  const payloadValue = payload?.v ?? {}

  const fromRaw = txBody.from ?? txBody.sender
  const toRaw = payloadValue.to ?? txBody.to
  const valueRaw = payloadValue.amount ?? payloadValue.value ?? txBody.value
  const dataRaw = payloadValue.data ?? txBody.data

  const gas = typeof txBody.gas === 'object' ? txBody.gas?.limit : txBody.gas ?? txBody.gasLimit
  const gasPrice = typeof txBody.gas === 'object' ? txBody.gas?.price : txBody.gasPrice ?? txBody.tip

  return {
    hash: txHashHex ?? txHashFromObject(txObject),
    from: fromRaw ? (Buffer.isBuffer(fromRaw) ? toHex(fromRaw) : normalizeJson(fromRaw)) : undefined,
    to: toRaw ? (Buffer.isBuffer(toRaw) ? toHex(toRaw) : normalizeJson(toRaw)) : undefined,
    nonce: formatTxValue(txBody.nonce),
    gas: formatTxValue(gas),
    gasLimit: gas === undefined ? undefined : formatTxValue(gas),
    gasPrice: gasPrice === undefined ? undefined : formatTxValue(gasPrice),
    chainId: formatTxValue(txBody.chainId ?? txBody.chain_id),
    value: formatTxValue(valueRaw),
    data: dataRaw ? (Buffer.isBuffer(dataRaw) ? toHex(dataRaw) : normalizeJson(dataRaw)) : undefined,
    blockHash: block.hash ?? undefined,
    blockNumber: block.height ?? undefined,
    transactionIndex: block.index ?? undefined
  }
}

function formatReceipt(receipt: Record<string, any>, txHash: string, blockHash: string, height: number): Record<string, any> {
  const status = receipt.status === 1 ? 'REVERT' : receipt.status === 2 ? 'OOG' : 'SUCCESS'
  return {
    txHash,
    blockHash,
    blockNumber: height,
    status,
    gasUsed: formatTxValue(receipt.gasUsed),
    logs: normalizeJson(receipt.logs)
  }
}

export class LocalChainClient {
  private db: Database.Database

  constructor(dbPath: string) {
    this.db = new Database(dbPath, { readonly: true, fileMustExist: true })
  }

  private getKv(key: Buffer): Buffer | null {
    const row = this.db.prepare('SELECT v FROM kv WHERE k = ?').get(key) as { v?: Buffer } | undefined
    return row?.v ? Buffer.from(row.v) : null
  }

  private getHeaderByHash(hash: Buffer): Record<string, any> | null {
    const raw = this.getKv(Buffer.concat([PFX_HDR, hash]))
    if (!raw) return null
    return decodeCbor(raw) as Record<string, any>
  }

  private getBlockByHashBytes(hash: Buffer): Record<string, any> | null {
    const raw = this.getKv(Buffer.concat([PFX_BLK, hash]))
    if (!raw) return null
    return decodeCbor(raw) as Record<string, any>
  }

  async getHead(): Promise<Record<string, any>> {
    const headHeightRaw = this.getKv(META_HEAD_HEIGHT)
    const headHashRaw = this.getKv(META_HEAD_HASH)
    if (!headHeightRaw || !headHashRaw) {
      throw new Error('Head not found')
    }

    const height = fromU64be(headHeightRaw)
    const header = this.getHeaderByHash(headHashRaw)
    if (!header) {
      throw new Error('Head not found')
    }

    return {
      height,
      hash: toHex(headHashRaw),
      chainId: formatTxValue(header.chainId),
      time: formatTxValue(header.timestamp)
    }
  }

  async getBlockByNumber(
    heightInput: number | string,
    includeTxs = false,
    includeReceipts = false
  ): Promise<Record<string, any>> {
    const height = typeof heightInput === 'string' ? Number.parseInt(heightInput, 10) : heightInput
    const hash = this.getKv(Buffer.concat([PFX_HIX, u64be(height)]))
    if (!hash) {
      throw new Error('Block not found')
    }
    return this.getBlockByHash(toHex(hash), includeTxs, includeReceipts)
  }

  async getBlockByHash(hashHex: string, includeTxs = false, includeReceipts = false): Promise<Record<string, any>> {
    const hashBytes = Buffer.from(hashHex.replace(/^0x/, ''), 'hex')
    const block = this.getBlockByHashBytes(hashBytes)
    if (!block) {
      throw new Error('Block not found')
    }

    const header = normalizeJson(block.header ?? {}) as Record<string, any>
    header.hash = hashHex

    const txs = Array.isArray(block.txs) ? block.txs : []
    const txEntries = includeTxs
      ? txs.map((tx: Record<string, any>, index: number) =>
          formatTxView(tx, undefined, { hash: hashHex, height: header.height, index })
        )
      : Array.from({ length: txs.length }, () => ({}))

    return {
      header,
      txs: txEntries,
      receipts: includeReceipts ? normalizeJson(block.receipts) : undefined
    }
  }

  async getTransactionByHash(hashHex: string): Promise<Record<string, any>> {
    const hashBytes = Buffer.from(hashHex.replace(/^0x/, ''), 'hex')
    const raw = this.getKv(Buffer.concat([PFX_RXI, hashBytes]))
    if (!raw) {
      throw new Error('Transaction not found')
    }

    const pointer = decodeCbor(raw) as Record<string, any>
    const height = Number(pointer.h ?? pointer.height)
    const index = Number(pointer.i ?? pointer.index)
    const blockHash = pointer.b ? toHex(pointer.b as Buffer) : undefined
    if (!blockHash) {
      throw new Error('Transaction not found')
    }

    const block = await this.getBlockByHash(blockHash, true, true)
    const tx = Array.isArray(block.txs) ? block.txs[index] : undefined
    if (!tx) {
      throw new Error('Transaction not found')
    }

    return formatTxView(tx as Record<string, any>, hashHex, { hash: blockHash, height, index })
  }

  async getTransactionReceipt(hashHex: string): Promise<Record<string, any>> {
    const hashBytes = Buffer.from(hashHex.replace(/^0x/, ''), 'hex')
    const raw = this.getKv(Buffer.concat([PFX_RXI, hashBytes]))
    if (!raw) {
      throw new Error('Receipt not found')
    }

    const pointer = decodeCbor(raw) as Record<string, any>
    const height = Number(pointer.h ?? pointer.height)
    const index = Number(pointer.i ?? pointer.index)
    const blockHash = pointer.b ? toHex(pointer.b as Buffer) : undefined
    if (!blockHash) {
      throw new Error('Receipt not found')
    }

    const block = await this.getBlockByHash(blockHash, true)
    const receipts = block.receipts as Record<string, any>[] | undefined
    if (!receipts || !receipts[index]) {
      throw new Error('Receipt not found')
    }
    return formatReceipt(receipts[index], hashHex, blockHash, height)
  }

  async getBalance(address: string, _tag: BalanceTag = 'latest'): Promise<string> {
    const addrBytes = decodeAddress(address)
    const key = Buffer.concat([PFX_ACC, Buffer.from([addrBytes.length]), addrBytes])
    const raw = this.getKv(key)
    if (!raw) return '0x0'
    const account = decodeCbor(raw) as { balance?: number | bigint }
    const balance = account.balance ?? 0
    const value = typeof balance === 'bigint' ? balance : BigInt(balance)
    return `0x${value.toString(16)}`
  }
}

export class HybridChainClient {
  constructor(
    private local: LocalChainClient,
    private rpc: {
      getHead: () => Promise<unknown>
      getBlockByNumber: (height: number | string, includeTxs?: boolean, includeReceipts?: boolean) => Promise<unknown>
      getBlockByHash: (hash: string, includeTxs?: boolean, includeReceipts?: boolean) => Promise<unknown>
      getTransactionByHash: (hash: string) => Promise<unknown>
      getTransactionReceipt: (hash: string) => Promise<unknown>
      getBalance: (address: string, tag?: BalanceTag) => Promise<string>
      getMempoolPending: () => Promise<string[]>
      getMempoolStats: () => Promise<{ count: number; totalBytes: number; oldestAgeSec: number | null }>
      getPeers: () => Promise<unknown[]>
    } | null
  ) {}

  async getHead(): Promise<unknown> {
    try {
      return await this.local.getHead()
    } catch (error) {
      if (this.rpc) return this.rpc.getHead()
      throw error
    }
  }

  async getBlockByNumber(height: number | string, includeTxs = false, includeReceipts = false): Promise<unknown> {
    try {
      return await this.local.getBlockByNumber(height, includeTxs, includeReceipts)
    } catch (error) {
      if (this.rpc) return this.rpc.getBlockByNumber(height, includeTxs, includeReceipts)
      throw error
    }
  }

  async getBlockByHash(hash: string, includeTxs = false, includeReceipts = false): Promise<unknown> {
    try {
      return await this.local.getBlockByHash(hash, includeTxs, includeReceipts)
    } catch (error) {
      if (this.rpc) return this.rpc.getBlockByHash(hash, includeTxs, includeReceipts)
      throw error
    }
  }

  async getTransactionByHash(hash: string): Promise<unknown> {
    try {
      return await this.local.getTransactionByHash(hash)
    } catch (error) {
      if (this.rpc) return this.rpc.getTransactionByHash(hash)
      throw error
    }
  }

  async getTransactionReceipt(hash: string): Promise<unknown> {
    try {
      return await this.local.getTransactionReceipt(hash)
    } catch (error) {
      if (this.rpc) return this.rpc.getTransactionReceipt(hash)
      throw error
    }
  }

  async getBalance(address: string, tag: BalanceTag = 'latest'): Promise<string> {
    if (tag === 'pending' && this.rpc) {
      try {
        return await this.rpc.getBalance(address, tag)
      } catch (error) {
        return this.local.getBalance(address, tag)
      }
    }
    return this.local.getBalance(address, tag)
  }

  async getMempoolPending(): Promise<string[]> {
    if (!this.rpc) throw new Error('RPC unavailable')
    return this.rpc.getMempoolPending()
  }

  async getMempoolStats(): Promise<{ count: number; totalBytes: number; oldestAgeSec: number | null }> {
    if (!this.rpc) throw new Error('RPC unavailable')
    return this.rpc.getMempoolStats()
  }

  async getPeers(): Promise<unknown[]> {
    if (!this.rpc) throw new Error('RPC unavailable')
    return this.rpc.getPeers()
  }
}

export function defaultChainDbPath(chainId: number, dataRoot = path.join(homedir(), '.animica')): string {
  return path.join(dataRoot, `chain-${chainId}`, 'animica.db')
}
