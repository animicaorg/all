/**
 * Database Client
 * Prisma client singleton
 */

import { PrismaClient } from '@prisma/client';
import type { Logger } from '../utils/logger.js';

let prisma: PrismaClient | null = null;

export function createPrismaClient(logger: Logger): PrismaClient {
  if (prisma) {
    return prisma;
  }

  prisma = new PrismaClient({
    log: [
      { level: 'query', emit: 'event' },
      { level: 'error', emit: 'event' },
      { level: 'warn', emit: 'event' },
    ],
  });

  // Log queries in development
  if (process.env.NODE_ENV === 'development') {
    prisma.$on('query' as never, (e: any) => {
      logger.debug({ query: e.query, params: e.params, duration: e.duration }, 'Database query');
    });
  }

  prisma.$on('error' as never, (e: any) => {
    logger.error({ error: e }, 'Database error');
  });

  prisma.$on('warn' as never, (e: any) => {
    logger.warn({ warning: e }, 'Database warning');
  });

  return prisma;
}

export function getPrismaClient(): PrismaClient {
  if (!prisma) {
    throw new Error('Prisma client not initialized. Call createPrismaClient first.');
  }
  return prisma;
}

export async function disconnectPrisma(): Promise<void> {
  if (prisma) {
    await prisma.$disconnect();
    prisma = null;
  }
}
