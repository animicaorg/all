export type PoolSummary = {
  pool_name: string;
  network: string;
  height: number;
  last_block_hash: string;
  pool_hashrate: number;
  hashrate_series: [string, number][];
  hashrate_1m: number;
  hashrate_15m: number;
  hashrate_1h: number;
  num_miners: number;
  num_workers: number;
  round_duration_seconds: number;
  round_shares: number;
  round_estimated_reward: string;
  uptime_seconds: number;
  stratum_endpoint: string;
  last_update: string;
  latest_block?: { height: number; hash: string; timestamp: string | null; found_by_pool: boolean };
};

export type Miner = {
  worker_id: string;
  worker_name: string;
  address: string;
  hashrate_1m: number;
  hashrate_15m: number;
  hashrate_1h: number;
  last_share_at: number | null;
  difficulty: number;
  shares_accepted: number;
  shares_rejected: number;
};

export type MinerDetail = {
  address: string;
  worker_name: string;
  hashrate_timeseries: [string, number][];
  last_share: {
    time: string | null;
    difficulty: number | null;
    status: string | null;
  };
  shares_accepted: number;
  shares_rejected: number;
  current_difficulty: number;
  connected_since: string | null;
};

export type BlockRow = {
  height: number;
  hash: string;
  timestamp: string;
  found_by_pool: boolean;
  reward: string;
  tx_count?: number;
};

const API_URL =
  import.meta.env.VITE_POOL_API_URL || import.meta.env.VITE_STRATUM_API_URL || 'http://127.0.0.1:8550';

async function request<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  getPoolSummary: () => request<PoolSummary>('/summary'),
  getMiners: () => request<{ items: Miner[]; total: number }>('/miners'),
  getMinerDetail: (workerId: string) => request<MinerDetail>(`/miners/${workerId}`),
  getRecentBlocks: () => request<{ items: BlockRow[]; total: number }>('/blocks'),
  getHealth: () => request<{ status: string; uptime: number }>('/healthz'),
};
