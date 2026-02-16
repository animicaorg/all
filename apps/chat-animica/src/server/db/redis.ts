import Redis from "ioredis";
import { env } from "@/src/server/env";

const globalForRedis = globalThis as unknown as { redis?: Redis };

export const redis = globalForRedis.redis ?? new Redis(env.REDIS_URL, { maxRetriesPerRequest: null });
if (process.env.NODE_ENV !== "production") globalForRedis.redis = redis;
