import pino from 'pino';
import type { Config } from './config.js';

export function createLogger(config: Config) {
  const create = pino as unknown as (options: Record<string, unknown>) => any;
  return create({
    level: config.USDAN_API_LOG_LEVEL,
    transport:
      config.NODE_ENV === 'development'
        ? {
            target: 'pino-pretty',
            options: { colorize: true, translateTime: 'SYS:standard' }
          }
        : undefined
  });
}

export type Logger = ReturnType<typeof createLogger>;
