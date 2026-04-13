export type Hash = `0x${string}` | string
export type Address = `anim1${string}` | string
export type AccountType = 'eoa' | 'contract' | 'unknown'
export type TxClassificationType = 'native_transfer' | 'contract_deployment' | 'contract_interaction' | 'unknown'
export type VerificationJobState = 'pending' | 'running' | 'verified' | 'failed'

export interface DecodedValue {
  name: string
  type: string
  value: unknown
}

export interface DecodedFunctionCall {
  selector: string
  signature?: string
  name?: string
  args?: DecodedValue[]
  rawData: string
}

export interface DecodedEvent {
  index: number
  name?: string
  signature?: string
  topic0?: string
  args?: DecodedValue[]
  raw: unknown
}

export interface TxClassification {
  type: TxClassificationType
  failed: boolean
  isReverted: boolean
  reason?: string | null
  targetIsContract?: boolean
  createdContractAddress?: Address | null
  methodSelector?: string | null
  rawInput?: string | null
  rawOutput?: string | null
  decodedCall?: DecodedFunctionCall | null
  decodedEvents?: DecodedEvent[]
}

export interface HeadView {
  height: number
  canonicalHeight?: number
  hash: Hash
  time: number
  chainId?: number
}

export interface BlockSummary {
  height: number
  canonicalHeight?: number
  hash: Hash
  time: number
  txCount: number
  miner?: Address | null
}

export interface BlockDetail {
  height: number
  canonicalHeight?: number
  hash: Hash
  parentHash?: Hash
  time: number
  chainId?: number
  difficulty?: string | number | null
  nonce?: number
  txs: TxSummary[]
  raw: unknown
}

export interface TxSummary {
  hash: Hash
  from?: Address
  to?: Address
  nonce?: number | string
  value?: string
  status?: 'pending' | 'confirmed' | 'failed'
  classification?: TxClassification
}

export interface TxDetail {
  hash: Hash
  tx_hash?: Hash
  status: 'pending' | 'confirmed' | 'failed'
  blockHash?: Hash
  blockHeight?: number
  included_height?: number | null
  included_block_hash?: Hash | null
  confirmations?: number
  explorer_head_height?: number
  timestamp?: number | null
  from?: Address
  to?: Address
  value?: string
  gasUsed?: string
  feePaid?: string
  fee?: string
  raw: unknown
  receipt?: unknown
  classification?: TxClassification
}

export interface ContractCreationInfo {
  creatorAddress?: Address | null
  creatorTxHash?: Hash | null
  creationBlockHeight?: number | null
  creationBlockHash?: Hash | null
  creationTimestamp?: number | null
}

export interface ContractVerificationRecord {
  jobId?: string
  status: VerificationJobState | 'unverified'
  verifierStatus?: string
  contractName?: string | null
  language?: string | null
  compiler?: string | null
  compilerVersion?: string | null
  optimizationEnabled?: boolean | null
  optimizationRuns?: number | null
  vmTarget?: string | null
  constructorArgs?: string | null
  metadataJson?: unknown
  sourceFiles?: Record<string, string> | null
  abi?: unknown
  creationBytecodeHash?: string | null
  runtimeBytecodeHash?: string | null
  verifiedAt?: number | null
  submittedAt?: number | null
  completedAt?: number | null
  error?: string | null
}

export interface ContractProfile extends ContractCreationInfo {
  address: Address
  accountType: AccountType
  codeHash?: string | null
  runtimeCodeHash?: string | null
  codeSizeBytes?: number | null
  abi?: unknown
  metadataJson?: unknown
  isVerified?: boolean
  verification?: ContractVerificationRecord
}

export interface AddressSummary {
  address: Address
  accountType?: AccountType
  confirmedBalance?: string | null
  pendingBalance?: string | null
  txs: TxSummary[]
  contract?: ContractProfile | null
  nextCursor?: string | null
  scannedBlocks?: number
  partial?: boolean
}

export interface MempoolEntry {
  hash: Hash
  sizeBytes?: number
  receivedAt?: number | null
}

export interface MempoolView {
  total: number
  entries: MempoolEntry[]
  nextCursor?: string | null
  stats?: {
    count: number
    totalBytes: number
    oldestAgeSec: number | null
  }
}

export interface ApiError {
  error: string
  message: string
  detail?: string
}

export interface RichListEntry {
  rank: number
  address: Address
  balance: string
  pctSupply?: number
}

export interface RichListResponse {
  height: number
  items: RichListEntry[]
  totalAddresses: number
  nextOffset?: number
}

export interface RichListSummary {
  height: number
  totalSupply: string
  addressCount: number
  top10Pct?: number
  top100Pct?: number
  top1000Pct?: number
}

export type ContractDeploymentKind = 'contract_create' | 'package_publish' | 'manifest_deploy' | 'unknown'

export interface ContractDeployment {
  txHash: Hash
  blockHeight: number
  blockHash: Hash
  blockTime: number | null
  deployer?: Address
  contractAddress?: Address | null
  status: 'confirmed' | 'failed'
  kind: ContractDeploymentKind
  feePaid?: string
  gasUsed?: string
  codeSizeBytes?: number | null
  label?: string | null
}

export interface ContractDeploymentFeed {
  headHeight: number
  scannedBlocks: number
  stats: {
    total: number
    successful: number
    failed: number
    uniqueDeployers: number
    uniqueContracts: number
  }
  spotlight: ContractDeployment | null
  items: ContractDeployment[]
}

export interface ContractDetailResponse {
  address: Address
  profile: ContractProfile
  txs: TxSummary[]
}

export interface ContractCodeResponse {
  address: Address
  code: string | null
  codeHash: string | null
}

export interface ContractVerificationSubmitRequest {
  address: Address
  contractName?: string
  language: string
  compiler?: string
  compilerVersion?: string
  optimizationEnabled?: boolean
  optimizationRuns?: number
  vmTarget?: string
  constructorArgs?: string
  sourceCode?: string
  sources?: Record<string, string>
  abi?: unknown
  metadataJson?: unknown
  standardJsonInput?: unknown
  buildArtifact?: unknown
}

export interface ContractVerificationJob {
  jobId: string
  address: Address
  status: VerificationJobState
  submittedAt: number
  completedAt?: number | null
  error?: string | null
  result?: ContractVerificationRecord | null
}
