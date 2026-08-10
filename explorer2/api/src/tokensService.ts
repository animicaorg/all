/**
 * Token tracker — auto-indexes ANM-20 tokens (AnimicaTokenStandard) as they
 * are deployed, decodes their `init` calls for metadata, reconstructs the
 * promoted set from ANMPROMO1 treasury transfers, and (when the node allows
 * contract execution) enriches tokens with live market data via `state.call`.
 *
 * Detection signals, in order of reliability on the current node:
 *  1. `init` CALL selector — a kind=2 tx whose calldata starts with the
 *     AnimicaTokenStandard init selector identifies its target as a token
 *     (the wallet launch flow always sends deploy → init). The init calldata
 *     also carries the token's name/symbol/decimals/supplies/metadata_uri, and
 *     is used even when the init tx reverted: the INTENT lives in calldata.
 *     Init calldata alone is NOT trusted — it is staged and applied only when
 *     the target has an independently recorded deploy and the init sender is
 *     that deploy's creator (see applyVerifiedInits), so foreign calldata can
 *     never create a token or overwrite a real token's metadata.
 *  2. Deploy manifest name === "AnimicaTokenStandard" (when the wire tx view
 *     carries the manifest — mempool/wire shapes do, persisted views don't).
 *  3. Deploy code sha3-256 === the standard contract.py hash (same caveat).
 *
 * DEX contracts (pair/router/factory) are recognized the same way and stored
 * with a non-'token' kind so they never appear in token listings.
 *
 * HONESTY: on mainnet contract execution is currently fail-closed, so
 * state.call for token views and pool reserves REVERTS. Every market field
 * (totalSupply, priceAnm, liquidityAnm, change24h, pairAddress, feeBps) stays
 * null until execution is live — errors are treated as "unavailable", never
 * fabricated.
 */

import { createHash } from 'node:crypto'
import { bech32m } from 'bech32'
import pino from 'pino'
import type { TokenInfo } from '@animica/explorer2-shared'
import type { ChainClient } from './service.js'
import { ExplorerStore, TokenProfileRow } from './explorerStore.js'
import { RpcClient } from './rpcClient.js'
import { canonicalAddressKey, normalizeAddress, normalizeBlockDetail, normalizeHead } from './normalize.js'
import { extractContractAddress, extractManifest, extractDeployCode, extractTxInputData } from './txClassifier.js'
import { decodeCallWithAbi } from './abiDecoder.js'
import { normalizeTxHash } from './txHash.js'

const log = pino({ name: 'token-tracker' })

// ── Standard contract fingerprints (verified against contracts/standards) ─────

/** sha3-256 of the standard contracts' raw contract.py bytes. */
const CODE_HASHES: Record<string, TokenContractKind> = {
  '51c1a0409abff3e31b725971920387f801d5698f183a3c952854b009205497d5': 'token',
  '4b373a3d29f07c27bc9f972627e8046cd591778aefbe1eb58fa967610f2e0f6f': 'dex_pair',
  '32b220144331a63076d8c6776952bef6a50604bfab143709b53404e721b84c21': 'dex_router',
  '3458219f461d2b9db1b2f2383f1fd67c4d484b90c702f087e9def79709cbe7bb': 'dex_factory'
}

const MANIFEST_NAMES: Record<string, TokenContractKind> = {
  animicatokenstandard: 'token',
  animicadexpair: 'dex_pair',
  animicadexrouter: 'dex_router',
  animicadexfactory: 'dex_factory'
}

/** sha3-256 of empty bytes — the node's tx view emits this when it could not
 *  recover the deploy payload, so it must be treated as "unknown", not "empty". */
const EMPTY_SHA3 = 'a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a'

type TokenContractKind = 'token' | 'dex_pair' | 'dex_router' | 'dex_factory'

/** Foundation treasury that receives ANMPROMO1 promotion payments. */
const FOUNDATION_TREASURY = 'anim1zqpsmegc0qcvzjfukm89xs0zeu3eqyyyel7kelehuszvwfarqypky2gr946ga'

const PROMO_TAG = 'ANMPROMO1|'
const ADDRESS_HRP = 'anim'
const NANO_PER_ANM = 1e9
/** Tokens live-enriched per tick (round-robin over the whole set). */
const MARKET_BATCH_PER_TICK = 24

// ── AnimicaTokenStandard init ABI (literal manifest types) ────────────────────

const TOKEN_ABI = {
  functions: [
    {
      name: 'init',
      stateMutability: 'nonpayable',
      inputs: [
        { name: 'name', type: 'bytes' },
        { name: 'symbol', type: 'bytes' },
        { name: 'decimals', type: 'int' },
        { name: 'owner', type: 'bytes' },
        { name: 'initial_supply', type: 'int' },
        { name: 'max_supply', type: 'int' },
        { name: 'mintable', type: 'bool' },
        { name: 'metadata_uri', type: 'bytes' },
        { name: 'freeze_authority', type: 'bytes' }
      ],
      outputs: []
    }
  ]
}

function sha3(data: Buffer | string): Buffer {
  return createHash('sha3-256').update(data).digest()
}

/** 8-byte animica:abi:v1 selector over the given LITERAL signature string. */
function selectorHex(signature: string): string {
  return sha3(`animica:abi:v1|${signature}`).subarray(0, 8).toString('hex')
}

// The chain hashes the literal manifest type strings (init(bytes,bytes,int,…)),
// but tolerate normalized encoders too (int→int256).
const TOKEN_INIT_SELECTORS = new Set([
  selectorHex('init(bytes,bytes,int,bytes,int,int,bool,bytes,bytes)'),
  selectorHex('init(bytes,bytes,int256,bytes,int256,int256,bool,bytes,bytes)')
])
const DEX_INIT_SELECTORS = new Map<string, TokenContractKind>([
  [selectorHex('init(bytes,bytes,int,bytes)'), 'dex_pair'],
  [selectorHex('init(bytes,bytes,int256,bytes)'), 'dex_pair'],
  [selectorHex('init(bytes,bytes)'), 'dex_router'],
  [selectorHex('init(bytes,bytes,int,int,bytes)'), 'dex_factory'],
  [selectorHex('init(bytes,bytes,int256,int256,bytes)'), 'dex_factory']
])

// ── small byte / hex helpers ──────────────────────────────────────────────────

function hexToBuf(value: string | null | undefined): Buffer | null {
  if (typeof value !== 'string') return null
  const raw = value.trim().replace(/^0x/i, '')
  if (!raw.length) return Buffer.alloc(0)
  if (raw.length % 2 !== 0 || /[^0-9a-f]/i.test(raw)) return null
  return Buffer.from(raw, 'hex')
}

function utf8FromHex(value: string | null | undefined): string | null {
  const buf = hexToBuf(value ?? null)
  if (!buf || !buf.length) return null
  const text = buf.toString('utf-8')
  // Reject binary garbage (invalid UTF-8 shows up as replacement chars).
  if (text.includes('�')) return null
  return text
}

/** Encode a 32-byte digest as the canonical contract bech32m (alg_id 0x0000). */
function digestToContractAddress(digest: Buffer): string | null {
  if (digest.length !== 32) return null
  const payload = Buffer.concat([Buffer.from([0x00, 0x00]), digest])
  try {
    return bech32m.encode(ADDRESS_HRP, bech32m.toWords(payload), 128).toLowerCase()
  } catch {
    return null
  }
}

/** bech32m/hex address → 32-byte digest (drops the 2-byte alg prefix). */
function addressToDigest(value: string | null | undefined): Buffer | null {
  if (typeof value !== 'string' || !value.trim().length) return null
  const trimmed = value.trim()
  if (/^anim1/i.test(trimmed)) {
    try {
      const decoded = bech32m.decode(trimmed.toLowerCase(), 128)
      const payload = Buffer.from(bech32m.fromWords(decoded.words))
      if (payload.length === 34) return payload.subarray(2)
      if (payload.length === 32) return payload
      return null
    } catch {
      return null
    }
  }
  const buf = hexToBuf(trimmed)
  if (!buf) return null
  if (buf.length === 34) return buf.subarray(2)
  if (buf.length === 32) return buf
  return null
}

/**
 * Canonical token index key: lowercase 32-byte digest hex without 0x.
 * Strictly digest-shaped — undecodable input (e.g. a junk address inside an
 * ANMPROMO1 memo) yields null instead of a garbage key, so it can never
 * create a bogus token row.
 */
function addrKeyOf(value: string | null | undefined): string | null {
  const digest = addressToDigest(value ?? null)
  if (digest) return digest.toString('hex')
  const key = canonicalAddressKey(value ?? null)
  if (!key) return null
  const normalized = key.replace(/^0x/, '').toLowerCase()
  return /^[0-9a-f]{64}$/.test(normalized) ? normalized : null
}

function displayContractAddress(value: string | null | undefined): string | null {
  const digest = addressToDigest(value ?? null)
  if (!digest) return null
  return digestToContractAddress(digest)
}

// ── animica:abi:v1 view-call encoding (calls only need bytes args) ────────────

function uvarint(n: number): Buffer {
  const out: number[] = []
  let v = n >>> 0
  for (;;) {
    const b = v & 0x7f
    v >>>= 7
    if (v !== 0) out.push(b | 0x80)
    else {
      out.push(b)
      break
    }
  }
  return Buffer.from(out)
}

function encodeBytesArg(data: Buffer): Buffer {
  return Buffer.concat([uvarint(data.length), data])
}

/** selector(8) ++ uvarint(argc) ++ encoded bytes args, as 0x-hex calldata. */
function encodeViewCall(signature: string, bytesArgs: Buffer[] = []): string {
  const parts: Buffer[] = [Buffer.from(selectorHex(signature), 'hex'), uvarint(bytesArgs.length)]
  for (const arg of bytesArgs) parts.push(encodeBytesArg(arg))
  return `0x${Buffer.concat(parts).toString('hex')}`
}

function readUvarint(buf: Buffer, offset: number): [number, number] {
  let value = 0
  let shift = 0
  let i = offset
  for (let n = 0; n < 10; n += 1) {
    if (i >= buf.length) throw new Error('truncated uvarint')
    const b = buf[i]
    i += 1
    value |= (b & 0x7f) << shift
    if ((b & 0x80) === 0) return [value >>> 0, i]
    shift += 7
  }
  throw new Error('uvarint too large')
}

/**
 * Decode a view return payload: uvarint(count) ++ encoded outputs.
 * Supports the scalar types our views use: 'int' (sign+magnitude, varint len)
 * and 'bytes' (varint len + raw).
 */
function decodeReturnValues(payload: Buffer, types: Array<'int' | 'bytes'>): Array<bigint | Buffer> | null {
  try {
    if (!payload.length) return null
    let [count, cursor] = readUvarint(payload, 0)
    if (count !== types.length) return null
    const out: Array<bigint | Buffer> = []
    for (const type of types) {
      const [len, afterLen] = readUvarint(payload, cursor)
      cursor = afterLen
      if (cursor + len > payload.length) return null
      const chunk = payload.subarray(cursor, cursor + len)
      cursor += len
      if (type === 'bytes') {
        out.push(Buffer.from(chunk))
      } else {
        if (!chunk.length) return null
        const magnitude = chunk.subarray(1)
        const value = magnitude.length ? BigInt(`0x${magnitude.toString('hex')}`) : 0n
        out.push(chunk[0] === 0x01 ? -value : value)
      }
    }
    return out
  } catch {
    return null
  }
}

// ── manifest / deploy helpers ─────────────────────────────────────────────────

function parseManifestName(manifestLike: unknown): string | null {
  let candidate: unknown = manifestLike
  if (typeof candidate === 'string') {
    const asUtf8 = utf8FromHex(candidate) ?? candidate
    try {
      candidate = JSON.parse(asUtf8)
    } catch {
      return null
    }
  }
  if (candidate && typeof candidate === 'object') {
    const name = (candidate as Record<string, unknown>).name
    if (typeof name === 'string' && name.trim().length) return name.trim()
  }
  return null
}

function deployCodeHash(rawTx: unknown): string | null {
  // 1. Actual code bytes on the wire (mempool-style shapes).
  const code = extractDeployCode(rawTx)
  if (typeof code === 'string' && code.trim().length) {
    const buf = hexToBuf(code)
    const payload = buf && buf.length ? buf : Buffer.from(code, 'utf-8')
    if (payload.length) return sha3(payload).toString('hex')
  }
  // 2. The node's deploy-metadata codeHash (persisted views). The empty-bytes
  //    hash means the node could not recover the payload — treat as unknown.
  const wireHash = (rawTx as any)?.codeHash ?? (rawTx as any)?.code_hash
  if (typeof wireHash === 'string') {
    const normalized = wireHash.trim().toLowerCase().replace(/^0x/, '')
    if (/^[0-9a-f]{64}$/.test(normalized) && normalized !== EMPTY_SHA3) return normalized
  }
  return null
}

function extractTxKind(rawTx: unknown): number | null {
  const tx = rawTx as any
  const kind = tx?.kind ?? tx?.tx_kind ?? tx?.tx?.kind ?? tx?.body?.kind
  if (typeof kind === 'number' && Number.isInteger(kind)) return kind
  if (typeof kind === 'string' && /^[0-9]+$/.test(kind)) return Number(kind)
  return null
}

function extractTo(rawTx: unknown): string | null {
  const tx = rawTx as any
  const to = tx?.to ?? tx?.tx?.to ?? tx?.body?.to ?? tx?.payload?.v?.to ?? tx?.tx?.payload?.v?.to
  return typeof to === 'string' && to.trim().length ? to.trim() : null
}

function extractTxHashOf(rawTx: unknown): string | null {
  const tx = rawTx as any
  const hash = tx?.hash ?? tx?.txHash ?? tx?.tx_hash
  if (typeof hash !== 'string' || !hash.trim().length) return null
  try {
    return normalizeTxHash(hash)
  } catch {
    return null
  }
}

function receiptFailed(receipt: unknown): boolean {
  if (!receipt || typeof receipt !== 'object') return false
  const status = (receipt as any).status
  if (typeof status === 'string') {
    const upper = status.toUpperCase()
    return upper === 'REVERT' || upper === 'OOG' || upper === 'FAILED'
  }
  // Animica ReceiptStatus IntEnum: SUCCESS=0, REVERT=1, OOG=2.
  return status === 1 || status === 2
}

function ipfsToGateway(uri: string): string {
  if (uri.startsWith('ipfs://')) return `https://ipfs.io/ipfs/${uri.slice('ipfs://'.length)}`
  return uri
}

function isFetchableUri(uri: string): boolean {
  return /^https?:\/\//i.test(uri) || uri.startsWith('ipfs://')
}

// ── decoded init call ─────────────────────────────────────────────────────────

interface DecodedInit {
  name: string | null
  symbol: string | null
  decimals: number | null
  initialSupply: string | null
  maxSupply: string | null
  mintable: boolean | null
  metadataUri: string | null
}

function decodeTokenInit(inputHex: string): DecodedInit | null {
  const decoded = decodeCallWithAbi(inputHex, TOKEN_ABI)
  if (!decoded || decoded.name !== 'init' || !Array.isArray(decoded.args) || decoded.args.length !== 9) return null
  const argValue = (index: number): unknown => decoded.args?.[index]?.value
  const asString = (v: unknown): string | null => (typeof v === 'string' ? v : null)
  const asInt = (v: unknown): number | null => {
    if (typeof v !== 'string') return null
    const n = Number.parseInt(v, 10)
    return Number.isFinite(n) ? n : null
  }
  return {
    name: utf8FromHex(asString(argValue(0))),
    symbol: utf8FromHex(asString(argValue(1))),
    decimals: asInt(argValue(2)),
    initialSupply: asString(argValue(4)),
    maxSupply: asString(argValue(5)),
    mintable: typeof argValue(6) === 'boolean' ? (argValue(6) as boolean) : null,
    metadataUri: utf8FromHex(asString(argValue(7)))
  }
}

// ── the tracker ───────────────────────────────────────────────────────────────

export interface TokenTrackerOptions {
  chain: ChainClient
  store: ExplorerStore
  /** Only used for state.call live enrichment; omit in Local DB mode. */
  rpc?: RpcClient | null
  /** DEX factory address (env EXPLORER_DEX_FACTORY); empty disables pools. */
  dexFactory?: string
  /** How far back the one-time backfill walks (0 = to genesis). */
  scanDepth?: number
  /** Max blocks fetched per tick (forward + backfill combined budget). */
  blocksPerTick?: number
}

export class TokenTracker {
  private chain: ChainClient
  private store: ExplorerStore
  private rpc: RpcClient | null
  private dexFactory: string
  private scanDepth: number
  private blocksPerTick: number

  private ticking = false
  private lastTickAt = 0
  private timer: NodeJS.Timeout | null = null
  private intervalMs = 20_000
  private metaFailures = new Map<string, number>()

  constructor(options: TokenTrackerOptions) {
    this.chain = options.chain
    this.store = options.store
    this.rpc = options.rpc ?? null
    this.dexFactory = (options.dexFactory ?? '').trim()
    this.scanDepth = Math.max(0, options.scanDepth ?? 0)
    this.blocksPerTick = Math.max(10, options.blocksPerTick ?? 240)
  }

  /** Start the background scan loop. Never throws; never crashes the server. */
  start(intervalMs = 20_000): void {
    this.intervalMs = Math.max(2_000, intervalMs)
    void this.tick()
    this.timer = setInterval(() => void this.tick(), this.intervalMs)
    this.timer.unref?.()
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer)
    this.timer = null
  }

  /** Lazily kick a scan if the loop has stalled (also called from routes). */
  ensureFresh(): void {
    if (this.ticking) return
    if (Date.now() - this.lastTickAt < this.intervalMs * 2) return
    void this.tick()
  }

  async tick(): Promise<void> {
    if (this.ticking) return
    this.ticking = true
    try {
      const headRaw = await this.chain.getHead()
      const head = normalizeHead(headRaw)
      let budget = this.blocksPerTick

      budget = await this.scanForward(head.height, budget)
      await this.scanBackfill(head.height, budget)
      this.reconcileCreationInfo()
      this.applyVerifiedInits()
      this.refreshPromoFlags()
      await this.fetchPendingMetadata()
      await this.refreshMarketData()
    } catch (error) {
      log.warn({ err: error instanceof Error ? error.message : String(error) }, 'token scan tick failed')
    } finally {
      this.lastTickAt = Date.now()
      this.ticking = false
    }
  }

  // ── block scanning ─────────────────────────────────────────────────────────

  /** Follow the head; on first run, initialize cursors at the current head. */
  private async scanForward(headHeight: number, budget: number): Promise<number> {
    const lastRaw = this.store.getTokenScanState('last_scanned')
    if (lastRaw === null) {
      await this.processHeight(headHeight)
      this.store.setTokenScanState('last_scanned', String(headHeight))
      this.store.setTokenScanState('backfill_cursor', String(headHeight))
      return budget - 1
    }
    let last = Number.parseInt(lastRaw, 10)
    if (!Number.isFinite(last)) last = headHeight
    let used = 0
    while (last < headHeight && used < budget) {
      last += 1
      used += 1
      await this.processHeight(last)
      this.store.setTokenScanState('last_scanned', String(last))
    }
    return budget - used
  }

  /** One-time historical walk from the first-seen head down to the floor. */
  private async scanBackfill(headHeight: number, budget: number): Promise<void> {
    if (budget <= 0) return
    const cursorRaw = this.store.getTokenScanState('backfill_cursor')
    if (cursorRaw === null) return
    let cursor = Number.parseInt(cursorRaw, 10)
    if (!Number.isFinite(cursor)) return
    const floor = this.scanDepth > 0 ? Math.max(0, headHeight - this.scanDepth) : 0
    let used = 0
    while (cursor > floor && used < budget) {
      cursor -= 1
      used += 1
      await this.processHeight(cursor)
      this.store.setTokenScanState('backfill_cursor', String(cursor))
    }
  }

  private async processHeight(height: number): Promise<void> {
    let raw: unknown
    try {
      raw = await this.chain.getBlockByNumber(height, true, true)
    } catch {
      return
    }
    if (!raw) return
    try {
      this.processBlock(raw)
    } catch (error) {
      log.warn({ height, err: error instanceof Error ? error.message : String(error) }, 'token scan block failed')
    }
  }

  private processBlock(rawBlock: unknown): void {
    const detail = normalizeBlockDetail(rawBlock)
    const txs = extractArray(rawBlock, ['txs', 'transactions'])
    if (!txs.length) return
    const receipts = extractArray(rawBlock, ['receipts', 'receiptResults'])
    const blockTime = Number.isFinite(detail.time) && detail.time > 0 ? detail.time : null

    for (let i = 0; i < txs.length; i += 1) {
      const tx = txs[i]
      const receipt = matchReceipt(tx, i, receipts)
      const kind = extractTxKind(tx)
      const deployedAddress = extractContractAddress(receipt, tx)

      if (kind === 1 || deployedAddress) {
        this.handleDeploy(tx, deployedAddress, detail.height, blockTime)
        continue
      }
      if (kind === 2) {
        this.handleCall(tx, detail.height, blockTime)
        continue
      }
      if (kind === 0) {
        this.handlePossiblePromo(tx, receipt, blockTime)
        continue
      }
      // Unknown wire shape: try both interpretations defensively.
      this.handleCall(tx, detail.height, blockTime)
      this.handlePossiblePromo(tx, receipt, blockTime)
    }
  }

  private handleDeploy(rawTx: unknown, deployedAddress: string | null, height: number, blockTime: number | null): void {
    const address = deployedAddress ?? extractContractAddress(null, rawTx)
    const addrKey = addrKeyOf(address)
    if (!addrKey) return
    const display = displayContractAddress(address) ?? address ?? addrKey
    const creator = normalizeAddress((rawTx as any)?.from ?? (rawTx as any)?.sender) ?? null
    const txHash = extractTxHashOf(rawTx)
    const manifestName = parseManifestName(extractManifest(rawTx))
    const codeHash = deployCodeHash(rawTx)

    this.store.upsertTokenDeploy({
      addrKey,
      address: display,
      creator,
      creationHeight: height,
      creationTx: txHash,
      creationTs: blockTime,
      codeHash,
      manifestName
    })

    // Identify the contract kind when the wire carried enough evidence.
    let contractKind: TokenContractKind | null = null
    if (manifestName && MANIFEST_NAMES[manifestName.toLowerCase()]) {
      contractKind = MANIFEST_NAMES[manifestName.toLowerCase()]
    } else if (codeHash && CODE_HASHES[codeHash]) {
      contractKind = CODE_HASHES[codeHash]
    }
    if (!contractKind) return

    this.store.upsertTokenProfile({
      address: display,
      addrKey,
      kind: contractKind,
      creator,
      creationHeight: height,
      creationTx: txHash,
      creationTs: blockTime
    })
  }

  private handleCall(rawTx: unknown, height: number, blockTime: number | null): void {
    const input = extractTxInputData(rawTx)
    if (!input || input === '0x') return
    const buf = hexToBuf(input)
    if (!buf || buf.length < 8) return
    const selector = buf.subarray(0, 8).toString('hex')

    const to = extractTo(rawTx)
    const addrKey = addrKeyOf(to)
    if (!addrKey) return

    // Init calldata is UNTRUSTED on its own: anyone can send init-shaped
    // calldata at any address to spoof metadata. Stage it here and let
    // applyVerifiedInits() apply it only once the target has an independently
    // recorded deploy AND the init sender equals that deploy's creator.
    const txHash = extractTxHashOf(rawTx)
    if (!txHash) return
    const senderKey = addrKeyOf((rawTx as any)?.from ?? (rawTx as any)?.sender)

    if (TOKEN_INIT_SELECTORS.has(selector)) {
      const init = decodeTokenInit(input)
      if (!init) return
      this.store.upsertTokenInitSeen({
        txHash,
        addrKey,
        senderKey,
        kind: 'token',
        height,
        name: init.name,
        symbol: init.symbol,
        decimals: init.decimals,
        initialSupply: init.initialSupply,
        maxSupply: init.maxSupply,
        mintable: init.mintable,
        metadataUri: init.metadataUri && init.metadataUri.length ? init.metadataUri : null
      })
      return
    }

    const dexKind = DEX_INIT_SELECTORS.get(selector)
    if (dexKind) {
      this.store.upsertTokenInitSeen({ txHash, addrKey, senderKey, kind: dexKind, height })
    }
    void blockTime
  }

  private handlePossiblePromo(rawTx: unknown, receipt: unknown, blockTime: number | null): void {
    const to = extractTo(rawTx)
    if (!to) return
    const toKey = addrKeyOf(to)
    if (!toKey || toKey !== this.treasuryKey) return
    const dataHex = extractTxInputData(rawTx)
    if (!dataHex || dataHex === '0x') return
    const memo = utf8FromHex(dataHex)
    if (!memo || !memo.startsWith(PROMO_TAG)) return
    if (receiptFailed(receipt)) return

    const parts = memo.split('|')
    // ANMPROMO1|<contract>|<days>|<label?>
    if (parts.length < 3) return
    const contract = parts[1]?.trim()
    const days = Number.parseInt(parts[2] ?? '', 10)
    const label = parts.slice(3).join('|').trim() || null
    const tokenKey = addrKeyOf(contract)
    const txHash = extractTxHashOf(rawTx)
    if (!tokenKey || !txHash) return
    if (!Number.isFinite(days) || days <= 0 || days > 3650) return
    const startTs = blockTime ?? Math.floor(Date.now() / 1000)

    this.store.upsertTokenPromo({ txHash, addrKey: tokenKey, startTs, days, label })
    // Make sure the promoted token exists in the index (metadata may follow).
    const display = displayContractAddress(contract) ?? contract
    if (!this.store.getTokenProfileByKey(tokenKey)) {
      this.store.upsertTokenProfile({ address: display, addrKey: tokenKey, kind: 'token' })
    }
    log.info({ token: display, days, label }, 'indexed ANMPROMO1 promotion')
  }

  private get treasuryKey(): string {
    return (this._treasuryKey ??= addrKeyOf(FOUNDATION_TREASURY) ?? '')
  }
  private _treasuryKey: string | null = null

  // ── post-scan passes ───────────────────────────────────────────────────────

  /** Copy deploy info onto tokens whose init was seen before their deploy tx. */
  private reconcileCreationInfo(): void {
    for (const addrKey of this.store.listTokenKeysMissingCreation()) {
      const deploy = this.store.getTokenDeploy(addrKey)
      if (!deploy?.creationTx) continue
      const row = this.store.getTokenProfileByKey(addrKey)
      if (!row) continue
      this.store.upsertTokenProfile({
        address: row.address,
        addrKey,
        kind: row.kind,
        creator: deploy.creator,
        creationHeight: deploy.creationHeight,
        creationTx: deploy.creationTx,
        creationTs: deploy.creationTs
      })
    }
  }

  /**
   * Apply staged init calls whose sender is the recorded deployer of the
   * target contract. A profile is written only when (a) the address has an
   * independently recorded deploy in token_deploy and (b) the init sender
   * equals that deploy's creator — a foreign init can never create a token or
   * overwrite one. Every staged init is mined (we only scan blocks); reverted
   * inits from the deployer still count, since the INTENT is in the calldata.
   * The earliest deployer init wins (deterministic: height, then tx hash).
   */
  private applyVerifiedInits(): void {
    for (const addrKey of this.store.listInitSeenAddrKeys()) {
      const deploy = this.store.getTokenDeploy(addrKey)
      if (!deploy?.creationTx) continue // deploy not seen (yet) — keep staged
      const deployerKey = addrKeyOf(deploy.creator)
      if (!deployerKey) continue // cannot bind without a known deployer
      const chosen = this.store.listTokenInitCandidates(addrKey).find((c) => c.senderKey === deployerKey)
      if (!chosen) continue
      // The deploy fingerprint (manifest name / code hash) outranks init
      // calldata — a contract fingerprinted as one kind cannot be re-labeled.
      const fingerprintKind =
        (deploy.manifestName ? MANIFEST_NAMES[deploy.manifestName.toLowerCase()] : undefined) ??
        (deploy.codeHash ? CODE_HASHES[deploy.codeHash.replace(/^0x/, '').toLowerCase()] : undefined) ??
        null
      if (fingerprintKind && fingerprintKind !== chosen.kind) continue
      const existing = this.store.getTokenProfileByKey(addrKey)
      if (existing?.initTx === chosen.txHash) continue // already applied
      const display = displayContractAddress(addrKey) ?? addrKey
      this.store.upsertTokenProfile({
        address: existing?.address ?? display,
        addrKey,
        kind: chosen.kind,
        name: chosen.name,
        symbol: chosen.symbol,
        decimals: chosen.decimals,
        initialSupply: chosen.initialSupply,
        maxSupply: chosen.maxSupply,
        mintable: chosen.mintable,
        metadataUri: chosen.metadataUri,
        creator: deploy.creator,
        creationHeight: deploy.creationHeight,
        creationTx: deploy.creationTx,
        creationTs: deploy.creationTs,
        initTx: chosen.txHash
      })
      if (chosen.kind === 'token') {
        log.info({ token: display, name: chosen.name, symbol: chosen.symbol, initTx: chosen.txHash }, 'indexed token init (deployer-verified)')
      }
    }
  }

  /**
   * Recompute promoted/promoDaysLeft columns from ANMPROMO1 windows. Windows
   * are ADDITIVE — the wallet promises "New periods extend the run" — so a
   * deposit made while a promo is running starts at the current end rather
   * than its own block time, and total featured time equals the sum of
   * purchased days. Deterministic from chain data (deposits ordered by block
   * time, then tx hash).
   */
  private refreshPromoFlags(): void {
    const now = Math.floor(Date.now() / 1000)
    for (const { addrKey } of this.store.listAllTokenKeys()) {
      const end = chainedPromoEnd(this.store.listTokenPromos(addrKey))
      if (end !== null && end > now) {
        this.store.setTokenPromoFlags(addrKey, true, Math.ceil((end - now) / 86400))
      } else {
        this.store.setTokenPromoFlags(addrKey, false, null)
      }
    }
  }

  /** Best-effort metadata_uri JSON fetch (image/description/links). */
  private async fetchPendingMetadata(): Promise<void> {
    const now = Math.floor(Date.now() / 1000)
    // Retry failures every 6 hours; successful fetches refresh daily.
    const rows = this.store.listTokensNeedingMetadataFetch(now - 86_400, 4)
    for (const row of rows) {
      const uri = row.metadataUri
      if (!uri || !isFetchableUri(uri)) {
        this.store.setTokenMetaFetchedAt(row.addrKey, now)
        continue
      }
      const failures = this.metaFailures.get(row.addrKey) ?? 0
      if (failures >= 5) {
        this.store.setTokenMetaFetchedAt(row.addrKey, now)
        continue
      }
      try {
        const response = await fetch(ipfsToGateway(uri), {
          signal: AbortSignal.timeout(6_000),
          headers: { accept: 'application/json' }
        })
        if (!response.ok) throw new Error(`http ${response.status}`)
        const text = (await response.text()).slice(0, 128 * 1024)
        const json = JSON.parse(text) as Record<string, unknown>
        const image = firstString(json, ['image', 'imageUrl', 'image_url', 'logo', 'icon'])
        const description = firstString(json, ['description', 'about'])
        const links = extractLinks(json)
        this.store.upsertTokenProfile({
          address: row.address,
          addrKey: row.addrKey,
          kind: row.kind,
          imageUrl: image ? ipfsToGateway(image) : null,
          description,
          links: Object.keys(links).length ? links : null,
          metaFetchedAt: now
        })
        this.metaFailures.delete(row.addrKey)
      } catch (error) {
        this.metaFailures.set(row.addrKey, failures + 1)
        // Mark attempted so we do not hammer a dead URI; retried after cutoff.
        this.store.setTokenMetaFetchedAt(row.addrKey, now - 86_400 + 21_600)
        log.debug({ token: row.address, uri, err: error instanceof Error ? error.message : String(error) }, 'metadata fetch failed')
      }
    }
  }

  // ── live market enrichment (state.call; unavailable ⇒ nulls) ───────────────

  private async stateCall(to: string, dataHex: string): Promise<Buffer | null> {
    if (!this.rpc) return null
    try {
      const result = await this.rpc.call<unknown>('state.call', [{ to, data: dataHex }])
      if (typeof result !== 'string') return null
      return hexToBuf(result)
    } catch {
      // Execution disabled / revert / missing contract — honest "unavailable".
      return null
    }
  }

  private async refreshMarketData(): Promise<void> {
    if (!this.rpc) return
    const now = Math.floor(Date.now() / 1000)
    // Round-robin over ALL tokens with a persisted cursor, so every token —
    // not just the top slice of the default ordering — gets market fields and
    // price-history points over successive ticks.
    const keys = this.store.listAllTokenKeys()
    if (!keys.length) return
    const batchSize = Math.min(MARKET_BATCH_PER_TICK, keys.length)
    let cursor = Number.parseInt(this.store.getTokenScanState('market_cursor') ?? '0', 10)
    if (!Number.isFinite(cursor) || cursor < 0) cursor = 0
    cursor %= keys.length
    for (let i = 0; i < batchSize; i += 1) {
      const row = this.store.getTokenProfileByKey(keys[(cursor + i) % keys.length].addrKey)
      if (!row) continue
      try {
        await this.refreshTokenMarket(row, now)
      } catch (error) {
        log.debug({ token: row.address, err: error instanceof Error ? error.message : String(error) }, 'market refresh failed')
      }
    }
    this.store.setTokenScanState('market_cursor', String((cursor + batchSize) % keys.length))
  }

  private async refreshTokenMarket(row: TokenProfileRow, now: number): Promise<void> {
    // total_supply() -> int
    const supplyRet = await this.stateCall(row.address, encodeViewCall('total_supply()'))
    let totalSupply: string | null = null
    if (supplyRet) {
      const values = decodeReturnValues(supplyRet, ['int'])
      if (values && typeof values[0] === 'bigint') totalSupply = values[0].toString()
    }

    let priceAnm: number | null = null
    let liquidityAnm: number | null = null
    let pairAddress: string | null = null
    let feeBps: number | null = null
    let change24h: number | null = null

    if (this.dexFactory) {
      const tokenDigest = addressToDigest(row.address)
      if (tokenDigest) {
        // Native ANM is represented as empty bytes in the DEX contracts.
        const anm = Buffer.alloc(0)
        let pairDigest = await this.callGetPair(anm, tokenDigest)
        if (!pairDigest) pairDigest = await this.callGetPair(tokenDigest, anm)
        if (pairDigest) {
          pairAddress = digestToContractAddress(pairDigest)
          if (pairAddress) {
            const market = await this.readPairMarket(pairAddress, row.decimals ?? 9)
            if (market) {
              priceAnm = market.priceAnm
              liquidityAnm = market.liquidityAnm
              feeBps = market.feeBps
            }
          }
        }
      }
    }

    if (priceAnm !== null && Number.isFinite(priceAnm) && priceAnm > 0) {
      this.store.insertTokenPricePoint(row.address, now, priceAnm)
      const dayAgo = this.store.getTokenPriceBefore(row.address, now - 86_400)
      if (dayAgo !== null && dayAgo > 0) {
        change24h = ((priceAnm - dayAgo) / dayAgo) * 100
      }
    }

    if (totalSupply !== null || priceAnm !== null || pairAddress !== null) {
      this.store.upsertTokenProfile({
        address: row.address,
        addrKey: row.addrKey,
        kind: row.kind,
        totalSupply,
        priceAnm,
        liquidityAnm,
        change24h,
        pairAddress,
        feeBps
      })
    }
  }

  private async callGetPair(a: Buffer, b: Buffer): Promise<Buffer | null> {
    const ret = await this.stateCall(this.dexFactory, encodeViewCall('get_pair(bytes,bytes)', [a, b]))
    if (!ret) return null
    const values = decodeReturnValues(ret, ['bytes'])
    const digest = values && Buffer.isBuffer(values[0]) ? (values[0] as Buffer) : null
    if (!digest || digest.length !== 32 || digest.every((byte) => byte === 0)) return null
    return digest
  }

  private async readPairMarket(
    pairAddress: string,
    tokenDecimals: number
  ): Promise<{ priceAnm: number; liquidityAnm: number; feeBps: number | null } | null> {
    const reservesRet = await this.stateCall(pairAddress, encodeViewCall('reserves()'))
    if (!reservesRet) return null
    const reserves = decodeReturnValues(reservesRet, ['int', 'int'])
    if (!reserves || typeof reserves[0] !== 'bigint' || typeof reserves[1] !== 'bigint') return null

    const token0Ret = await this.stateCall(pairAddress, encodeViewCall('token0()'))
    const token0 = token0Ret ? decodeReturnValues(token0Ret, ['bytes']) : null
    const token0Bytes = token0 && Buffer.isBuffer(token0[0]) ? (token0[0] as Buffer) : null
    // Native ANM side is stored as empty bytes; default to reserve0=ANM.
    const anmIsToken0 = token0Bytes === null || token0Bytes.length === 0 || token0Bytes.every((byte) => byte === 0)
    const anmReserve = anmIsToken0 ? (reserves[0] as bigint) : (reserves[1] as bigint)
    const tokenReserve = anmIsToken0 ? (reserves[1] as bigint) : (reserves[0] as bigint)
    if (anmReserve <= 0n || tokenReserve <= 0n) return null

    let feeBps: number | null = null
    const feeRet = await this.stateCall(pairAddress, encodeViewCall('fee_bps()'))
    if (feeRet) {
      const fee = decodeReturnValues(feeRet, ['int'])
      if (fee && typeof fee[0] === 'bigint') feeBps = Number(fee[0])
    }

    const anm = Number(anmReserve) / NANO_PER_ANM
    const tokens = Number(tokenReserve) / Math.pow(10, tokenDecimals)
    if (!Number.isFinite(anm) || !Number.isFinite(tokens) || tokens <= 0) return null
    return { priceAnm: anm / tokens, liquidityAnm: anm * 2, feeBps }
  }

  // ── read API ───────────────────────────────────────────────────────────────

  listTokens(limit: number): TokenInfo[] {
    this.ensureFresh()
    return this.store.listTokenProfiles(clamp(limit, 1, 500)).map((row) => this.toTokenInfo(row))
  }

  listFeatured(limit: number): TokenInfo[] {
    this.ensureFresh()
    return this.store.listPromotedTokenProfiles(clamp(limit, 1, 200)).map((row) => this.toTokenInfo(row))
  }

  search(query: string, limit: number): TokenInfo[] {
    this.ensureFresh()
    const trimmed = query.trim()
    if (!trimmed) return this.listTokens(limit)
    // An exact address (bech32m or hex) resolves through the canonical key.
    const key = addrKeyOf(trimmed)
    if (key) {
      const row = this.store.getTokenProfileByKey(key)
      if (row && row.kind === 'token') return [this.toTokenInfo(row)]
    }
    return this.store.searchTokenProfiles(trimmed, clamp(limit, 1, 200)).map((row) => this.toTokenInfo(row))
  }

  getToken(address: string): TokenInfo | null {
    this.ensureFresh()
    const key = addrKeyOf(address)
    if (!key) return null
    const row = this.store.getTokenProfileByKey(key)
    if (!row || row.kind !== 'token') return null
    return this.toTokenInfo(row)
  }

  getHistory(address: string, rangeSeconds: number): Array<{ t: number; priceAnm: number }> {
    const key = addrKeyOf(address)
    if (!key) return []
    const row = this.store.getTokenProfileByKey(key)
    if (!row) return []
    const since = rangeSeconds > 0 ? Math.floor(Date.now() / 1000) - rangeSeconds : 0
    return this.store.getTokenPriceHistory(row.address, since)
  }

  private toTokenInfo(row: TokenProfileRow): TokenInfo {
    return {
      address: row.address,
      name: row.name ?? '',
      symbol: row.symbol ?? '',
      decimals: row.decimals ?? 9,
      imageUrl: row.imageUrl,
      description: row.description,
      links: row.links ?? {},
      totalSupply: row.totalSupply,
      priceAnm: row.priceAnm,
      liquidityAnm: row.liquidityAnm,
      change24h: row.change24h,
      pairAddress: row.pairAddress,
      feeBps: row.feeBps,
      promoted: row.promoted,
      promoDaysLeft: row.promoted ? row.promoDaysLeft : null,
      metadataUri: row.metadataUri,
      initialSupply: row.initialSupply,
      maxSupply: row.maxSupply,
      mintable: row.mintable,
      creator: row.creator,
      creationHeight: row.creationHeight,
      creationTx: row.creationTx,
      creationTime: row.creationTs,
      holders: null,
      updatedAt: row.updatedAt
    }
  }
}

// ── misc helpers ──────────────────────────────────────────────────────────────

function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min
  return Math.max(min, Math.min(max, Math.floor(value)))
}

function extractArray(source: unknown, keys: string[]): any[] {
  for (const key of keys) {
    const value = (source as any)?.[key]
    if (Array.isArray(value)) return value
  }
  return []
}

function matchReceipt(tx: any, index: number, receipts: any[]): any | null {
  if (tx?.receipt) return tx.receipt
  const direct = receipts[index]
  if (direct) return direct
  const txHash = tx?.hash ?? tx?.txHash
  if (typeof txHash !== 'string') return null
  return (
    receipts.find((receipt) => {
      const receiptHash = receipt?.txHash ?? receipt?.hash
      return typeof receiptHash === 'string' && receiptHash === txHash
    }) ?? null
  )
}

/**
 * Chained (additive) promo end: deposits in on-chain order, each starting at
 * the later of its own block time and the previous window's end. Total
 * featured time therefore equals the sum of purchased days.
 */
function chainedPromoEnd(promos: Array<{ startTs: number; days: number }>): number | null {
  let end: number | null = null
  for (const promo of promos) {
    const start: number = end !== null && end > promo.startTs ? end : promo.startTs
    end = start + promo.days * 86_400
  }
  return end
}

function firstString(record: Record<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'string' && value.trim().length) return value.trim()
  }
  return null
}

function extractLinks(json: Record<string, unknown>): Record<string, string> {
  const links: Record<string, string> = {}
  const fromObject = json.links ?? json.socials ?? json.social
  if (fromObject && typeof fromObject === 'object' && !Array.isArray(fromObject)) {
    for (const [key, value] of Object.entries(fromObject as Record<string, unknown>)) {
      if (typeof value === 'string' && value.trim().length) links[key] = value.trim()
    }
  }
  for (const key of ['website', 'homepage', 'x', 'twitter', 'telegram', 'discord', 'github']) {
    const value = json[key]
    if (typeof value === 'string' && value.trim().length && !links[key]) links[key] = value.trim()
  }
  return links
}
