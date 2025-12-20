export type Hash = `0x${string}` | string
export type Address = `anim1${string}` | string

export interface HeadView {
  height: number
  hash: Hash
  time: number
  chainId?: number
}

export interface BlockSummary {
  height: number
  hash: Hash
  time: number
  txCount: number
  miner?: Address | null
}

export interface BlockDetail {
  height: number
  hash: Hash
  parentHash?: Hash
  time: number
  chainId?: number
  difficulty?: string | number | null
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
  status: 'pending' | 'confirmed' | 'failed'
  blockHash?: Hash
  blockHeight?: number
  from?: Address
  to?: Address
  value?: string
  gasUsed?: string
  feePaid?: string
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
