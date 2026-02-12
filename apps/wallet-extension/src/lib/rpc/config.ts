import { DEFAULT_MAINNET_RPC_URL } from '../../types/network';

const RPC_OVERRIDE_KEY = 'rpc_url_override';
const LEGACY_RPC_KEYS = ['rpcUrl', 'rpc_url', 'rpcEndpoint', 'rpc_url_custom'];

export const DEFAULT_RPC = normalizeRpcUrl(DEFAULT_MAINNET_RPC_URL);

let rpcOverrideCache: string | null = null;

export function normalizeRpcUrl(input: string): string {
  const trimmed = input.trim();
  const url = new URL(trimmed);

  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error('RPC URL must use http:// or https://');
  }

  if (!url.pathname || url.pathname === '/') {
    url.pathname = '/rpc';
  }

  url.pathname = url.pathname.replace(/\/+$/, '') || '/rpc';

  return url.toString();
}

export function validateRpcUrl(input: string): { normalizedUrl: string; warning?: string } {
  const normalizedUrl = normalizeRpcUrl(input);
  const url = new URL(normalizedUrl);

  let warning: string | undefined;
  if (url.protocol === 'http:' && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) {
    warning = 'Warning: HTTP RPC endpoints are insecure on public networks. Prefer HTTPS when available.';
  }

  return { normalizedUrl, warning };
}

async function readStorage(keys: string | string[]): Promise<Record<string, any>> {
  return chrome.storage.local.get(keys);
}

export async function initializeRpcConfig(): Promise<void> {
  await getRpcUrl();
}

export function getEffectiveRpcUrl(defaultRpcUrl?: string): string {
  if (rpcOverrideCache) {
    return rpcOverrideCache;
  }

  if (defaultRpcUrl) {
    return normalizeRpcUrl(defaultRpcUrl);
  }

  return DEFAULT_RPC;
}

export async function getRpcUrl(defaultRpcUrl?: string): Promise<string> {
  const result = await readStorage([RPC_OVERRIDE_KEY, ...LEGACY_RPC_KEYS]);

  let override = result[RPC_OVERRIDE_KEY] ?? null;
  if (!override) {
    for (const legacyKey of LEGACY_RPC_KEYS) {
      if (result[legacyKey]) {
        override = result[legacyKey];
        break;
      }
    }

    if (override) {
      const { normalizedUrl } = validateRpcUrl(override);
      await chrome.storage.local.set({ [RPC_OVERRIDE_KEY]: normalizedUrl });
      await chrome.storage.local.remove(LEGACY_RPC_KEYS);
      override = normalizedUrl;
    }
  }

  if (override) {
    const { normalizedUrl } = validateRpcUrl(override);
    rpcOverrideCache = normalizedUrl;
    return normalizedUrl;
  }

  rpcOverrideCache = null;
  return defaultRpcUrl ? normalizeRpcUrl(defaultRpcUrl) : DEFAULT_RPC;
}

export async function setRpcUrl(url: string): Promise<void> {
  const { normalizedUrl } = validateRpcUrl(url);
  await chrome.storage.local.set({ [RPC_OVERRIDE_KEY]: normalizedUrl });
  rpcOverrideCache = normalizedUrl;
}

export async function resetRpcUrl(): Promise<void> {
  await chrome.storage.local.remove(RPC_OVERRIDE_KEY);
  rpcOverrideCache = null;
}

export { RPC_OVERRIDE_KEY };
