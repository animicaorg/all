// Singleton Prisma client. Reusing the instance across requests is what
// avoids the "too many database connections" symptom on warm reloads.

import { PrismaClient } from '@prisma/client';
import { env, isProd } from './env';

declare global {
  // eslint-disable-next-line no-var
  var __animicaPrisma: PrismaClient | undefined;
}

export const prisma: PrismaClient =
  global.__animicaPrisma ??
  new PrismaClient({
    log: isProd ? ['warn', 'error'] : ['warn', 'error'],
    datasources: { db: { url: env.DATABASE_URL } },
  });

if (!isProd) global.__animicaPrisma = prisma;
