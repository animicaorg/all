import { afterEach, describe, expect, it, jest } from "@jest/globals";
import { ConfirmationBackfill } from "../jobs/confirmation_backfill.js";

const originalFetch = global.fetch;

describe("ConfirmationBackfill", () => {
  afterEach(() => {
    global.fetch = originalFetch;
    jest.restoreAllMocks();
  });

  it("discovers confirmed Litecoin transfers from active BitGo deposit wallets", async () => {
    const queries: Array<{ query: string; values?: any[] }> = [];
    const now = new Date("2026-05-06T18:00:00Z");

    const client = {
      query: async (query: string, values?: any[]) => {
        queries.push({ query, values });

        if (/BEGIN|COMMIT|ROLLBACK/.test(query)) {
          return { rows: [], rowCount: 0 };
        }
        if (/SELECT\s+an\.id,\s+a\.decimals/i.test(query)) {
          return { rows: [{ id: "an-ltc", decimals: 8 }], rowCount: 1 };
        }
        if (/SELECT\s+an\.id/i.test(query) && /FROM\s+asset_networks/i.test(query)) {
          return { rows: [{ id: "an-ltc" }], rowCount: 1 };
        }
        if (/FROM\s+user_deposit_addresses/i.test(query) && /SELECT\s+user_id/i.test(query)) {
          return { rows: [{ user_id: "11111111-1111-1111-1111-111111111111" }], rowCount: 1 };
        }
        if (/COALESCE\(an\.confirmations_override/i.test(query)) {
          return { rows: [{ confirmations: 6 }], rowCount: 1 };
        }
        if (/INSERT\s+INTO\s+deposits/i.test(query)) {
          return {
            rows: [{
              id: "dep-ltc",
              user_id: "11111111-1111-1111-1111-111111111111",
              asset_network_id: "an-ltc",
              provider: "BITGO",
              provider_event_id: values?.[3],
              wallet_id: "wallet-ltc",
              transfer_id: "transfer-ltc",
              txid: "ltc-txid",
              vout: "0",
              address: "MWqoKwYzgwRwndUB4gNFwG3fmXU31YcgYJ",
              tag: "",
              amount_atoms: "250000000",
              confirmations: 12,
              confirmations_required: 6,
              block_height: 123,
              block_hash: "block-ltc",
              status: "CONFIRMED",
              detected_at: now,
              confirmed_at: now,
              credited_at: null,
              unassigned: false,
              risk_hold: false,
              risk_reason: null,
              raw: {},
              metadata: {},
              created_at: now,
              updated_at: now,
            }],
            rowCount: 1,
          };
        }
        if (/SELECT\s+COUNT\(\*\)/i.test(query)) {
          return { rows: [{ count: "1" }], rowCount: 1 };
        }
        if (/address\s+!=/i.test(query)) {
          return { rows: [], rowCount: 0 };
        }
        if (/SELECT\s+a\.symbol/i.test(query)) {
          return { rows: [{ symbol: "LTC" }], rowCount: 1 };
        }
        if (/INSERT\s+INTO\s+deposit_outbox/i.test(query)) {
          return {
            rows: [{
              id: "outbox-ltc",
              deposit_id: "dep-ltc",
              idempotency_key: "deposit:dep-ltc",
              payload: values?.[2],
              created_at: now,
              processed_at: null,
              retry_count: 0,
              last_retry_at: null,
              last_error: null,
            }],
            rowCount: 1,
          };
        }
        if (/INSERT\s+INTO\s+audit_logs/i.test(query)) {
          return { rows: [], rowCount: 1 };
        }
        if (/UPDATE\s+user_deposit_addresses/i.test(query)) {
          return { rows: [], rowCount: 1 };
        }

        return { rows: [], rowCount: 0 };
      },
      release: jest.fn(),
    };

    const pool = {
      query: async (query: string) => {
        queries.push({ query });
        if (/SELECT\s+DISTINCT/i.test(query) && /FROM\s+wallets/i.test(query)) {
          return {
            rows: [{
              asset_network_id: "an-ltc",
              coin: "ltc",
              wallet_id: "wallet-ltc",
            }],
            rowCount: 1,
          };
        }
        if (/FROM\s+deposits/i.test(query) && /status\s+=\s+'DETECTED'/i.test(query)) {
          return { rows: [], rowCount: 0 };
        }
        return { rows: [], rowCount: 0 };
      },
      connect: async () => client,
    };

    (global as any).fetch = jest.fn(async () => ({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({
        transfers: [{
          id: "transfer-ltc",
          coin: "ltc",
          wallet: "wallet-ltc",
          txid: "ltc-txid",
          height: 123,
          heightId: "block-ltc",
          date: "2026-05-06T17:30:00Z",
          confirmations: 12,
          valueString: "250000000",
          state: "confirmed",
          entries: [{
            address: "MWqoKwYzgwRwndUB4gNFwG3fmXU31YcgYJ",
            valueString: "250000000",
          }],
        }],
      }),
      text: async () => "",
    } as Response));

    const logger = {
      info: jest.fn(),
      warn: jest.fn(),
      error: jest.fn(),
      debug: jest.fn(),
      child: () => logger,
    } as any;

    const job = new ConfirmationBackfill(
      pool as any,
      {
        BITGO_API_TOKEN: "token",
        BITGO_ENV: "test",
        CONFIRMATION_BACKFILL_INTERVAL_MS: 60000,
        BITGO_TRANSFER_DISCOVERY_LIMIT: 100,
      } as any,
      logger
    );

    await (job as any).run();

    const fetchMock = global.fetch as jest.Mock;
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/v2/ltc/wallet/wallet-ltc/transfer");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      headers: { Authorization: "Bearer token" },
    });
    expect(queries.some(({ query }) => /INSERT\s+INTO\s+deposits/i.test(query))).toBe(true);
    expect(queries.some(({ query }) => /INSERT\s+INTO\s+deposit_outbox/i.test(query))).toBe(true);
  });
});
