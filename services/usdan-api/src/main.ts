import 'dotenv/config';
import { loadConfig } from './config.js';
import { createLogger } from './logger.js';
import { buildRuntimeAsync } from './runtime.js';
import { createApp } from './http/app.js';
import { ReconciliationJob } from './jobs/reconciliationJob.js';

async function main() {
  const config = loadConfig();
  const logger = createLogger(config);

  const runtime = await buildRuntimeAsync(config, logger);
  const app = createApp({
    config,
    logger,
    store: runtime.store,
    treasury: runtime.treasury,
    services: runtime.services
  });

  const server = app.listen(config.USDAN_API_PORT, config.USDAN_API_HOST, () => {
    logger.info(
      {
        host: config.USDAN_API_HOST,
        port: config.USDAN_API_PORT,
        mode: config.USDAN_DATA_MODE
      },
      'USDAN API started'
    );
  });

  const reconciliationJob = new ReconciliationJob(runtime.services.reserve, logger);
  reconciliationJob.start();

  const shutdown = async () => {
    reconciliationJob.stop();
    server.close(() => {
      logger.info('USDAN API stopped');
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
