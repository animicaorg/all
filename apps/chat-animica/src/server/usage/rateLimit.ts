import { redis } from "@/src/server/db/redis";

const DAILY_LIMIT = 200;

export async function consumeDailyMessage(userId: string) {
  const day = new Date().toISOString().slice(0, 10);
  const key = `usage:msg:${userId}:${day}`;
  const total = await redis.incr(key);
  if (total === 1) await redis.expire(key, 60 * 60 * 24 * 2);
  return { total, limit: DAILY_LIMIT, allowed: total <= DAILY_LIMIT };
}
