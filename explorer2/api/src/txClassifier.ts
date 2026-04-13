import type { Address, TxClassification, TxClassificationType, TxDetail } from '@animica/explorer2-shared'
import { decodeCallWithAbi, decodeEventsWithAbi, extractMethodSelector } from './abiDecoder.js'

function toStringValue(value: unknown): string | null {
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isFinite(value)) return `0x${Math.floor(value).toString(16)}`
  if (typeof value === 'bigint') return `0x${value.toString(16)}`
  if (value instanceof Uint8Array) return `0x${Buffer.from(value).toString('hex')}`
  return null
}

function isHexString(value: string): boolean {
  return /^0x[0-9a-f]*$/i.test(value)
}

function normalizeHex(value: string): string {
  const trimmed = value.trim().toLowerCase()
  if (!trimmed) return '0x'
  if (trimmed.startsWith('0x')) return trimmed
  if (/^[0-9a-f]+$/i.test(trimmed)) return `0x${trimmed}`
  return trimmed
}

function isEmptyRecipient(value: unknown): boolean {
  if (value === null || value === undefined) return true
  if (typeof value !== 'string') return false
  const normalized = value.trim().toLowerCase()
  return normalized.length === 0 || normalized === '0x' || normalized === '0x0' || /^0x0+$/.test(normalized)
}

function extractByPath(record: any, ...paths: string[]): unknown {
  for (const path of paths) {
    const parts = path.split('.')
    let cursor: any = record
    let matched = true
    for (const part of parts) {
      if (!cursor || typeof cursor !== 'object' || !(part in cursor)) {
        matched = false
        break
      }
      cursor = cursor[part]
    }
    if (matched) return cursor
  }
  return undefined
}

export function extractTxInputData(rawTx: unknown, txDetail?: TxDetail): string | null {
  const tx = rawTx as any
  const candidates = [
    extractByPath(tx, 'data'),
    extractByPath(tx, 'input'),
    extractByPath(tx, 'payload.v.data'),
    extractByPath(tx, 'tx.payload.v.data'),
    extractByPath(tx, 'body.payload.v.data'),
    extractByPath(tx, 'callData'),
    extractByPath(tx, 'calldata'),
    extractByPath(tx, 'payload')
  ]

  for (const candidate of candidates) {
    const asString = toStringValue(candidate)
    if (!asString) continue
    const normalized = normalizeHex(asString)
    if (isHexString(normalized)) return normalized
  }

  if (txDetail && typeof txDetail.raw === 'object' && txDetail.raw) {
    const nested = extractTxInputData(txDetail.raw)
    if (nested) return nested
  }

  return null
}

export function extractDeployCode(rawTx: unknown): string | null {
  const tx = rawTx as any
  const candidates = [
    extractByPath(tx, 'code'),
    extractByPath(tx, 'init_code'),
    extractByPath(tx, 'bytecode'),
    extractByPath(tx, 'payload.v.code'),
    extractByPath(tx, 'tx.payload.v.code'),
    extractByPath(tx, 'body.payload.v.code')
  ]
  for (const candidate of candidates) {
    const asString = toStringValue(candidate)
    if (!asString) continue
    const normalized = normalizeHex(asString)
    if (isHexString(normalized)) return normalized
    return asString
  }
  return null
}

export function extractManifest(rawTx: unknown): unknown {
  const tx = rawTx as any
  return (
    extractByPath(tx, 'manifest') ??
    extractByPath(tx, 'payload.v.manifest') ??
    extractByPath(tx, 'tx.payload.v.manifest') ??
    extractByPath(tx, 'body.payload.v.manifest')
  )
}

export function extractContractAddress(receipt: unknown, rawTx: unknown): Address | null {
  const fromReceipt =
    extractByPath(receipt as any, 'contractAddress') ??
    extractByPath(receipt as any, 'contract_address') ??
    extractByPath(receipt as any, 'createdContract') ??
    extractByPath(receipt as any, 'created_contract')
  const fromTx =
    extractByPath(rawTx as any, 'contractAddress') ??
    extractByPath(rawTx as any, 'createdContract') ??
    extractByPath(rawTx as any, 'createdAddress')
  const value = typeof fromReceipt === 'string' ? fromReceipt : typeof fromTx === 'string' ? fromTx : null
  if (!value) return null
  const trimmed = value.trim()
  return trimmed.length ? trimmed : null
}

function inferExplicitKind(rawTx: any): string | null {
  const kind = extractByPath(rawTx, 'kind') ?? extractByPath(rawTx, 'tx_kind') ?? extractByPath(rawTx, 'type') ?? extractByPath(rawTx, 'txType')
  const deploymentType =
    extractByPath(rawTx, 'deploymentType') ??
    extractByPath(rawTx, 'deployment_type') ??
    extractByPath(rawTx, 'receipt.deploymentType')
  const methodLike =
    extractByPath(rawTx, 'method') ??
    extractByPath(rawTx, 'action') ??
    extractByPath(rawTx, 'operation') ??
    extractByPath(rawTx, 'payload.v.method') ??
    extractByPath(rawTx, 'tx.payload.v.method')
  if (typeof kind === 'number') {
    if (kind === 1) return 'deploy'
    if (kind === 2) return 'call'
    if (kind === 0) return 'transfer'
  }
  if (typeof kind === 'string') {
    const compact = kind.toLowerCase().replace(/[^a-z0-9]/g, '')
    if (compact.includes('deploy') || compact.includes('contractcreate') || compact === 'create') return 'deploy'
    if (compact.includes('call') || compact.includes('invoke') || compact.includes('interaction')) return 'call'
    if (compact.includes('transfer') || compact.includes('payment')) return 'transfer'
  }
  if (typeof deploymentType === 'string') {
    const compact = deploymentType.toLowerCase().replace(/[^a-z0-9]/g, '')
    if (compact.includes('pythonvm') || compact.includes('package') || compact.includes('deploy')) {
      return 'deploy'
    }
  }
  if (typeof methodLike === 'string') {
    const compact = methodLike.toLowerCase().replace(/[^a-z0-9]/g, '')
    if (
      compact.includes('deploy') ||
      compact.includes('createcontract') ||
      compact.includes('packagepublish') ||
      compact.includes('manifestdeploy')
    ) {
      return 'deploy'
    }
    if (compact.includes('call') || compact.includes('invoke') || compact.includes('interaction')) return 'call'
    if (compact.includes('transfer') || compact.includes('payment')) return 'transfer'
  }
  return null
}

function classifyType(params: {
  rawTx: unknown
  txDetail: TxDetail
  receipt: unknown
  knownTargetIsContract: boolean
  hasInputData: boolean
  createdContractAddress: string | null
}): TxClassificationType {
  const explicit = inferExplicitKind(params.rawTx as any)
  if (params.createdContractAddress || explicit === 'deploy') return 'contract_deployment'

  const tx = params.txDetail
  const toValue =
    tx.to ??
    (extractByPath(params.rawTx as any, 'to') as string | undefined) ??
    (extractByPath(params.rawTx as any, 'payload.v.to') as string | undefined) ??
    (extractByPath(params.rawTx as any, 'tx.payload.v.to') as string | undefined)

  if (explicit === 'transfer') return !isEmptyRecipient(toValue) ? 'native_transfer' : 'unknown'
  if (explicit === 'call') return 'contract_interaction'
  if (params.knownTargetIsContract && params.hasInputData) return 'contract_interaction'
  if (params.knownTargetIsContract && !params.hasInputData) {
    const hasExecution =
      (Array.isArray((params.receipt as any)?.logs) && (params.receipt as any).logs.length > 0) ||
      typeof extractByPath(params.receipt as any, 'returnData') === 'string' ||
      typeof extractByPath(params.receipt as any, 'output') === 'string' ||
      typeof extractByPath(params.receipt as any, 'vmOutput') === 'string'
    return hasExecution ? 'contract_interaction' : 'native_transfer'
  }
  if (!isEmptyRecipient(toValue) && params.hasInputData) return 'contract_interaction'
  if (isEmptyRecipient(toValue) && params.hasInputData) return 'contract_deployment'
  if (!isEmptyRecipient(toValue)) return 'native_transfer'
  return 'unknown'
}

function normalizeFailureReason(txDetail: TxDetail, receipt: any): string | null {
  if (txDetail.status === 'failed') return 'failed'
  const status = receipt?.status ?? txDetail.status
  if (typeof status === 'string') {
    const upper = status.toUpperCase()
    if (upper === 'REVERT') return 'revert'
    if (upper === 'OOG') return 'out_of_gas'
    if (upper === 'FAILED') return 'failed'
  }
  if (status === 0) return 'failed'
  return null
}

function isRevertedStatus(txDetail: TxDetail, receipt: any): boolean {
  if (txDetail.status === 'failed') return true
  const status = receipt?.status
  if (typeof status === 'string') {
    const upper = status.toUpperCase()
    return upper === 'REVERT' || upper === 'OOG' || upper === 'FAILED'
  }
  if (status === 0) return true
  return false
}

export interface ClassifyTxOptions {
  txDetail: TxDetail
  rawTx: unknown
  receipt: unknown
  knownTargetIsContract?: boolean
  abi?: unknown
}

export function classifyTransaction(options: ClassifyTxOptions): TxClassification {
  const { txDetail, rawTx, receipt, abi } = options
  const inputData = extractTxInputData(rawTx, txDetail)
  const methodSelector = extractMethodSelector(inputData)
  const createdContractAddress = extractContractAddress(receipt, rawTx)
  const type = classifyType({
    rawTx,
    txDetail,
    receipt,
    knownTargetIsContract: Boolean(options.knownTargetIsContract),
    hasInputData: Boolean(inputData && inputData !== '0x'),
    createdContractAddress
  })

  const reverted = isRevertedStatus(txDetail, receipt)
  const classification: TxClassification = {
    type,
    failed: reverted,
    isReverted: reverted,
    reason: normalizeFailureReason(txDetail, receipt),
    targetIsContract: Boolean(options.knownTargetIsContract),
    createdContractAddress,
    methodSelector,
    rawInput: inputData,
    rawOutput: null
  }

  if (abi && inputData && type === 'contract_interaction') {
    classification.decodedCall = decodeCallWithAbi(inputData, abi)
    const logs = Array.isArray((receipt as any)?.logs) ? (receipt as any).logs : []
    classification.decodedEvents = decodeEventsWithAbi(logs, abi)
  }

  return classification
}
