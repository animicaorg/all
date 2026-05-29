import type { WalletType } from "@launchpad/shared";
import { AnimicaExtensionAdapter } from "./extension";
import { AnimicaWebWalletAdapter } from "./web";
import { MockWalletAdapter } from "./mock";
import type { WalletAdapter } from "./types";

export interface WalletRegistryOptions {
  enableExtension: boolean;
  enableWebWallet: boolean;
  enableMock: boolean;
}

export class WalletRegistry {
  private adapters = new Map<WalletType, WalletAdapter>();

  constructor(opts: WalletRegistryOptions) {
    if (opts.enableExtension) this.register(new AnimicaExtensionAdapter());
    if (opts.enableWebWallet) this.register(new AnimicaWebWalletAdapter());
    if (opts.enableMock) this.register(new MockWalletAdapter());
  }

  register(adapter: WalletAdapter) {
    this.adapters.set(adapter.id, adapter);
  }

  list(): WalletAdapter[] {
    return Array.from(this.adapters.values());
  }

  get(id: WalletType): WalletAdapter | undefined {
    return this.adapters.get(id);
  }

  detectAvailable(): WalletAdapter[] {
    return this.list().filter((a) => {
      try {
        return Boolean(a.isAvailable());
      } catch {
        return false;
      }
    });
  }
}

export function defaultRegistryOptionsFromEnv(env: Record<string, string | undefined>): WalletRegistryOptions {
  const truthy = (v?: string) => v != null && /^(1|true|yes)$/i.test(v);
  return {
    enableExtension: env.NEXT_PUBLIC_ENABLE_ANIMICA_EXTENSION
      ? truthy(env.NEXT_PUBLIC_ENABLE_ANIMICA_EXTENSION)
      : true,
    enableWebWallet: env.NEXT_PUBLIC_ENABLE_ANIMICA_WEB_WALLET
      ? truthy(env.NEXT_PUBLIC_ENABLE_ANIMICA_WEB_WALLET)
      : true,
    enableMock: env.NODE_ENV !== "production"
  };
}
