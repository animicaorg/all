import type { Config } from '../config.js';
import type { Logger } from '../logger.js';
import type { UsdanStore } from '../store/types.js';
import type { TreasuryProvider } from '../providers/treasury/provider.js';
import type { ServiceContainer } from '../services/container.js';

export interface HttpContext {
  config: Config;
  logger: Logger;
  store: UsdanStore;
  treasury: TreasuryProvider;
  services: ServiceContainer;
}
