import type {
  AddressSummary,
  BlockDetail,
  BlockSummary,
  ContractCodeResponse,
  ContractDetailResponse,
  ContractDeploymentFeed,
  ContractProfile,
  ContractVerificationJob,
  ContractVerificationRecord,
  ContractVerificationSubmitRequest,
  HeadView,
  MempoolView,
  RichListResponse,
  RichListSummary,
  TokenInfo,
  TokenPricePoint,
  TxDetail
} from '@animica/explorer2-shared'
import { formatError } from './rpcUtils'

interface HeadResponse {
  head: HeadView
  stats: {
    peerCount?: number
    inboundPeers?: number | null
    outboundPeers?: number | null
    mempoolSize?: number | null
    tps?: number | null
    avgBlockTime?: number | null
  }
  thetaHistory?: Array<{
    height: number
    time: number
    thetaMicro: number | null
  }>
}

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) {
    const text = await res.text()
    let msg = text || `Request failed: ${res.status}`
    try {
      const json = JSON.parse(text)
      if (json?.message) msg = json.message
    } catch { /* not JSON */ }
    const fe = formatError(msg)
    throw Object.assign(new Error(fe.message), { kind: fe.kind, hint: fe.hint, remediation: fe.remediation })
  }
  return res.json() as Promise<T>
}

export const api = {
  getHead: () => apiGet<HeadResponse>('/api/head'),
  getBlocks: (limit = 20, cursor?: string) =>
    apiGet<{ items: BlockSummary[]; nextCursor: string | null }>(
      `/api/blocks?limit=${limit}${cursor ? `&cursor=${cursor}` : ''}`
    ),
  getBlock: (hashOrHeight: string) => apiGet<BlockDetail>(`/api/block/${hashOrHeight}`),
  getTx: (hash: string) => apiGet<TxDetail>(`/api/tx/${hash}`),
  getAddress: (address: string, limit = 20, cursor?: string) =>
    apiGet<AddressSummary>(`/api/address/${encodeURIComponent(address)}?limit=${limit}${cursor ? `&cursor=${cursor}` : ''}`),
  getMempool: (limit = 50, cursor?: string) =>
    apiGet<MempoolView>(`/api/mempool?limit=${limit}${cursor ? `&cursor=${cursor}` : ''}`),
  getContractDeployments: (limit = 24, scanBlocks = 240) =>
    apiGet<ContractDeploymentFeed>(`/api/contracts/deployments?limit=${limit}&scanBlocks=${scanBlocks}`),
  getContract: (address: string, limit = 20, cursor?: string) =>
    apiGet<ContractDetailResponse>(
      `/api/contracts/address/${encodeURIComponent(address)}?limit=${limit}${cursor ? `&cursor=${cursor}` : ''}`
    ),
  getContractByCreationTx: (txHash: string) =>
    apiGet<ContractProfile>(`/api/contracts/created-by/${encodeURIComponent(txHash)}`),
  getContractCode: (address: string) =>
    apiGet<ContractCodeResponse>(`/api/contracts/address/${encodeURIComponent(address)}/code`),
  getContractVerification: (address: string) =>
    apiGet<ContractVerificationRecord>(`/api/contracts/address/${encodeURIComponent(address)}/verification`),
  submitContractVerification: async (payload: ContractVerificationSubmitRequest) => {
    const res = await fetch('/api/contracts/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    if (!res.ok) {
      const text = await res.text()
      let msg = text || `Request failed: ${res.status}`
      try { msg = JSON.parse(text)?.message ?? msg } catch { /* not JSON */ }
      throw new Error(msg)
    }
    return res.json() as Promise<ContractVerificationJob>
  },
  getContractVerificationJob: (jobId: string) =>
    apiGet<ContractVerificationJob>(`/api/contracts/verify/${encodeURIComponent(jobId)}`),
  getRichList: (limit = 100, offset = 0) =>
    apiGet<RichListResponse>(`/api/richlist?limit=${limit}&offset=${offset}`),
  getRichListSummary: () =>
    apiGet<RichListSummary>('/api/richlist/summary'),

  // Extended endpoints
  getNetworkStatus: () =>
    apiGet<{ timestamp: string; services: Array<{ name: string; status: string; hint?: string; remediation?: string; detail?: unknown }> }>('/api/network/status'),
  getRpcDiscover: () =>
    apiGet<{ available: boolean; methods: string[]; version?: string; servers?: unknown[]; raw?: unknown; note?: string }>('/api/rpc/discover'),
  getAICFInfo: (address?: string) =>
    apiGet<{ available: boolean; status?: unknown; credits?: unknown; jobs?: unknown; plans?: unknown }>(
      `/api/aicf/info${address ? `?address=${encodeURIComponent(address)}` : ''}`
    ),
  // Network-wide AICF compute workers grouped by advertised tier (see
  // /api/aicf/workers in api/src/server.ts).
  getAICFWorkers: () =>
    apiGet<{
      available: boolean
      reason?: string
      total?: number
      by_tier: Record<string, number>
      workers: Array<{
        address: string
        tiers: string[]
        hardware?: {
          accelerator_preferred?: string
          ram_gb?: number
          gpus?: Array<{ name: string; vram_gb: number; backend: string }>
        }
        last_seen?: number
        jobs_completed?: number
      }>
    }>('/api/aicf/workers'),
  // Rolling-window AICF job stats: jobs/hr, latencies, top providers.
  getAICFJobStats: (windowMinutes = 60) =>
    apiGet<{
      available: boolean
      reason?: string
      window_minutes: number
      jobs_completed?: number
      jobs_failed?: number
      latency_p50_ms?: number | null
      latency_p95_ms?: number | null
      by_tier?: Record<string, number>
    }>(`/api/aicf/job-stats?window=${windowMinutes}`),
  getMiningInfo: () =>
    apiGet<{ available: boolean; status?: unknown; template?: unknown; metrics?: unknown }>('/api/mining/info'),
  getDAInfo: () =>
    apiGet<{ available: boolean; status?: unknown; quotas?: unknown }>('/api/da/info'),
  getDAHistory: (limit = 20) =>
    apiGet<unknown[]>(`/api/da/history?limit=${limit}`),
  getDABlob: (commitment: string) =>
    apiGet<unknown>(`/api/da/blob/${encodeURIComponent(commitment)}`),
  getDAProof: (commitment: string) =>
    apiGet<unknown>(`/api/da/proof/${encodeURIComponent(commitment)}`),
  putDABlob: async (namespace: string, data: string) => {
    const res = await fetch('/api/da/put', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ namespace, data }),
    })
    if (!res.ok) {
      const text = await res.text()
      let msg = text || `Request failed: ${res.status}`
      try { msg = JSON.parse(text)?.message ?? msg } catch { /* not JSON */ }
      throw new Error(msg)
    }
    return res.json()
  },
  getQuantumInfo: () =>
    apiGet<{ available: boolean; status?: unknown; workers?: unknown; jobs?: unknown; policy?: unknown }>('/api/quantum/info'),
  getDebugBundle: () =>
    apiGet<unknown>('/api/debug/bundle'),

  // ── ANM Instant (L2) ────────────────────────────────────────────────────────
  // The node exposes L2 over the same RPC endpoint (l2_* methods); the explorer
  // API surfaces them under /api/l2/* and returns { enabled: false } when the
  // connected node has L2 turned off (see api/src/server.ts).
  getL2Overview: () => apiGet<L2Overview>('/api/l2/overview'),
  getL2Status: () => apiGet<Record<string, unknown> & { enabled?: boolean }>('/api/l2/status'),
  getL2Tps: () => apiGet<L2Tps & { enabled?: boolean }>('/api/l2/tps'),
  getL2Batch: (n: number | string) => apiGet<L2BatchDetail>(`/api/l2/batch/${encodeURIComponent(String(n))}`),
  getL2Tx: (hash: string) => apiGet<L2TxDetail>(`/api/l2/tx/${encodeURIComponent(hash)}`),
  getL2Account: (address: string) =>
    apiGet<{ address: string; balance: string; nonce: number; pendingNonce: number; unit: string }>(
      `/api/l2/account/${encodeURIComponent(address)}`
    ),
  getL2StateRoot: () => apiGet<{ stateRoot?: string; enabled?: boolean }>('/api/l2/stateRoot'),
}

// ── L2 response shapes (mirror api/src/server.ts + rpc/methods/l2.py) ──────────

export interface L2Tps {
  ingressTotal?: number
  executedTotal?: number
  softConfirmedTotal?: number
  settledTotal?: number
  batchesTotal?: number
  sigVerificationsTotal?: number
  ingressTps?: number
  executedTps?: number
  softConfirmedTps?: number
  settledTps?: number
}

export interface L2ProofStatus {
  batch?: number
  hasProof?: boolean
  backend?: string | null
  publicInputsDigest?: string | null
}

export interface L2BatchHeader {
  number?: number
  l2ChainId?: number
  prevStateRoot?: string
  newStateRoot?: string
  transactionsRoot?: string
  receiptsRoot?: string
  escrowRoot?: string
  dataRoot?: string
  txCount?: number
  timestampMs?: number
  feesCollected?: string
  deposited?: string
  withdrawn?: string
  batchId?: string
}

export type L2BatchDetail = L2BatchHeader & { proof?: L2ProofStatus | null }

export interface L2TxDetail {
  txid?: string
  status?: string
  batch?: number
  receipt?: { txid?: string; batch?: number; status?: string; lifecycle?: string } | null
  reason?: string | null
  receivedMs?: number
  proof?: L2ProofStatus | null
  // Optional enriched fields (present when the sequencer record carries them).
  sender?: string
  recipient?: string
  from?: string
  to?: string
  amount?: string
  fee?: string
  nonce?: number
  kind?: string
  type?: string
  l1SettlementTx?: string
  l1Tx?: string
}

export interface L2Overview {
  enabled: boolean
  settlementMode?: string
  headBatch?: number
  latestStateRoot?: string | null
  latestBatch?: L2BatchHeader | null
  proofStatus?: L2ProofStatus | null
  tps?: L2Tps
  sigBackend?: string | null
  pending?: number
  anmLocked?: string
  l2Supply?: string
  totalL2Transactions?: number
  softConfirmedTotal?: number
  settledTotal?: number
  batchTransactions?: number
  compressionRatio?: number | null
  pendingProofs?: number
  deposits?: number
  withdrawals?: number
}

// ── Token tracker ─────────────────────────────────────────────────────────────

export async function fetchTokens(limit = 100): Promise<TokenInfo[]> {
  const res = await apiGet<{ tokens: TokenInfo[] }>(`/api/tokens?limit=${limit}`)
  return res.tokens ?? []
}

export async function fetchFeaturedTokens(limit = 50): Promise<TokenInfo[]> {
  const res = await apiGet<{ tokens: TokenInfo[] }>(`/api/tokens/featured?limit=${limit}`)
  return res.tokens ?? []
}

export async function searchTokens(query: string, limit = 50): Promise<TokenInfo[]> {
  const res = await apiGet<{ tokens: TokenInfo[] }>(
    `/api/tokens/search?q=${encodeURIComponent(query)}&limit=${limit}`
  )
  return res.tokens ?? []
}

export function fetchToken(address: string): Promise<TokenInfo> {
  return apiGet<TokenInfo>(`/api/tokens/${encodeURIComponent(address)}`)
}

export function fetchTokenHistory(address: string, range = '7d'): Promise<TokenPricePoint[]> {
  return apiGet<TokenPricePoint[]>(`/api/tokens/${encodeURIComponent(address)}/history?range=${range}`)
}
