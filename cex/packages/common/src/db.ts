import { Pool } from "pg";
import type { BaseEnv } from "./config/env.js";

export const createPgPool = (env: BaseEnv) => {
  return new Pool({
    host: env.DB_HOST,
    port: env.DB_PORT,
    user: env.DB_USER,
    password: env.DB_PASSWORD,
    database: env.DB_NAME
  });
};
