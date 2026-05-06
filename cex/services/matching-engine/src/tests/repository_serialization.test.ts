import { describe, expect, test } from "vitest";
import { EventsRepo } from "../db/repositories/events_repo.js";
import { IdempotencyRepo } from "../db/repositories/idempotency_repo.js";

function createClient() {
  const calls: Array<{ sql: string; params?: unknown[] }> = [];
  return {
    calls,
    client: {
      query: async (sql: string, params?: unknown[]) => {
        calls.push({ sql, params });
        return { rows: [], rowCount: 0 };
      }
    } as any
  };
}

describe("repository JSON serialization", () => {
  test("order events with BigInt payloads are stored as JSON", async () => {
    const { client, calls } = createClient();
    const repo = new EventsRepo(client);

    await repo.appendEvent({
      orderId: "00000000-0000-0000-0000-000000000001",
      marketId: "00000000-0000-0000-0000-000000000002",
      eventType: "ACCEPTED",
      sequence: 7n,
      payload: {
        order: {
          id: "00000000-0000-0000-0000-000000000001",
          priceAtoms: 123n,
          remainingAtoms: 456n
        }
      }
    });

    expect(calls).toHaveLength(1);
    expect(calls[0].params?.[5]).toBe(
      '{"order":{"id":"00000000-0000-0000-0000-000000000001","priceAtoms":"123","remainingAtoms":"456"}}'
    );
  });

  test("idempotency results with BigInt order fields are stored as JSON", async () => {
    const { client, calls } = createClient();
    const repo = new IdempotencyRepo(client);

    await repo.set("idem-key", "matching-engine", {
      success: true,
      order: {
        id: "00000000-0000-0000-0000-000000000001",
        sizeAtoms: 1000n
      },
      fills: [],
      trades: [],
      events: []
    });

    expect(calls).toHaveLength(1);
    expect(calls[0].params?.[2]).toBe(
      '{"success":true,"order":{"id":"00000000-0000-0000-0000-000000000001","sizeAtoms":"1000"},"fills":[],"trades":[],"events":[]}'
    );
  });
});
