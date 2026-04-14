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
    const migration = readMigration('001_explorer2_contracts.sql')
    this.db.exec(migration)
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
}
