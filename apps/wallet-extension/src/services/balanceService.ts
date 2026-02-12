import { validateAddress } from '../core/crypto/address';
import { RpcClient } from '../core/rpc/client';

const DEFAULT_DECIMALS = 9n;
const DEBUG_BALANCE = false;

export interface BalanceDebugState {
  lastBalanceResponse?: unknown;
  lastPingResponse?: unknown;
  lastBalanceError?: string | null;
  lastPingError?: string | null;
  lastBalanceFetchedAt?: number | null;
}

const balanceDebugState: BalanceDebugState = {
  lastBalanceResponse: null,
  lastPingResponse: null,
  lastBalanceError: null,
  lastPingError: null,
  lastBalanceFetchedAt: null,
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
  chainId: number
): Promise<bigint> {
  if (!/^anim1[0-9a-z]+$/.test(address) || !validateAddress(address)) {
    throw new Error('Invalid wallet address');
  }

  const client = new RpcClient([rpcUrl]);
  const rpcChainId = await client.getChainId();
  if (rpcChainId !== chainId) {
    throw new Error(`Network mismatch: expected chain_id ${chainId}, got ${rpcChainId}`);
  }

  let raw: unknown;
  try {
    raw = await client.call('state.getBalance', [address, 'latest']);
    const parsed = parseBalanceResult(raw);

    balanceDebugState.lastBalanceResponse = raw;
    balanceDebugState.lastBalanceError = null;
    balanceDebugState.lastBalanceFetchedAt = Date.now();

    debugLog('state.getBalance response', {
      address,
      rpcUrl,
      chainId,
      raw,
    });

    return parsed;
  } catch (error: any) {
    balanceDebugState.lastBalanceResponse = raw;
    balanceDebugState.lastBalanceError = error?.message || 'Unknown balance error';
    balanceDebugState.lastBalanceFetchedAt = Date.now();
    throw error;
  }
}

export async function getBalance(
  address: string,
  options: { rpcUrl: string; chainId: number }
): Promise<bigint> {
  return getBalanceBaseUnits(address, options.rpcUrl, options.chainId);
}
