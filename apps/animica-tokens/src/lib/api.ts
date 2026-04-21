import type {
  LiquidityRecord,
  PoolRecord,
  PortfolioResponse,
  QuoteResponse,
  ReportRecord,
  StatsResponse,
  SwapRecord,
  TokenRecord
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "";
const ADMIN_KEY = import.meta.env.VITE_ANIMICA_TOKENS_ADMIN_KEY || "";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(ADMIN_KEY ? { "x-admin-key": ADMIN_KEY } : {}),
      ...(init?.headers || {})
    },
    ...init
  });
  const json = await res.json();
  if (!res.ok) {
    throw new Error(json?.error || `Request failed: ${res.status}`);
  }
  return json as T;
}

export async function fetchStats(): Promise<StatsResponse> {
  return req<StatsResponse>("/api/stats");
}

export async function fetchTokens(search = ""): Promise<TokenRecord[]> {
  const q = new URLSearchParams();
  if (search.trim()) q.set("q", search.trim());
  return req<TokenRecord[]>(`/api/tokens?${q.toString()}`);
}

export async function fetchToken(id: string): Promise<{ token: TokenRecord; swaps: SwapRecord[]; liquidity: LiquidityRecord[] }> {
  return req(`/api/tokens/${encodeURIComponent(id)}`);
}

export async function fetchPools(): Promise<PoolRecord[]> {
  return req<PoolRecord[]>("/api/pools");
}

export async function fetchPool(id: string): Promise<{ pool: PoolRecord; swaps: SwapRecord[]; liquidity: LiquidityRecord[] }> {
  return req(`/api/pools/${encodeURIComponent(id)}`);
}

export async function fetchPortfolio(address: string): Promise<PortfolioResponse> {
  return req(`/api/portfolio/${encodeURIComponent(address)}`);
}

export async function fetchReports(): Promise<ReportRecord[]> {
  return req("/api/admin/reports");
}

export async function hideToken(tokenId: string, hidden: boolean): Promise<{ ok: true }> {
  return req(`/api/admin/tokens/${encodeURIComponent(tokenId)}/visibility`, {
    method: "POST",
    body: JSON.stringify({ hidden })
  });
}

export async function reportToken(tokenId: string, reason: string, reporter: string): Promise<{ ok: true }> {
  return req("/api/reports", {
    method: "POST",
    body: JSON.stringify({ tokenId, reason, reporter })
  });
}

export async function uploadMedia(file: File): Promise<{ cid: string; uri: string; gatewayUrl: string }> {
  const body = new FormData();
  body.append("file", file);
  const res = await fetch(`${API_BASE}/api/upload/media`, { method: "POST", body });
  const json = await res.json();
  if (!res.ok) {
    throw new Error(json?.error || `Upload failed: ${res.status}`);
  }
  return json;
}

export async function uploadMetadata(payload: Record<string, unknown>): Promise<{ cid: string; uri: string; gatewayUrl: string }> {
  return req("/api/upload/metadata", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function launchToken(payload: Record<string, unknown>): Promise<{ token: TokenRecord }> {
  return req("/api/tokens/launch", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function createPool(payload: Record<string, unknown>): Promise<{ pool: PoolRecord }> {
  return req("/api/pools/create", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function quote(payload: Record<string, unknown>): Promise<QuoteResponse> {
  return req("/api/dex/quote", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function swapExactIn(payload: Record<string, unknown>): Promise<{ swap: SwapRecord }> {
  return req("/api/dex/swap", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function addLiquidity(payload: Record<string, unknown>): Promise<{ liquidity: LiquidityRecord }> {
  return req("/api/dex/liquidity/add", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function removeLiquidity(payload: Record<string, unknown>): Promise<{ liquidity: LiquidityRecord }> {
  return req("/api/dex/liquidity/remove", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}
