import type { Config } from './config.js';
import type { Logger } from './logger.js';
import { createServiceContainer, type ServiceContainer } from './services/container.js';
import { InMemoryUsdanStore } from './store/inMemoryStore.js';
import { PrismaUsdanStore } from './store/prismaStore.js';
import type { UsdanStore } from './store/types.js';
import { ModernTreasuryProvider } from './providers/treasury/modernTreasuryProvider.js';
import { MockTreasuryProvider } from './providers/treasury/mockTreasuryProvider.js';
import type { TreasuryProvider } from './providers/treasury/provider.js';

export interface RuntimeContext {
  store: UsdanStore;
  treasury: TreasuryProvider;
  services: ServiceContainer;
}

export function buildRuntime(config: Config, logger: Logger): RuntimeContext {
  const store: UsdanStore = new InMemoryUsdanStore();

  const treasury: TreasuryProvider =
    config.NODE_ENV === 'test'
      ? new MockTreasuryProvider(config.MODERN_TREASURY_WEBHOOK_SECRET)
      : new ModernTreasuryProvider(config, logger);

  const services = createServiceContainer({
    config,
    logger,
    store,
    treasury
  });

  return {
    store,
    treasury,
    services
  };
}

export async function buildRuntimeAsync(config: Config, logger: Logger): Promise<RuntimeContext> {
  if (config.USDAN_DATA_MODE !== 'prisma') {
    return buildRuntime(config, logger);
  }

  const { prisma } = await import('./db/client.js');
  const store: UsdanStore = new PrismaUsdanStore(prisma);
  const treasury: TreasuryProvider =
    config.NODE_ENV === 'test'
      ? new MockTreasuryProvider(config.MODERN_TREASURY_WEBHOOK_SECRET)
      : new ModernTreasuryProvider(config, logger);
  const services = createServiceContainer({ config, logger, store, treasury });
  return { store, treasury, services };
}
