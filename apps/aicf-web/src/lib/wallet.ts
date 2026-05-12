export type AnimicaProvider = {
  request: (args: { method: string; params?: unknown[] | Record<string, unknown> }) => Promise<unknown>;
  on?: (event: string, handler: (...args: unknown[]) => void) => void;
  removeListener?: (event: string, handler: (...args: unknown[]) => void) => void;
};

type RequestAttempt = {
  method: string;
  params?: unknown[] | Record<string, unknown>;
};

declare global {
  interface Window {
    animica?: AnimicaProvider;
  }
}

export function getAnimicaProvider(): AnimicaProvider | null {
  if (typeof window === 'undefined') return null;
  return window.animica ?? null;
}

function normalizeErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === 'string') return error;
  if (error && typeof error === 'object' && typeof (error as any).message === 'string') return (error as any).message;
  return String(error);
}

function toAddressArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value
        .map((entry) => String(entry).trim())
        .filter((entry) => entry.length > 0)
    : [];
}

async function requestWithFallback(provider: AnimicaProvider, attempts: RequestAttempt[]): Promise<unknown> {
  let lastError: Error | null = null;

  for (const attempt of attempts) {
    try {
      return await provider.request({ method: attempt.method, params: attempt.params });
    } catch (error) {
      lastError = new Error(normalizeErrorMessage(error));
    }
  }

  if (lastError) throw lastError;
  throw new Error('Wallet request failed');
}

export async function waitForAnimicaProvider(timeoutMs: number = 4_000): Promise<AnimicaProvider | null> {
  const existing = getAnimicaProvider();
  if (existing) return existing;
  if (typeof window === 'undefined') return null;

  return new Promise((resolve) => {
    let done = false;

    const finish = (provider: AnimicaProvider | null) => {
      if (done) return;
      done = true;
      window.clearTimeout(timeoutId);
      window.clearInterval(pollId);
      window.removeEventListener('animica#initialized', onInitialized as EventListener);
      resolve(provider);
    };

    const onInitialized = () => {
      finish(getAnimicaProvider());
    };

    const pollId = window.setInterval(() => {
      const provider = getAnimicaProvider();
      if (provider) finish(provider);
    }, 150);

    const timeoutId = window.setTimeout(() => finish(getAnimicaProvider()), timeoutMs);
    window.addEventListener('animica#initialized', onInitialized as EventListener, { once: false });
  });
}

export async function connectWallet(): Promise<string[]> {
  const provider = await waitForAnimicaProvider();
  if (!provider) {
    throw new Error('Animica wallet provider not detected. Enable the extension and reload this page.');
  }

  const accounts = await requestWithFallback(provider, [
    { method: 'animica_requestAccounts' },
    { method: 'provider_requestAccounts' },
    { method: 'eth_requestAccounts' },
  ]);
  return toAddressArray(accounts);
}

export async function getAccounts(): Promise<string[]> {
  const provider = await waitForAnimicaProvider(1_500);
  if (!provider) return [];

  const accounts = await requestWithFallback(provider, [
    { method: 'animica_accounts' },
    { method: 'provider_getAccounts' },
    { method: 'eth_accounts' },
  ]);
  return toAddressArray(accounts);
}

export async function getChainId(): Promise<number | null> {
  const provider = await waitForAnimicaProvider(1_500);
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
  const provider = await waitForAnimicaProvider(1_500);
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
  const provider = await waitForAnimicaProvider(1_500);
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
  const provider = await waitForAnimicaProvider(1_500);
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
  const provider = await waitForAnimicaProvider(1_500);
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
