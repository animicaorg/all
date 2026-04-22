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
      // keep trying methods
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
      // try next
    }
  }

  return null;
}

export async function signTypedPayload(payload: Record<string, unknown>, account?: string): Promise<string | null> {
  const provider = getAnimicaProvider();
  if (!provider) return null;

  for (const method of ['animica_signTypedData', 'eth_signTypedData_v4']) {
    try {
      const result = await provider.request({ method, params: [account ?? '', payload] });
      if (typeof result === 'string' && result.length > 0) return result;
    } catch {
      // try next
    }
  }

  return null;
}

export async function addUsdanToWallet(input: {
  tokenAddress: string;
  symbol?: string;
  decimals?: number;
  image?: string;
}): Promise<boolean> {
  const provider = getAnimicaProvider();
  if (!provider) return false;

  const payload = {
    type: 'ANM20',
    options: {
      address: input.tokenAddress,
      symbol: input.symbol ?? 'USDAN',
      decimals: input.decimals ?? 6,
      image: input.image ?? ''
    }
  };

  for (const method of ['animica_watchAsset', 'animica_addToken']) {
    try {
      const result = await provider.request({ method, params: [payload] });
      if (result === true || result === null || result === undefined || result === 'ok') return true;
    } catch {
      // try next
    }
  }

  return false;
}

export function onAccountsChanged(handler: (accounts: string[]) => void): () => void {
  const provider = getAnimicaProvider();
  if (!provider?.on) return () => {};

  const wrapped = (accounts: unknown) => {
    if (Array.isArray(accounts)) handler(accounts.map(String));
  };
  provider.on('accountsChanged', wrapped);

  return () => {
    provider.removeListener?.('accountsChanged', wrapped);
  };
}
