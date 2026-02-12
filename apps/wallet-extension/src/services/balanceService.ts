import { validateAddress } from '../core/crypto/address';
import { RpcClient } from '../core/rpc/client';

const DEFAULT_DECIMALS = 9n;
const DEBUG_BALANCE = false;

function debugLog(message: string, data?: unknown): void {
  if (!DEBUG_BALANCE) return;
  console.debug(`[balance-service] ${message}`, data);
}

export function parseBaseUnits(value: unknown): bigint {
  if (typeof value === 'bigint') {
    return value;
  }

  if (typeof value === 'number') {
    if (!Number.isFinite(value)) {
      throw new Error('Invalid balance number');
    }
    return BigInt(Math.trunc(value));
  }

  if (typeof value === 'string') {
    const normalized = value.trim();
    if (!normalized) {
      throw new Error('Empty balance value');
    }
    return BigInt(normalized);
  }

  throw new Error('Unsupported balance value type');
}

export function formatBalance(baseUnits: bigint, decimals = Number(DEFAULT_DECIMALS)): string {
  const value = parseBaseUnits(baseUnits);
  const divisor = 10n ** BigInt(decimals);
  const sign = value < 0n ? '-' : '';
  const abs = value < 0n ? -value : value;
  const whole = abs / divisor;
  const fraction = abs % divisor;

  return `${sign}${whole.toLocaleString()}.${fraction.toString().padStart(decimals, '0')}`;
}

export async function getBalance(
  address: string,
  options: { rpcUrl: string; chainId: number }
): Promise<bigint> {
  if (!validateAddress(address)) {
    throw new Error('Invalid wallet address');
  }

  const client = new RpcClient([options.rpcUrl]);
  const rpcChainId = await client.getChainId();
  if (rpcChainId !== options.chainId) {
    throw new Error(`RPC chain mismatch (expected ${options.chainId}, got ${rpcChainId})`);
  }

  const raw = await client.call('state.getBalance', [address, 'latest']);
  debugLog('state.getBalance response', {
    address,
    rpcUrl: options.rpcUrl,
    chainId: options.chainId,
    raw,
  });
  return parseBaseUnits(raw);
}
