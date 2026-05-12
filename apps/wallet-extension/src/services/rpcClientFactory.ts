import { RpcClient } from '../core/rpc/client';
import { getEffectiveRpcUrl, getRpcUrl, normalizeRpcUrl } from './rpcConfig';

let currentClient: RpcClient | null = null;
let currentRpcUrlsKey: string | null = null;

function normalizeRpcUrlList(urls: string[]): string[] {
  const normalized: string[] = [];
  const seen = new Set<string>();

  for (const rawUrl of urls) {
    if (typeof rawUrl !== 'string' || rawUrl.trim().length === 0) continue;
    let url: string;
    try {
      url = normalizeRpcUrl(rawUrl);
    } catch {
      continue;
    }
    if (seen.has(url)) continue;
    seen.add(url);
    normalized.push(url);
  }

  return normalized;
}

function rpcUrlsKey(urls: string[]): string {
  return urls.join('|');
}

export async function getRpcClient(defaultRpcUrl?: string | string[]): Promise<RpcClient> {
  const defaultUrls = Array.isArray(defaultRpcUrl)
    ? defaultRpcUrl
    : (defaultRpcUrl ? [defaultRpcUrl] : []);

  const primaryRpcUrl = await getRpcUrl(defaultUrls[0]);
  const fallbackUrls = normalizeRpcUrlList(defaultUrls.slice(1));
  const rpcUrls = normalizeRpcUrlList([primaryRpcUrl, ...fallbackUrls]);

  const nextKey = rpcUrlsKey(rpcUrls);
  if (!currentClient || currentRpcUrlsKey !== nextKey) {
    currentClient = new RpcClient(rpcUrls);
    currentRpcUrlsKey = nextKey;
  }

  return currentClient;
}

export function recreateRpcClient(rpcUrl?: string): RpcClient {
  const targetRpcUrl = rpcUrl || getEffectiveRpcUrl();
  const rpcUrls = normalizeRpcUrlList([targetRpcUrl]);
  currentClient = new RpcClient(rpcUrls);
  currentRpcUrlsKey = rpcUrlsKey(rpcUrls);
  return currentClient;
}

export function clearRpcClient(): void {
  currentClient = null;
  currentRpcUrlsKey = null;
}
