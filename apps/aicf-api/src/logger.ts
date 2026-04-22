import type { AppConfig } from './config.js';

export type AppLogger = {
  info: (meta: unknown, message?: string) => void;
  warn: (meta: unknown, message?: string) => void;
  error: (meta: unknown, message?: string) => void;
  debug: (meta: unknown, message?: string) => void;
};

function log(level: string, meta: unknown, message?: string) {
  const line = message ? `${level} ${message}` : level;
  if (meta && typeof meta === 'object') {
    console.log(line, JSON.stringify(meta));
    return;
  }
  console.log(line, meta ?? '');
}

export function createLogger(config: AppConfig): AppLogger {
  const base = {
    service: 'aicf-api',
    chainId: config.AICF_CHAIN_ID
  };
  return {
    info(meta, message) {
      log('INFO', { ...base, meta }, message);
    },
    warn(meta, message) {
      log('WARN', { ...base, meta }, message);
    },
    error(meta, message) {
      log('ERROR', { ...base, meta }, message);
    },
    debug(meta, message) {
      if (process.env.LOG_LEVEL === 'debug') {
        log('DEBUG', { ...base, meta }, message);
      }
    }
  };
}
