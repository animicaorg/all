import type {
  AddressSummary,
  BlockDetail,
  BlockSummary,
  HeadView,
  MempoolView,
  TxDetail
} from '@animica/explorer2-shared'

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
    throw new Error(text || `Request failed: ${res.status}`)
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
    apiGet<MempoolView>(`/api/mempool?limit=${limit}${cursor ? `&cursor=${cursor}` : ''}`)
}
