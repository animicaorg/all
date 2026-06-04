// Provider registry. The mock is always present; real providers are added
// only when configured (see config.ts). Built lazily + memoized so env is
// loaded by first use.

import { buildRealProviders } from "./config";
import { mockProvider } from "./mock";
import type { Provider, ProviderName } from "./types";

let cache: Map<ProviderName, Provider> | null = null;

function build(): Map<ProviderName, Provider> {
  const m = new Map<ProviderName, Provider>();
  m.set("mock", mockProvider);
  for (const p of buildRealProviders()) m.set(p.name, p);
  return m;
}

function registry(): Map<ProviderName, Provider> {
  return (cache ??= build());
}

export function getProvider(name: ProviderName): Provider | undefined {
  return registry().get(name);
}

export function allProviders(): Provider[] {
  return [...registry().values()];
}

/** Drop the memoized registry — used by tests that toggle provider env vars. */
export function resetProviderRegistry(): void {
  cache = null;
}
