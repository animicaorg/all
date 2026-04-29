import type { Pool, PoolClient } from "pg";

export interface DbClient {
  query: Pool["query"];
  connect: Pool["connect"];
}

export const createDbClient = (pool: Pool): DbClient => {
  return {
    query: pool.query.bind(pool),
    connect: pool.connect.bind(pool)
  };
};
