import type {
  AddressSummary,
  BlockDetail,
  BlockSummary,
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
    apiGet<AddressSummary>(`/api/address/${address}?limit=${limit}${cursor ? `&cursor=${cursor}` : ''}`),
  getMempool: (limit = 50, cursor?: string) =>
    apiGet<MempoolView>(`/api/mempool?limit=${limit}${cursor ? `&cursor=${cursor}` : ''}`),
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
