export type AnimicaProvider = {
  request: (args: { method: string; params?: unknown[] | Record<string, unknown> }) => Promise<unknown>;
};

export function getAnimicaProvider(): AnimicaProvider | null {
  return (window as unknown as { animica?: AnimicaProvider }).animica || null;
}

export async function connectWallet(): Promise<string[]> {
  const provider = getAnimicaProvider();
  if (!provider) {
    return [];
  }
  const accounts = await provider.request({ method: "animica_requestAccounts" });
  if (!Array.isArray(accounts)) {
    return [];
  }
  return accounts.map(String);
}

export async function getWalletAccounts(): Promise<string[]> {
  const provider = getAnimicaProvider();
  if (!provider) {
    return [];
  }
  const accounts = await provider.request({ method: "animica_accounts" });
  if (!Array.isArray(accounts)) {
    return [];
  }
  return accounts.map(String);
}

export async function watchTokenAsset(params: {
  address: string;
  symbol: string;
  decimals: number;
  image?: string;
  name?: string;
}): Promise<boolean> {
  const provider = getAnimicaProvider();
  if (!provider) return false;

  const payload = {
    type: "ANM20",
    options: {
      address: params.address,
      symbol: params.symbol,
      decimals: params.decimals,
      image: params.image || "",
      name: params.name || params.symbol
    }
  };

  const methods = ["animica_watchAsset", "animica_addToken"];
  for (const method of methods) {
    try {
      const result = await provider.request({ method, params: [payload] });
      if (result === true || result === "ok" || result === null || result === undefined) {
        return true;
      }
    } catch {
      // Try alternate method.
    }
  }
  return false;
}
