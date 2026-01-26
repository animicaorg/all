import { Redis } from "ioredis";
import type { BaseEnv } from "./config/env.js";

export const createRedis = (env: BaseEnv) => {
  return new Redis(env.REDIS_URL, {
    maxRetriesPerRequest: 3
  });
};
