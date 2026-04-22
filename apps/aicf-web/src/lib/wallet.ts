export type AnimicaProvider = {
  request: (args: { method: string; params?: unknown[] | Record<string, unknown> }) => Promise<unknown>;
  on?: (event: string, handler: (...args: unknown[]) => void) => void;
  removeListener?: (event: string, handler: (...args: unknown[]) => void) => void;
};

declare global {
  interface Window {
    animica?: AnimicaProvider;
  }
}

export function getAnimicaProvider(): AnimicaProvider | null {
  return window.animica ?? null;
}

export async function connectWallet(): Promise<string[]> {
  const provider = getAnimicaProvider();
  if (!provider) return [];
  const accounts = await provider.request({ method: 'animica_requestAccounts' });
  return Array.isArray(accounts) ? accounts.map(String) : [];
}

export async function getAccounts(): Promise<string[]> {
  const provider = getAnimicaProvider();
  if (!provider) return [];
  const accounts = await provider.request({ method: 'animica_accounts' });
  return Array.isArray(accounts) ? accounts.map(String) : [];
}

export async function getChainId(): Promise<number | null> {
  const provider = getAnimicaProvider();
  if (!provider) return null;

  for (const method of ['animica_chainId', 'eth_chainId']) {
    try {
      const value = await provider.request({ method });
      if (typeof value === 'number') return value;
      if (typeof value === 'string') {
        if (value.startsWith('0x')) return parseInt(value, 16);
        const parsed = Number(value);
        if (Number.isFinite(parsed)) return parsed;
      }
    } catch {
      // continue
    }
  }

  return null;
}

export async function signMessage(message: string, account?: string): Promise<string | null> {
  const provider = getAnimicaProvider();
  if (!provider) return null;

  const attempts = [
    { method: 'animica_signMessage', params: [{ message }] },
    { method: 'provider_signMessage', params: [{ message }] },
    { method: 'personal_sign', params: [message, account ?? ''] }
  ];

  for (const req of attempts) {
    try {
      const result = await provider.request(req);
      if (typeof result === 'string' && result.length > 0) return result;
    } catch {
      // continue
    }
  }

  return null;
}

export async function getAnmBalance(address: string): Promise<string | null> {
  const provider = getAnimicaProvider();
  if (!provider) return null;

  for (const method of ['animica_getBalance', 'eth_getBalance']) {
    try {
      const result = await provider.request({ method, params: [address, 'latest'] });
      if (typeof result === 'string') {
        if (result.startsWith('0x')) return BigInt(result).toString();
        return result;
      }
      if (typeof result === 'number') return String(result);
    } catch {
      // continue
    }
  }

  return null;
}

export async function sendContractCall(payload: {
  from: string;
  contractAddress: string;
  method: string;
  args: Record<string, unknown>;
}): Promise<string | null> {
  const provider = getAnimicaProvider();
  if (!provider) return null;

  const txPayload = {
    from: payload.from,
    to: payload.contractAddress,
    data: JSON.stringify({ method: payload.method, args: payload.args }),
    value: '0x0'
  };

  for (const method of ['animica_sendTransaction', 'eth_sendTransaction']) {
    try {
      const result = await provider.request({ method, params: [txPayload] });
      if (typeof result === 'string' && result.length > 0) return result;
    } catch {
      // continue
    }
  }

  return null;
}

export async function sendTransaction(payload: Record<string, unknown>): Promise<string | null> {
  const provider = getAnimicaProvider();
  if (!provider) return null;

  for (const method of ['animica_sendTransaction', 'eth_sendTransaction']) {
    try {
      const result = await provider.request({ method, params: [payload] });
      if (typeof result === 'string' && result.length > 0) return result;
    } catch {
      // continue
    }
  }

  return null;
}
