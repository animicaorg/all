import { formatBalance, parseBaseUnits } from './balanceService';

export function formatANM(baseUnits: bigint | string | number): string {
  return formatBalance(parseBaseUnits(baseUnits));
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
