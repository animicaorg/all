/**
 * Logger Utility
 * Pino logger with structured logging
 */

import { pino, type Logger as PinoLogger } from 'pino';
import type { Config } from '../config.js';

export type Logger = PinoLogger;

export function createLogger(config: Config): Logger {
  const isDevelopment = config.NODE_ENV === 'development';
  
  return pino({
    level: config.LOG_LEVEL,
    transport: isDevelopment
      ? {
          target: 'pino-pretty',
          options: {
            colorize: true,
            translateTime: 'HH:MM:ss Z',
            ignore: 'pid,hostname',
          },
        }
      : undefined,
    formatters: {
      level: (label: string) => {
        return { level: label };
      },
    },
    base: {
      service: config.SERVICE_NAME,
    },
  });
}
