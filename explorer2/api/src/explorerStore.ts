import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import type Database from 'better-sqlite3'
import type {
  ContractProfile,
  ContractVerificationJob,
  ContractVerificationRecord,
  TxClassification
} from '@animica/explorer2-shared'

function loadDatabaseModule(): typeof Database {
  const require = createRequire(import.meta.url)
  const module = require('better-sqlite3') as { default?: typeof Database }
  return module.default ?? (module as unknown as typeof Database)
}

const moduleDir = path.dirname(fileURLToPath(import.meta.url))

function migrationCandidates(fileName: string): string[] {
  const cwd = process.cwd()
  return [
    path.resolve(moduleDir, 'migrations', fileName),
    path.resolve(moduleDir, '..', 'src', 'migrations', fileName),
    path.resolve(cwd, 'explorer2', 'api', 'src', 'migrations', fileName),
    path.resolve(cwd, 'src', 'migrations', fileName)
  ]
}

function readMigration(fileName: string): string {
  for (const candidate of migrationCandidates(fileName)) {
    if (!fs.existsSync(candidate)) continue
    return fs.readFileSync(candidate, 'utf-8')
  }
  throw new Error(
    `Migration file not found: ${fileName}. Tried: ${migrationCandidates(fileName).join(', ')}`
  )
}

function safeJsonParse<T>(value: unknown): T | null {
  if (typeof value !== 'string' || !value.length) return null
  try {
    return JSON.parse(value) as T
  } catch {
    return null
  }
}

function nowTs(): number {
  return Math.floor(Date.now() / 1000)
}

export interface ExplorerStoreOptions {
  dbPath: string
}

export class ExplorerStore {
  private db: Database.Database

  constructor(options: ExplorerStoreOptions) {
    const DatabaseImpl = loadDatabaseModule()
    const dbDir = path.dirname(options.dbPath)
    if (dbDir && dbDir !== '.' && !fs.existsSync(dbDir)) {
      fs.mkdirSync(dbDir, { recursive: true })
    }
    this.db = new DatabaseImpl(options.dbPath)
    this.db.pragma('journal_mode = WAL')
    this.migrate()
  }

  private migrate(): void {
    for (const fileName of ['001_explorer2_contracts.sql', '002_explorer2_tokens.sql']) {
      this.db.exec(readMigration(fileName))
    }
  }

  upsertTxClassification(params: {
    txHash: string
    fromAddress?: string
    toAddress?: string
    classification: TxClassification
  }): void {
    const updatedAt = nowTs()
    this.db
      .prepare(
        `
        INSERT INTO tx_classification (
          tx_hash, tx_type, failed, is_reverted, reason, from_address, to_address,
          created_contract_address, method_selector, raw_input, decoded_call_json, decoded_events_json, updated_at
        ) VALUES (
          @tx_hash, @tx_type, @failed, @is_reverted, @reason, @from_address, @to_address,
          @created_contract_address, @method_selector, @raw_input, @decoded_call_json, @decoded_events_json, @updated_at
        )
        ON CONFLICT(tx_hash) DO UPDATE SET
          tx_type = excluded.tx_type,
          failed = excluded.failed,
          is_reverted = excluded.is_reverted,
          reason = excluded.reason,
          from_address = excluded.from_address,
          to_address = excluded.to_address,
          created_contract_address = excluded.created_contract_address,
          method_selector = excluded.method_selector,
          raw_input = excluded.raw_input,
          decoded_call_json = excluded.decoded_call_json,
          decoded_events_json = excluded.decoded_events_json,
          updated_at = excluded.updated_at
      `
      )
      .run({
        tx_hash: params.txHash,
        tx_type: params.classification.type,
        failed: params.classification.failed ? 1 : 0,
        is_reverted: params.classification.isReverted ? 1 : 0,
        reason: params.classification.reason ?? null,
        from_address: params.fromAddress ?? null,
        to_address: params.toAddress ?? null,
        created_contract_address: params.classification.createdContractAddress ?? null,
        method_selector: params.classification.methodSelector ?? null,
        raw_input: params.classification.rawInput ?? null,
        decoded_call_json: params.classification.decodedCall ? JSON.stringify(params.classification.decodedCall) : null,
        decoded_events_json: params.classification.decodedEvents ? JSON.stringify(params.classification.decodedEvents) : null,
        updated_at: updatedAt
      })
  }

  getTxClassification(txHash: string): TxClassification | null {
    const row = this.db.prepare('SELECT * FROM tx_classification WHERE tx_hash = ?').get(txHash) as Record<string, unknown> | undefined
    if (!row) return null
    return {
      type: String(row.tx_type) as TxClassification['type'],
      failed: Number(row.failed || 0) > 0,
      isReverted: Number(row.is_reverted || 0) > 0,
      reason: typeof row.reason === 'string' ? row.reason : null,
      targetIsContract: String(row.tx_type) === 'contract_interaction',
      createdContractAddress: typeof row.created_contract_address === 'string' ? row.created_contract_address : null,
      methodSelector: typeof row.method_selector === 'string' ? row.method_selector : null,
      rawInput: typeof row.raw_input === 'string' ? row.raw_input : null,
      rawOutput: null,
      decodedCall: safeJsonParse(row.decoded_call_json),
      decodedEvents: safeJsonParse(row.decoded_events_json) ?? []
    }
  }

  upsertContractProfile(params: {
    address: string
    accountType: 'contract' | 'eoa' | 'unknown'
    creatorAddress?: string | null
    creatorTxHash?: string | null
    creationBlockHeight?: number | null
    creationBlockHash?: string | null
    creationTimestamp?: number | null
    codeHash?: string | null
    runtimeCodeHash?: string | null
    codeSizeBytes?: number | null
    metadataJson?: unknown
    abi?: unknown
  }): void {
    const updatedAt = nowTs()
    this.db
      .prepare(
        `
        INSERT INTO contract_profile (
          address, account_type, creator_address, creator_tx_hash, creation_block_height, creation_block_hash,
          creation_timestamp, code_hash, runtime_code_hash, code_size_bytes, metadata_json, abi_json, updated_at
        ) VALUES (
          @address, @account_type, @creator_address, @creator_tx_hash, @creation_block_height, @creation_block_hash,
          @creation_timestamp, @code_hash, @runtime_code_hash, @code_size_bytes, @metadata_json, @abi_json, @updated_at
        )
        ON CONFLICT(address) DO UPDATE SET
          account_type = excluded.account_type,
          creator_address = COALESCE(contract_profile.creator_address, excluded.creator_address),
          creator_tx_hash = COALESCE(contract_profile.creator_tx_hash, excluded.creator_tx_hash),
          creation_block_height = COALESCE(contract_profile.creation_block_height, excluded.creation_block_height),
          creation_block_hash = COALESCE(contract_profile.creation_block_hash, excluded.creation_block_hash),
          creation_timestamp = COALESCE(contract_profile.creation_timestamp, excluded.creation_timestamp),
          code_hash = COALESCE(excluded.code_hash, contract_profile.code_hash),
          runtime_code_hash = COALESCE(excluded.runtime_code_hash, contract_profile.runtime_code_hash),
          code_size_bytes = COALESCE(excluded.code_size_bytes, contract_profile.code_size_bytes),
          metadata_json = COALESCE(excluded.metadata_json, contract_profile.metadata_json),
          abi_json = COALESCE(excluded.abi_json, contract_profile.abi_json),
          updated_at = excluded.updated_at
      `
      )
      .run({
        address: params.address,
        account_type: params.accountType,
        creator_address: params.creatorAddress ?? null,
        creator_tx_hash: params.creatorTxHash ?? null,
        creation_block_height: params.creationBlockHeight ?? null,
        creation_block_hash: params.creationBlockHash ?? null,
        creation_timestamp: params.creationTimestamp ?? null,
        code_hash: params.codeHash ?? null,
        runtime_code_hash: params.runtimeCodeHash ?? null,
        code_size_bytes: params.codeSizeBytes ?? null,
        metadata_json: params.metadataJson ? JSON.stringify(params.metadataJson) : null,
        abi_json: params.abi ? JSON.stringify(params.abi) : null,
        updated_at: updatedAt
      })
  }

  private parseVerificationRecord(row: Record<string, unknown> | undefined): ContractVerificationRecord | undefined {
    if (!row) return undefined
    const result = safeJsonParse<ContractVerificationRecord>(row.result_json) ?? undefined
    const status = String(row.status || '')
    if (!result) {
      return {
        jobId: typeof row.job_id === 'string' ? row.job_id : undefined,
        status: status === 'verified' ? 'verified' : status === 'failed' ? 'failed' : status === 'running' ? 'running' : 'pending',
        error: typeof row.error_message === 'string' ? row.error_message : null,
        submittedAt: typeof row.submitted_at === 'number' ? row.submitted_at : null,
        completedAt: typeof row.completed_at === 'number' ? row.completed_at : null
      }
    }
    return {
      jobId: typeof row.job_id === 'string' ? row.job_id : result.jobId,
      ...result,
      status: status === 'verified' ? 'verified' : status === 'failed' ? 'failed' : status === 'running' ? 'running' : 'pending',
      error: typeof row.error_message === 'string' ? row.error_message : result.error ?? null,
      submittedAt: typeof row.submitted_at === 'number' ? row.submitted_at : result.submittedAt ?? null,
      completedAt: typeof row.completed_at === 'number' ? row.completed_at : result.completedAt ?? null
    }
  }

  getContractProfile(address: string): ContractProfile | null {
    const row = this.db.prepare('SELECT * FROM contract_profile WHERE address = ?').get(address) as Record<string, unknown> | undefined
    if (!row) return null
    const verificationRow = this.db
      .prepare('SELECT * FROM verification_job WHERE address = ? ORDER BY submitted_at DESC LIMIT 1')
      .get(address) as Record<string, unknown> | undefined
    const abi = safeJsonParse(row.abi_json)
    const metadataJson = safeJsonParse(row.metadata_json)
    const verification = this.parseVerificationRecord(verificationRow)
    return {
      address,
      accountType: String(row.account_type || 'unknown') as ContractProfile['accountType'],
      creatorAddress: (row.creator_address as string | null) ?? null,
      creatorTxHash: (row.creator_tx_hash as string | null) ?? null,
      creationBlockHeight: typeof row.creation_block_height === 'number' ? row.creation_block_height : null,
      creationBlockHash: (row.creation_block_hash as string | null) ?? null,
      creationTimestamp: typeof row.creation_timestamp === 'number' ? row.creation_timestamp : null,
      codeHash: (row.code_hash as string | null) ?? null,
      runtimeCodeHash: (row.runtime_code_hash as string | null) ?? null,
      codeSizeBytes: typeof row.code_size_bytes === 'number' ? row.code_size_bytes : null,
      abi,
      metadataJson,
      isVerified: verification?.status === 'verified',
      verification
    }
  }

  findContractProfileByCreatorTx(txHash: string): ContractProfile | null {
    const row = this.db
      .prepare('SELECT address FROM contract_profile WHERE creator_tx_hash = ? LIMIT 1')
      .get(txHash) as { address?: string } | undefined
    if (!row?.address) return null
    return this.getContractProfile(row.address)
  }

  createVerificationJob(params: { jobId: string; address: string; requestJson: unknown }): void {
    this.db
      .prepare(
        `
        INSERT OR REPLACE INTO verification_job (
          job_id, address, status, request_json, result_json, error_message, submitted_at, completed_at
        ) VALUES (
          @job_id, @address, @status, @request_json, NULL, NULL, @submitted_at, NULL
        )
      `
      )
      .run({
        job_id: params.jobId,
        address: params.address,
        status: 'pending',
        request_json: JSON.stringify(params.requestJson ?? {}),
        submitted_at: nowTs()
      })
  }

  updateVerificationJob(params: {
    jobId: string
    status: 'pending' | 'running' | 'verified' | 'failed'
    result?: ContractVerificationRecord | null
    error?: string | null
  }): void {
    this.db
      .prepare(
        `
        UPDATE verification_job
        SET status = @status,
            result_json = @result_json,
            error_message = @error_message,
            completed_at = CASE WHEN @status IN ('verified', 'failed') THEN @completed_at ELSE completed_at END
        WHERE job_id = @job_id
      `
      )
      .run({
        job_id: params.jobId,
        status: params.status,
        result_json: params.result ? JSON.stringify(params.result) : null,
        error_message: params.error ?? null,
        completed_at: nowTs()
      })
  }

  getVerificationJob(jobId: string): ContractVerificationJob | null {
    const row = this.db.prepare('SELECT * FROM verification_job WHERE job_id = ?').get(jobId) as Record<string, unknown> | undefined
    if (!row) return null
    return {
      jobId: String(row.job_id),
      address: String(row.address),
      status: String(row.status) as ContractVerificationJob['status'],
      submittedAt: Number(row.submitted_at || 0),
      completedAt: typeof row.completed_at === 'number' ? row.completed_at : null,
      error: typeof row.error_message === 'string' ? row.error_message : null,
      result: safeJsonParse(row.result_json)
    }
  }

  getLatestVerificationForAddress(address: string): ContractVerificationRecord | undefined {
    const row = this.db
      .prepare('SELECT * FROM verification_job WHERE address = ? ORDER BY submitted_at DESC LIMIT 1')
      .get(address) as Record<string, unknown> | undefined
    return this.parseVerificationRecord(row)
  }

  // ── Token tracker ───────────────────────────────────────────────────────────

  /**
   * Insert-or-merge a token profile row. Null/undefined fields never clobber
   * previously-learned values (COALESCE on the excluded row), so deploy info,
   * init metadata and market data can arrive in any order.
   */
  upsertTokenProfile(params: TokenProfileUpsert): void {
    this.db
      .prepare(
        `
        INSERT INTO token_profile (
          address, addr_key, kind, name, symbol, decimals, metadata_uri, image_url, description, links_json,
          total_supply, price_anm, liquidity_anm, change_24h, pair_address, fee_bps,
          initial_supply, max_supply, mintable, creator, creation_height, creation_tx, creation_ts,
          init_tx, promoted, promo_days_left, meta_fetched_at, updated_at
        ) VALUES (
          @address, @addr_key, @kind, @name, @symbol, @decimals, @metadata_uri, @image_url, @description, @links_json,
          @total_supply, @price_anm, @liquidity_anm, @change_24h, @pair_address, @fee_bps,
          @initial_supply, @max_supply, @mintable, @creator, @creation_height, @creation_tx, @creation_ts,
          @init_tx, 0, NULL, @meta_fetched_at, @updated_at
        )
        ON CONFLICT(addr_key) DO UPDATE SET
          kind = excluded.kind,
          name = COALESCE(excluded.name, token_profile.name),
          symbol = COALESCE(excluded.symbol, token_profile.symbol),
          decimals = COALESCE(excluded.decimals, token_profile.decimals),
          metadata_uri = COALESCE(excluded.metadata_uri, token_profile.metadata_uri),
          image_url = COALESCE(excluded.image_url, token_profile.image_url),
          description = COALESCE(excluded.description, token_profile.description),
          links_json = COALESCE(excluded.links_json, token_profile.links_json),
          total_supply = COALESCE(excluded.total_supply, token_profile.total_supply),
          price_anm = COALESCE(excluded.price_anm, token_profile.price_anm),
          liquidity_anm = COALESCE(excluded.liquidity_anm, token_profile.liquidity_anm),
          change_24h = COALESCE(excluded.change_24h, token_profile.change_24h),
          pair_address = COALESCE(excluded.pair_address, token_profile.pair_address),
          fee_bps = COALESCE(excluded.fee_bps, token_profile.fee_bps),
          initial_supply = COALESCE(excluded.initial_supply, token_profile.initial_supply),
          max_supply = COALESCE(excluded.max_supply, token_profile.max_supply),
          mintable = COALESCE(excluded.mintable, token_profile.mintable),
          creator = COALESCE(excluded.creator, token_profile.creator),
          creation_height = COALESCE(excluded.creation_height, token_profile.creation_height),
          creation_tx = COALESCE(excluded.creation_tx, token_profile.creation_tx),
          creation_ts = COALESCE(excluded.creation_ts, token_profile.creation_ts),
          init_tx = COALESCE(excluded.init_tx, token_profile.init_tx),
          meta_fetched_at = COALESCE(excluded.meta_fetched_at, token_profile.meta_fetched_at),
          updated_at = excluded.updated_at
      `
      )
      .run({
        address: params.address,
        addr_key: params.addrKey,
        kind: params.kind ?? 'token',
        name: params.name ?? null,
        symbol: params.symbol ?? null,
        decimals: params.decimals ?? null,
        metadata_uri: params.metadataUri ?? null,
        image_url: params.imageUrl ?? null,
        description: params.description ?? null,
        links_json: params.links ? JSON.stringify(params.links) : null,
        total_supply: params.totalSupply ?? null,
        price_anm: params.priceAnm ?? null,
        liquidity_anm: params.liquidityAnm ?? null,
        change_24h: params.change24h ?? null,
        pair_address: params.pairAddress ?? null,
        fee_bps: params.feeBps ?? null,
        initial_supply: params.initialSupply ?? null,
        max_supply: params.maxSupply ?? null,
        mintable: params.mintable === undefined || params.mintable === null ? null : params.mintable ? 1 : 0,
        creator: params.creator ?? null,
        creation_height: params.creationHeight ?? null,
        creation_tx: params.creationTx ?? null,
        creation_ts: params.creationTs ?? null,
        init_tx: params.initTx ?? null,
        meta_fetched_at: params.metaFetchedAt ?? null,
        updated_at: nowTs()
      })
  }

  getTokenProfileByKey(addrKey: string): TokenProfileRow | null {
    const row = this.db.prepare('SELECT * FROM token_profile WHERE addr_key = ?').get(addrKey) as
      | Record<string, unknown>
      | undefined
    return row ? toTokenRow(row) : null
  }

  listTokenProfiles(limit: number, offset = 0): TokenProfileRow[] {
    const rows = this.db
      .prepare(
        `SELECT * FROM token_profile WHERE kind = 'token'
         ORDER BY promoted DESC, COALESCE(creation_height, 0) DESC, updated_at DESC
         LIMIT ? OFFSET ?`
      )
      .all(limit, offset) as Record<string, unknown>[]
    return rows.map(toTokenRow)
  }

  searchTokenProfiles(query: string, limit: number): TokenProfileRow[] {
    const like = `%${query.replace(/[%_]/g, (m) => `\\${m}`)}%`
    const rows = this.db
      .prepare(
        `SELECT * FROM token_profile WHERE kind = 'token' AND (
           name LIKE ? ESCAPE '\\' COLLATE NOCASE
           OR symbol LIKE ? ESCAPE '\\' COLLATE NOCASE
           OR address LIKE ? ESCAPE '\\' COLLATE NOCASE
           OR addr_key LIKE ? ESCAPE '\\' COLLATE NOCASE
         )
         ORDER BY promoted DESC, COALESCE(creation_height, 0) DESC
         LIMIT ?`
      )
      .all(like, like, like, like, limit) as Record<string, unknown>[]
    return rows.map(toTokenRow)
  }

  listPromotedTokenProfiles(limit: number): TokenProfileRow[] {
    const rows = this.db
      .prepare(
        `SELECT * FROM token_profile WHERE kind = 'token' AND promoted = 1
         ORDER BY COALESCE(promo_days_left, 0) DESC, updated_at DESC
         LIMIT ?`
      )
      .all(limit) as Record<string, unknown>[]
    return rows.map(toTokenRow)
  }

  listAllTokenKeys(): Array<{ addrKey: string; address: string }> {
    // Ordered by address so round-robin passes over tokens are deterministic.
    const rows = this.db
      .prepare(`SELECT addr_key, address FROM token_profile WHERE kind = 'token' ORDER BY address`)
      .all() as Array<{ addr_key: string; address: string }>
    return rows.map((row) => ({ addrKey: row.addr_key, address: row.address }))
  }

  listTokenKeysMissingCreation(): string[] {
    const rows = this.db
      .prepare(`SELECT addr_key FROM token_profile WHERE kind = 'token' AND creation_tx IS NULL`)
      .all() as Array<{ addr_key: string }>
    return rows.map((row) => row.addr_key)
  }

  listTokensNeedingMetadataFetch(cutoffTs: number, limit: number): TokenProfileRow[] {
    const rows = this.db
      .prepare(
        `SELECT * FROM token_profile
         WHERE kind = 'token' AND metadata_uri IS NOT NULL AND metadata_uri != ''
           AND (meta_fetched_at IS NULL OR meta_fetched_at < ?)
         LIMIT ?`
      )
      .all(cutoffTs, limit) as Record<string, unknown>[]
    return rows.map(toTokenRow)
  }

  setTokenPromoFlags(addrKey: string, promoted: boolean, promoDaysLeft: number | null): void {
    this.db
      .prepare('UPDATE token_profile SET promoted = ?, promo_days_left = ? WHERE addr_key = ?')
      .run(promoted ? 1 : 0, promoDaysLeft, addrKey)
  }

  setTokenMetaFetchedAt(addrKey: string, ts: number): void {
    this.db.prepare('UPDATE token_profile SET meta_fetched_at = ? WHERE addr_key = ?').run(ts, addrKey)
  }

  upsertTokenDeploy(params: {
    addrKey: string
    address?: string | null
    creator?: string | null
    creationHeight?: number | null
    creationTx?: string | null
    creationTs?: number | null
    codeHash?: string | null
    manifestName?: string | null
  }): void {
    this.db
      .prepare(
        `
        INSERT INTO token_deploy (addr_key, address, creator, creation_height, creation_tx, creation_ts, code_hash, manifest_name, updated_at)
        VALUES (@addr_key, @address, @creator, @creation_height, @creation_tx, @creation_ts, @code_hash, @manifest_name, @updated_at)
        ON CONFLICT(addr_key) DO UPDATE SET
          address = COALESCE(excluded.address, token_deploy.address),
          creator = COALESCE(excluded.creator, token_deploy.creator),
          creation_height = COALESCE(excluded.creation_height, token_deploy.creation_height),
          creation_tx = COALESCE(excluded.creation_tx, token_deploy.creation_tx),
          creation_ts = COALESCE(excluded.creation_ts, token_deploy.creation_ts),
          code_hash = COALESCE(excluded.code_hash, token_deploy.code_hash),
          manifest_name = COALESCE(excluded.manifest_name, token_deploy.manifest_name),
          updated_at = excluded.updated_at
      `
      )
      .run({
        addr_key: params.addrKey,
        address: params.address ?? null,
        creator: params.creator ?? null,
        creation_height: params.creationHeight ?? null,
        creation_tx: params.creationTx ?? null,
        creation_ts: params.creationTs ?? null,
        code_hash: params.codeHash ?? null,
        manifest_name: params.manifestName ?? null,
        updated_at: nowTs()
      })
  }

  getTokenDeploy(addrKey: string): {
    creator: string | null
    creationHeight: number | null
    creationTx: string | null
    creationTs: number | null
    manifestName: string | null
    codeHash: string | null
  } | null {
    const row = this.db.prepare('SELECT * FROM token_deploy WHERE addr_key = ?').get(addrKey) as
      | Record<string, unknown>
      | undefined
    if (!row) return null
    return {
      creator: typeof row.creator === 'string' ? row.creator : null,
      creationHeight: typeof row.creation_height === 'number' ? row.creation_height : null,
      creationTx: typeof row.creation_tx === 'string' ? row.creation_tx : null,
      creationTs: typeof row.creation_ts === 'number' ? row.creation_ts : null,
      manifestName: typeof row.manifest_name === 'string' ? row.manifest_name : null,
      codeHash: typeof row.code_hash === 'string' ? row.code_hash : null
    }
  }

  upsertTokenPromo(params: { txHash: string; addrKey: string; startTs: number; days: number; label?: string | null }): void {
    this.db
      .prepare(
        `
        INSERT INTO token_promo (tx_hash, addr_key, start_ts, days, label, updated_at)
        VALUES (@tx_hash, @addr_key, @start_ts, @days, @label, @updated_at)
        ON CONFLICT(tx_hash) DO UPDATE SET
          addr_key = excluded.addr_key,
          start_ts = excluded.start_ts,
          days = excluded.days,
          label = COALESCE(excluded.label, token_promo.label),
          updated_at = excluded.updated_at
      `
      )
      .run({
        tx_hash: params.txHash,
        addr_key: params.addrKey,
        start_ts: params.startTs,
        days: params.days,
        label: params.label ?? null,
        updated_at: nowTs()
      })
  }

  /**
   * All promo deposits for a token in deterministic on-chain order
   * (block time, then tx hash). Windows are chained additively by the caller.
   */
  listTokenPromos(addrKey: string): Array<{ txHash: string; startTs: number; days: number }> {
    const rows = this.db
      .prepare('SELECT tx_hash, start_ts, days FROM token_promo WHERE addr_key = ? ORDER BY start_ts ASC, tx_hash ASC')
      .all(addrKey) as Array<{ tx_hash: string; start_ts: number; days: number }>
    return rows.map((row) => ({ txHash: row.tx_hash, startTs: row.start_ts, days: row.days }))
  }

  /** Stage a mined init call for later deployer-verification. */
  upsertTokenInitSeen(params: {
    txHash: string
    addrKey: string
    senderKey?: string | null
    kind?: string
    height?: number | null
    name?: string | null
    symbol?: string | null
    decimals?: number | null
    initialSupply?: string | null
    maxSupply?: string | null
    mintable?: boolean | null
    metadataUri?: string | null
  }): void {
    this.db
      .prepare(
        `
        INSERT INTO token_init_seen (
          tx_hash, addr_key, sender_key, kind, height, name, symbol, decimals,
          initial_supply, max_supply, mintable, metadata_uri, updated_at
        ) VALUES (
          @tx_hash, @addr_key, @sender_key, @kind, @height, @name, @symbol, @decimals,
          @initial_supply, @max_supply, @mintable, @metadata_uri, @updated_at
        )
        ON CONFLICT(tx_hash) DO UPDATE SET
          addr_key = excluded.addr_key,
          sender_key = COALESCE(excluded.sender_key, token_init_seen.sender_key),
          kind = excluded.kind,
          height = COALESCE(excluded.height, token_init_seen.height),
          name = COALESCE(excluded.name, token_init_seen.name),
          symbol = COALESCE(excluded.symbol, token_init_seen.symbol),
          decimals = COALESCE(excluded.decimals, token_init_seen.decimals),
          initial_supply = COALESCE(excluded.initial_supply, token_init_seen.initial_supply),
          max_supply = COALESCE(excluded.max_supply, token_init_seen.max_supply),
          mintable = COALESCE(excluded.mintable, token_init_seen.mintable),
          metadata_uri = COALESCE(excluded.metadata_uri, token_init_seen.metadata_uri),
          updated_at = excluded.updated_at
      `
      )
      .run({
        tx_hash: params.txHash,
        addr_key: params.addrKey,
        sender_key: params.senderKey ?? null,
        kind: params.kind ?? 'token',
        height: params.height ?? null,
        name: params.name ?? null,
        symbol: params.symbol ?? null,
        decimals: params.decimals ?? null,
        initial_supply: params.initialSupply ?? null,
        max_supply: params.maxSupply ?? null,
        mintable: params.mintable === undefined || params.mintable === null ? null : params.mintable ? 1 : 0,
        metadata_uri: params.metadataUri ?? null,
        updated_at: nowTs()
      })
  }

  /** Distinct addresses with staged init calls awaiting deployer-verification. */
  listInitSeenAddrKeys(): string[] {
    const rows = this.db.prepare('SELECT DISTINCT addr_key FROM token_init_seen').all() as Array<{ addr_key: string }>
    return rows.map((row) => row.addr_key)
  }

  /** Staged init calls for one address, earliest mined first (height, tx hash). */
  listTokenInitCandidates(addrKey: string): TokenInitSeenRow[] {
    const rows = this.db
      .prepare(
        'SELECT * FROM token_init_seen WHERE addr_key = ? ORDER BY COALESCE(height, 2147483647) ASC, tx_hash ASC'
      )
      .all(addrKey) as Record<string, unknown>[]
    return rows.map((row) => ({
      txHash: String(row.tx_hash),
      addrKey: String(row.addr_key),
      senderKey: typeof row.sender_key === 'string' ? row.sender_key : null,
      kind: String(row.kind ?? 'token'),
      height: typeof row.height === 'number' ? row.height : null,
      name: typeof row.name === 'string' ? row.name : null,
      symbol: typeof row.symbol === 'string' ? row.symbol : null,
      decimals: typeof row.decimals === 'number' ? row.decimals : null,
      initialSupply: typeof row.initial_supply === 'string' ? row.initial_supply : null,
      maxSupply: typeof row.max_supply === 'string' ? row.max_supply : null,
      mintable: row.mintable === null || row.mintable === undefined ? null : Number(row.mintable) > 0,
      metadataUri: typeof row.metadata_uri === 'string' ? row.metadata_uri : null
    }))
  }

  insertTokenPricePoint(address: string, t: number, priceAnm: number): void {
    this.db
      .prepare('INSERT OR REPLACE INTO token_price_point (address, t, price_anm) VALUES (?, ?, ?)')
      .run(address, t, priceAnm)
  }

  getTokenPriceHistory(address: string, sinceTs: number, limit = 2000): Array<{ t: number; priceAnm: number }> {
    const rows = this.db
      .prepare('SELECT t, price_anm FROM token_price_point WHERE address = ? AND t >= ? ORDER BY t ASC LIMIT ?')
      .all(address, sinceTs, limit) as Array<{ t: number; price_anm: number }>
    return rows.map((row) => ({ t: row.t, priceAnm: row.price_anm }))
  }

  /** Most recent price observation at least `ageSeconds` old, for 24h change. */
  getTokenPriceBefore(address: string, beforeTs: number): number | null {
    const row = this.db
      .prepare('SELECT price_anm FROM token_price_point WHERE address = ? AND t <= ? ORDER BY t DESC LIMIT 1')
      .get(address, beforeTs) as { price_anm?: number } | undefined
    return typeof row?.price_anm === 'number' ? row.price_anm : null
  }

  getTokenScanState(key: string): string | null {
    const row = this.db.prepare('SELECT value FROM token_scan_state WHERE key = ?').get(key) as
      | { value?: string }
      | undefined
    return typeof row?.value === 'string' ? row.value : null
  }

  setTokenScanState(key: string, value: string): void {
    this.db
      .prepare('INSERT INTO token_scan_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value')
      .run(key, value)
  }
}

// ── Token row shapes ──────────────────────────────────────────────────────────

export interface TokenProfileRow {
  address: string
  addrKey: string
  kind: string
  name: string | null
  symbol: string | null
  decimals: number | null
  metadataUri: string | null
  imageUrl: string | null
  description: string | null
  links: Record<string, string> | null
  totalSupply: string | null
  priceAnm: number | null
  liquidityAnm: number | null
  change24h: number | null
  pairAddress: string | null
  feeBps: number | null
  initialSupply: string | null
  maxSupply: string | null
  mintable: boolean | null
  creator: string | null
  creationHeight: number | null
  creationTx: string | null
  creationTs: number | null
  initTx: string | null
  promoted: boolean
  promoDaysLeft: number | null
  metaFetchedAt: number | null
  updatedAt: number
}

export interface TokenInitSeenRow {
  txHash: string
  addrKey: string
  senderKey: string | null
  kind: string
  height: number | null
  name: string | null
  symbol: string | null
  decimals: number | null
  initialSupply: string | null
  maxSupply: string | null
  mintable: boolean | null
  metadataUri: string | null
}

export interface TokenProfileUpsert {
  address: string
  addrKey: string
  kind?: string
  name?: string | null
  symbol?: string | null
  decimals?: number | null
  metadataUri?: string | null
  imageUrl?: string | null
  description?: string | null
  links?: Record<string, string> | null
  totalSupply?: string | null
  priceAnm?: number | null
  liquidityAnm?: number | null
  change24h?: number | null
  pairAddress?: string | null
  feeBps?: number | null
  initialSupply?: string | null
  maxSupply?: string | null
  mintable?: boolean | null
  creator?: string | null
  creationHeight?: number | null
  creationTx?: string | null
  creationTs?: number | null
  initTx?: string | null
  metaFetchedAt?: number | null
}

function toTokenRow(row: Record<string, unknown>): TokenProfileRow {
  return {
    address: String(row.address),
    addrKey: String(row.addr_key),
    kind: String(row.kind ?? 'token'),
    name: typeof row.name === 'string' ? row.name : null,
    symbol: typeof row.symbol === 'string' ? row.symbol : null,
    decimals: typeof row.decimals === 'number' ? row.decimals : null,
    metadataUri: typeof row.metadata_uri === 'string' ? row.metadata_uri : null,
    imageUrl: typeof row.image_url === 'string' ? row.image_url : null,
    description: typeof row.description === 'string' ? row.description : null,
    links: safeJsonParse<Record<string, string>>(row.links_json),
    totalSupply: typeof row.total_supply === 'string' ? row.total_supply : null,
    priceAnm: typeof row.price_anm === 'number' ? row.price_anm : null,
    liquidityAnm: typeof row.liquidity_anm === 'number' ? row.liquidity_anm : null,
    change24h: typeof row.change_24h === 'number' ? row.change_24h : null,
    pairAddress: typeof row.pair_address === 'string' ? row.pair_address : null,
    feeBps: typeof row.fee_bps === 'number' ? row.fee_bps : null,
    initialSupply: typeof row.initial_supply === 'string' ? row.initial_supply : null,
    maxSupply: typeof row.max_supply === 'string' ? row.max_supply : null,
    mintable: row.mintable === null || row.mintable === undefined ? null : Number(row.mintable) > 0,
    creator: typeof row.creator === 'string' ? row.creator : null,
    creationHeight: typeof row.creation_height === 'number' ? row.creation_height : null,
    creationTx: typeof row.creation_tx === 'string' ? row.creation_tx : null,
    creationTs: typeof row.creation_ts === 'number' ? row.creation_ts : null,
    initTx: typeof row.init_tx === 'string' ? row.init_tx : null,
    promoted: Number(row.promoted ?? 0) > 0,
    promoDaysLeft: typeof row.promo_days_left === 'number' ? row.promo_days_left : null,
    metaFetchedAt: typeof row.meta_fetched_at === 'number' ? row.meta_fetched_at : null,
    updatedAt: Number(row.updated_at ?? 0)
  }
}
