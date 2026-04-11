export type Hash = `0x${string}` | string
export type Address = `anim1${string}` | string

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
}

export interface AddressSummary {
  address: Address
  confirmedBalance?: string | null
  pendingBalance?: string | null
  txs: TxSummary[]
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
