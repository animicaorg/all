import 'dotenv/config';
import { loadConfig } from './config.js';
import { createApp } from './http/app.js';
import './http/context.js';
import { createLogger } from './logger.js';
import { PlatformService } from './services/platformService.js';
import { createInMemoryStore } from './store/inMemoryStore.js';

async function main() {
  const config = loadConfig();
  const logger = createLogger(config);
  const store = createInMemoryStore();
  const service = new PlatformService(store, config, logger);
  const app = createApp(service, config, logger);

  const server = app.listen(config.AICF_API_PORT, config.AICF_API_HOST, () => {
    logger.info(
      {
        host: config.AICF_API_HOST,
        port: config.AICF_API_PORT,
        chainId: config.AICF_CHAIN_ID
      },
      'AICF API started'
    );
  });

  const shutdown = () => {
    server.close(() => {
      logger.info('AICF API stopped');
      process.exit(0);
    });
  };

  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}

export { main };
