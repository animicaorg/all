import { afterEach, describe, expect, it, vi } from "vitest";
import { submitToAnimicaNode } from "../pipeline/submit_animica_node.js";
import { createMockLogger } from "./helpers.js";

const withdrawal = {
  id: "wd-animica-1",
  user_id: "0bc966f7-69ff-4ca6-a1f7-14257ebaaac2",
  asset_network_id: "ffffffff-0006-0006-0006-000000000006",
  destination_address: "anim1destination",
  destination_tag: null,
  amount: "11000000000",
  fee_amount: "1000000000",
  total_debit_amount: "12000000000",
  status: "APPROVED",
  idempotency_key: "idem-animica-1",
  client_withdrawal_id: null,
  provider: "ANIMICA_NODE",
  provider_ref: null,
  txid: null,
  risk_score: null,
  risk_flags: "[]",
  risk_reason: null,
  requested_at: new Date(),
  approved_at: new Date(),
  broadcast_at: null,
  confirmed_at: null,
  failure_code: null,
  failure_message: null,
  attempt_count: 0,
  next_retry_at: null,
  created_at: new Date(),
  updated_at: new Date(),
};

function createAnimicaClient() {
  const state = {
    withdrawal: { ...withdrawal },
    auditLogs: [] as any[],
  };

  return {
    state,
    client: {
      query: vi.fn(async (query: string, values?: any[]) => {
        if (query.includes("SELECT * FROM withdrawals WHERE id =")) {
          return { rows: [state.withdrawal], rowCount: 1 };
        }

        if (query.includes("FROM asset_networks")) {
          return {
            rows: [
              {
                id: withdrawal.asset_network_id,
                asset_symbol: "ANM",
                asset_decimals: 9,
                network_name: "ANIMICA",
                address_type: "ACCOUNT",
                provider: "ANIMICA_NODE",
                confirmations_required: 1,
                enabled: true,
                metadata: { rpc_url: "http://127.0.0.1:8545/rpc" },
              },
            ],
            rowCount: 1,
          };
        }

        if (query.includes("FROM user_deposit_addresses")) {
          return {
            rows: [
              {
                address: "anim1userdeposit",
                label: "ANM-user",
                wallet_id: "animica-node:deposit",
              },
            ],
            rowCount: 1,
          };
        }

        if (query.includes("FROM wallets")) {
          return {
            rows: [
              {
                id: "wallet-hot",
                asset_network_id: withdrawal.asset_network_id,
                wallet_type: "HOT",
                provider: "ANIMICA_NODE",
                provider_wallet_id: "animica-node:hot",
                enabled: true,
                metadata: {
                  address: "anim1nonexistenthot",
                  rpc_url: "http://127.0.0.1:8545/rpc",
                },
              },
            ],
            rowCount: 1,
          };
        }

        if (query.includes("UPDATE withdrawals")) {
          state.withdrawal.status = values?.[1];
          if (query.includes("provider_ref")) state.withdrawal.provider_ref = values?.[2];
          if (query.includes("txid")) state.withdrawal.txid = values?.[3];
          return { rows: [state.withdrawal], rowCount: 1 };
        }

        if (query.includes("INSERT INTO withdrawal_audit_log")) {
          state.auditLogs.push({ values });
          return { rows: [{ id: "audit-1" }], rowCount: 1 };
        }

        return { rows: [], rowCount: 0 };
      }),
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("submitToAnimicaNode", () => {
  it("submits from the user's active Animica deposit address", async () => {
    const { client, state } = createAnimicaClient();
    const rpcCalls: any[] = [];

    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: any) => {
        const body = JSON.parse(init.body);
        rpcCalls.push(body);

        if (body.method === "state.getBalance") {
          return Response.json({ jsonrpc: "2.0", id: body.id, result: { confirmed_balance: "26000000000" } });
        }
        if (body.method === "wallet.send") {
          return Response.json({ jsonrpc: "2.0", id: body.id, result: { txid: "0xabc123" } });
        }
        return Response.json({ jsonrpc: "2.0", id: body.id, error: { code: -32601, message: "not found" } });
      })
    );

    const result = await submitToAnimicaNode(client as any, withdrawal.id, createMockLogger());

    expect(result.success).toBe(true);
    expect(state.withdrawal.status).toBe("BROADCAST");
    expect(state.withdrawal.txid).toBe("0xabc123");
    expect(rpcCalls.find((call) => call.method === "state.getBalance").params).toEqual(["anim1userdeposit"]);
    expect(rpcCalls.find((call) => call.method === "wallet.send").params[0]).toMatchObject({
      from: "anim1userdeposit",
      to: "anim1destination",
      amountAtoms: "11000000000",
      feeAtoms: "1000000000",
    });
  });

  it("leaves the withdrawal approved when node submission fails so the outbox can retry", async () => {
    const { client, state } = createAnimicaClient();

    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: any) => {
        const body = JSON.parse(init.body);
        if (body.method === "state.getBalance") {
          return Response.json({ jsonrpc: "2.0", id: body.id, result: { confirmed_balance: "26000000000" } });
        }
        return Response.json({
          jsonrpc: "2.0",
          id: body.id,
          error: { code: -32010, message: "mempool admission failed" },
        });
      })
    );

    const result = await submitToAnimicaNode(client as any, withdrawal.id, createMockLogger());

    expect(result.success).toBe(false);
    expect(state.withdrawal.status).toBe("APPROVED");
    expect(state.withdrawal.txid).toBeNull();
  });
});
