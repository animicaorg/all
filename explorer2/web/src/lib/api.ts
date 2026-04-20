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
}
