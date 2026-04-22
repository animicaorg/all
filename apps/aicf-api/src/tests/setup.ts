import { createLogger } from '../logger.js';
import { PlatformService } from '../services/platformService.js';
import { createInMemoryStore } from '../store/inMemoryStore.js';
import { loadConfig } from '../config.js';
import '../http/context.js';

export function buildTestApp() {
  const config = loadConfig({
    ...process.env,
    AICF_API_JWT_SECRET: 'test-jwt-secret',
    AICF_API_INTERNAL_SECRET: 'test-internal-secret',
    AICF_ADMIN_BOOTSTRAP_EMAIL: 'admin@test.animica.org',
    AICF_ADMIN_BOOTSTRAP_PASSWORD: 'admin-password-test',
    AICF_TREASURY_BOOTSTRAP_ANM: '1000000'
  });

  const logger = createLogger(config);
  const store = createInMemoryStore();
  const service = new PlatformService(store, config, logger);
  return { service, config };
}
