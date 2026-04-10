/**
 * Redis Client Utility
 */

import { createClient, type RedisClientType } from 'redis';
import type { Config } from '../config.js';
import type { Logger } from './logger.js';

export async function createRedisClient(
  config: Config,
  logger: Logger
): Promise<RedisClientType | null> {
  try {
    const url =
      config.REDIS_URL ||
      `redis://${config.REDIS_HOST}:${config.REDIS_PORT}/${config.REDIS_DB}`;

    const client = createClient({
      url,
      password: config.REDIS_PASSWORD,
    });

    client.on('error', (err) => logger.error({ err }, 'Redis client error'));
    client.on('connect', () => logger.info('Redis client connected'));
    client.on('ready', () => logger.info('Redis client ready'));
    client.on('reconnecting', () => logger.warn('Redis client reconnecting'));

    await client.connect();
    return client;
  } catch (error) {
    logger.warn(
      { error },
      'Failed to connect to Redis, falling back to in-memory storage'
    );
    return null;
  }
}
