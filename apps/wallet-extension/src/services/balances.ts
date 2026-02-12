const DECIMALS = 9n;
const BASE_PER_ANM = 10n ** DECIMALS;

function parseBaseUnits(value: unknown): bigint {
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

export function formatANM(baseUnits: bigint | string | number): string {
  const value = parseBaseUnits(baseUnits);
  const sign = value < 0n ? '-' : '';
  const abs = value < 0n ? -value : value;
  const whole = abs / BASE_PER_ANM;
  const fraction = abs % BASE_PER_ANM;
  return `${sign}${whole.toString()}.${fraction.toString().padStart(Number(DECIMALS), '0')}`;
}

export async function getBalance(address: string): Promise<bigint> {
  const result = await chrome.runtime.sendMessage({
    method: 'wallet_getBalance',
    params: { address },
  });

  if (result?.error) {
    throw new Error(result.error);
  }

  const confirmed = result?.confirmed;
  return parseBaseUnits(confirmed);
}

export async function getBalances(addresses: string[]): Promise<Record<string, bigint>> {
  const uniqueAddresses = Array.from(new Set(addresses.filter(Boolean)));
  const entries = await Promise.all(
    uniqueAddresses.map(async (address) => [address, await getBalance(address)] as const)
  );

  return Object.fromEntries(entries);
}
