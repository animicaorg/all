import { describe, expect, it } from "vitest";
import { createServer } from "../http/server.js";
import { createMockLogger } from "./helpers.js";

function createRedisRateLimitMock() {
  const counts = new Map<string, number>();
  const expiresAt = new Map<string, number>();

  return {
    keys: counts,
    ping: async () => "PONG",
    incr: async (key: string) => {
      const current = (counts.get(key) || 0) + 1;
      counts.set(key, current);
      return current;
    },
    pexpire: async (key: string, milliseconds: number) => {
      expiresAt.set(key, Date.now() + milliseconds);
      return 1;
    },
    pttl: async (key: string) => Math.max(0, (expiresAt.get(key) || Date.now()) - Date.now()),
  };
}

describe("withdrawal rate-limit routing", () => {
  it("mounts the withdrawal request limiter only on POST /withdrawals", () => {
    const redis = createRedisRateLimitMock();
    const pool = {
      query: async () => ({ rows: [{ "?column?": 1 }], rowCount: 1 }),
      connect: async () => ({
        query: async () => ({ rows: [], rowCount: 0 }),
        release: () => undefined,
      }),
    };
    const app = createServer(
      pool as any,
      redis as any,
      {
        SERVICE_NAME: "withdrawals-service",
        WITHDRAWAL_REQUEST_RATE_LIMIT: 5,
        ADMIN_API_KEY: "admin-key",
      } as any,
      { getConfig: async () => ({ webhookSecret: undefined }) } as any,
      createMockLogger()
    );

    const withdrawalRouterLayer = (app as any)._router.stack.find((layer: any) =>
      layer.name === "router" &&
      layer.handle?.stack?.some((child: any) => child.route?.path === "/withdrawals")
    );

    expect(withdrawalRouterLayer).toBeDefined();
    const withdrawalStack = withdrawalRouterLayer.handle.stack;

    const middlewareLayers = withdrawalStack.filter((layer: any) => !layer.route);
    expect(middlewareLayers).toHaveLength(1); // authentication only

    const getWithdrawalRoutes = withdrawalStack.filter(
      (layer: any) => layer.route?.path === "/withdrawals" && layer.route.methods.get
    );
    expect(getWithdrawalRoutes).toHaveLength(1);
    expect(getWithdrawalRoutes[0].route.stack).toHaveLength(1);

    const postWithdrawalRoutes = withdrawalStack.filter(
      (layer: any) => layer.route?.path === "/withdrawals" && layer.route.methods.post
    );
    expect(postWithdrawalRoutes).toHaveLength(2);
    expect(postWithdrawalRoutes[0].route.stack).toHaveLength(2); // rate limit + idempotency
    expect(postWithdrawalRoutes[1].route.stack).toHaveLength(1); // route handler
  });
});
