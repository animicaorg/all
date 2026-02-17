import { validateAddress } from '../core/crypto/address';
import { RpcClient } from '../core/rpc/client';

const DEFAULT_DECIMALS = 9n;
const DEBUG_BALANCE = true; // Always enabled for troubleshooting

export interface BalanceDebugState {
  lastBalanceResponse?: unknown;
  lastPingResponse?: unknown;
  lastBalanceError?: string | null;
  lastPingError?: string | null;
  lastBalanceFetchedAt?: number | null;
  lastBalanceRequest?: {
    requestId?: string;
    address: string;
    rpcUrl: string;
    chainId: number;
    timestamp: number;
  };
}

const balanceDebugState: BalanceDebugState = {
  lastBalanceResponse: null,
  lastPingResponse: null,
  lastBalanceError: null,
  lastPingError: null,
  lastBalanceFetchedAt: null,
  lastBalanceRequest: undefined,
};

function debugLog(message: string, data?: unknown): void {
  if (!DEBUG_BALANCE) return;
  console.debug(`[balance-service] ${message}`, data);
}

export function setLastPingDebug(rawResponse: unknown, error: string | null = null): void {
  balanceDebugState.lastPingResponse = rawResponse;
  balanceDebugState.lastPingError = error;
}

export function getBalanceDebugState(): BalanceDebugState {
  return { ...balanceDebugState };
}

function parseBalanceResult(result: unknown): bigint {
  if (typeof result === 'bigint') return result;

  if (typeof result === 'number') {
    if (!Number.isFinite(result)) {
      throw new Error('Invalid balance number');
    }
    return BigInt(Math.floor(result));
  }

  if (typeof result === 'string') {
    const normalized = result.trim();
    if (!normalized) {
      throw new Error('Empty balance value');
    }
    if (/^0x[0-9a-f]+$/i.test(normalized)) {
      return BigInt(normalized);
    }
    return BigInt(normalized);
  }

  if (result && typeof result === 'object') {
    const nested = result as Record<string, unknown>;
    if (nested.balance !== undefined) {
      return parseBalanceResult(nested.balance);
    }
    if (nested.amount !== undefined) {
      return parseBalanceResult(nested.amount);
    }
  }

  throw new Error('Unsupported balance value type');
}

export function parseBaseUnits(value: unknown): bigint {
  return parseBalanceResult(value);
}

export function formatBalance(baseUnits: bigint, decimals = Number(DEFAULT_DECIMALS)): string {
  const value = parseBalanceResult(baseUnits);
  const divisor = 10n ** BigInt(decimals);
  const sign = value < 0n ? '-' : '';
  const abs = value < 0n ? -value : value;
  const whole = abs / divisor;
  const fraction = abs % divisor;

  return `${sign}${whole.toLocaleString()}.${fraction.toString().padStart(decimals, '0')}`;
}

export async function getBalanceBaseUnits(
  address: string,
  rpcUrl: string,
  chainId: number,
  options: { signal?: AbortSignal; requestId?: string } = {}
): Promise<bigint> {
  if (!/^anim1[0-9a-z]+$/.test(address) || !validateAddress(address)) {
    throw new Error('Invalid wallet address');
  }

  const client = new RpcClient([rpcUrl]);
  const rpcChainId = await client.call('chain.getChainId', [], undefined, { signal: options.signal });
  const normalizedChainId = typeof rpcChainId === 'number' ? rpcChainId : Number(rpcChainId);
  if (normalizedChainId !== chainId) {
    throw new Error(`Network mismatch: expected chain_id ${chainId}, got ${normalizedChainId}`);
  }

  let raw: unknown;
  try {
    // Store request info for debugging
    balanceDebugState.lastBalanceRequest = {
      address,
      rpcUrl,
      chainId,
      requestId: options.requestId,
      timestamp: Date.now(),
    };

    debugLog('Calling state.getBalance', {
      address,
      rpcUrl,
      chainId,
    });

    let retries = 0;
    const maxRetries = 2;
    while (true) {
      try {
        raw = await client.call('state.getBalance', [address, 'latest'], undefined, { signal: options.signal });
        break;
      } catch (error) {
        retries += 1;
        if (retries > maxRetries) throw error;
        const delayMs = 150 * (2 ** (retries - 1));
        await new Promise((resolve, reject) => {
          const t = setTimeout(resolve, delayMs);
          options.signal?.addEventListener('abort', () => {
            clearTimeout(t);
            reject(new Error('Balance request aborted'));
          }, { once: true });
        });
      }
    }
    
    debugLog('state.getBalance raw response', {
      address,
      raw,
      rawType: typeof raw,
    });

    // Handle both direct string response and object-wrapped response
    // Some RPC servers/versions may return {"balance": "0x..."} instead of just "0x..."
    // This matches the defensive handling in explorer2's rpcChainClient.ts
    let balanceValue = raw;
    if (typeof raw === 'object' && raw !== null && 'balance' in raw) {
      const obj = raw as Record<string, unknown>;
      balanceValue = obj.balance;
      debugLog('Unwrapped balance from object response', { original: raw, unwrapped: balanceValue });
    }

    const parsed = parseBalanceResult(balanceValue);

    balanceDebugState.lastBalanceResponse = raw;
    balanceDebugState.lastBalanceError = null;
    balanceDebugState.lastBalanceFetchedAt = Date.now();

    debugLog('state.getBalance parsed result', {
      address,
      rpcUrl,
      chainId,
      raw,
      parsed: parsed.toString(),
    });

    return parsed;
  } catch (error: any) {
    const errorMsg = error?.message || 'Unknown balance error';
    balanceDebugState.lastBalanceResponse = raw;
    balanceDebugState.lastBalanceError = errorMsg;
    balanceDebugState.lastBalanceFetchedAt = Date.now();
    
    console.error('[balance-service] getBalance failed:', {
      address,
      rpcUrl,
      chainId,
      error: errorMsg,
      raw,
    });
    
    throw error;
  }
}

export async function getBalance(
  address: string,
  options: { rpcUrl: string; chainId: number; signal?: AbortSignal; requestId?: string }
): Promise<bigint> {
  return getBalanceBaseUnits(address, options.rpcUrl, options.chainId, {
    signal: options.signal,
    requestId: options.requestId,
  });
}
