/**
 * Logger Utility
 */

import pino from 'pino';
import type { Config } from '../config.js';

export function createLogger(config: Config) {
  return pino({
    name: config.SERVICE_NAME,
    level: config.LOG_LEVEL,
    transport:
      config.NODE_ENV === 'development'
        ? {
            target: 'pino-pretty',
            options: {
              colorize: true,
              translateTime: 'SYS:standard',
              ignore: 'pid,hostname',
            },
          }
        : undefined,
  });
}

export type Logger = ReturnType<typeof createLogger>;
