import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  balanceFailureToPayoutReason,
  RpcBalanceProvider,
  type BalanceLookup,
  type BalanceProvider,
} from "../src/balance-provider.js";
import type { Receipt } from "../src/billing.js";
import {
  BalanceAwarePayoutGuard,
  PayoutAuditor,
} from "../src/payout-policy.js";

const ADDR = "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l";
const RECIPIENT = "anm1zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz";

function makeReceipt(overrides: Partial<Receipt> = {}): Receipt {
  return {
    id: "rec-1",
    at: new Date(Date.now() - 5 * 60_000).toISOString(),
    kind: "scaffold",
    estimate: { raw: 1_000_000_000_000_000n, formattedANM: "0.001", tier: "base", breakdown: [] },
    status: "estimated",
    wallet: RECIPIENT,
    worker: "w1",
    receiptHash: "h".repeat(64),
    idempotencyKey: "useful-work:jobX:artifact",
    ...overrides,
  } as Receipt;
}

/**
 * Lightweight fake fetch that simulates an Animica RPC. Responds to
 * probeNode (chainId/blockNumber/clientVersion) and to a balance lookup.
 */
function makeFakeFetch(
  config: { chainIdHex: string; balanceHex: string | "fail" | "malformed-empty" | "malformed-string" },
): { fetchImpl: typeof fetch; calls: { method: string; params: unknown[] }[] } {
  const calls: { method: string; params: unknown[] }[] = [];
  const fetchImpl = (async (_url: string | URL | Request, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body ?? "{}")) as {
      method?: string;
      params?: unknown[];
      id?: number | string;
    };
    const method = String(body.method);
    calls.push({ method, params: body.params ?? [] });
    let result: unknown;
    switch (method) {
      case "animica_chainId":
      case "eth_chainId":
        result = config.chainIdHex;
        break;
      case "animica_blockNumber":
      case "eth_blockNumber":
        result = "0x10";
        break;
      case "animica_clientVersion":
      case "web3_clientVersion":
        result = "test-node/v0.1.0";
        break;
      case "animica_syncing":
      case "eth_syncing":
        result = false;
        break;
      case "animica_getBalance":
      case "eth_getBalance":
        if (config.balanceHex === "fail") {
          return new Response(JSON.stringify({ jsonrpc: "2.0", id: body.id ?? 1, error: { code: -32000, message: "rpc bug" } }), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        }
        if (config.balanceHex === "malformed-empty") {
          result = "";
          break;
        }
        if (config.balanceHex === "malformed-string") {
          result = "not-a-hex-or-decimal";
          break;
        }
        result = config.balanceHex;
        break;
      default:
        return new Response(JSON.stringify({ jsonrpc: "2.0", id: body.id ?? 1, error: { code: -32601, message: "method not found" } }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
    }
    return new Response(JSON.stringify({ jsonrpc: "2.0", id: body.id ?? 1, result }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as unknown as typeof fetch;
  return { fetchImpl, calls };
}

describe("RpcBalanceProvider", () => {
  it("fetches and parses a real balance", async () => {
    const { fetchImpl, calls } = makeFakeFetch({ chainIdHex: "0x1", balanceHex: "0xde0b6b3a7640000" }); // 1 ANM
    const p = new RpcBalanceProvider({
      rpcUrl: "http://fake",
      expectedChainId: "1",
      fetchImpl,
    });
    const r = await p.lookup(ADDR);
    expect(r.ok).toBe(true);
    if (!r.ok) throw new Error();
    expect(r.balance.raw).toBe(1_000_000_000_000_000_000n);
    expect(r.balance.formattedANM).toBe("1");
    expect(r.observedChainId).toBe("1");
    expect(calls.some((c) => c.method.endsWith("getBalance"))).toBe(true);
  });

  it("caches a successful lookup within TTL", async () => {
    const { fetchImpl, calls } = makeFakeFetch({ chainIdHex: "0x1", balanceHex: "0x1" });
    let t = 1000;
    const p = new RpcBalanceProvider({
      rpcUrl: "http://fake",
      expectedChainId: "1",
      fetchImpl,
      cacheTtlMs: 5_000,
      now: () => t,
    });
    const a = await p.lookup(ADDR);
    expect(a.ok).toBe(true);
    const callsAfterFirst = calls.length;
    t += 1_000; // within TTL
    const b = await p.lookup(ADDR);
    expect(b.ok).toBe(true);
    if (b.ok) expect(b.cached).toBe(true);
    expect(calls.length).toBe(callsAfterFirst); // no extra calls
  });

  it("re-fetches after TTL expiry", async () => {
    const { fetchImpl, calls } = makeFakeFetch({ chainIdHex: "0x1", balanceHex: "0x1" });
    let t = 1000;
    const p = new RpcBalanceProvider({
      rpcUrl: "http://fake",
      expectedChainId: "1",
      fetchImpl,
      cacheTtlMs: 1_000,
      now: () => t,
    });
    await p.lookup(ADDR);
    const callsAfterFirst = calls.length;
    t += 5_000;
    await p.lookup(ADDR);
    expect(calls.length).toBeGreaterThan(callsAfterFirst);
  });

  it("refuses when address is missing or invalid", async () => {
    const { fetchImpl } = makeFakeFetch({ chainIdHex: "0x1", balanceHex: "0x1" });
    const p = new RpcBalanceProvider({ rpcUrl: "http://fake", expectedChainId: "1", fetchImpl });
    const a = await p.lookup("");
    expect(a.ok).toBe(false);
    if (!a.ok) expect(a.failureReason).toBe("signer-address-missing");
    const b = await p.lookup("not-a-valid-address");
    expect(b.ok).toBe(false);
    if (!b.ok) expect(b.failureReason).toBe("signer-address-invalid");
  });

  it("refuses when expected chain id does not match the RPC", async () => {
    const { fetchImpl } = makeFakeFetch({ chainIdHex: "0x99", balanceHex: "0x1" });
    const p = new RpcBalanceProvider({ rpcUrl: "http://fake", expectedChainId: "1", fetchImpl });
    const r = await p.lookup(ADDR);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.failureReason).toBe("chain-id-mismatch");
  });

  it("refuses when RPC is unreachable", async () => {
    const fetchImpl: typeof fetch = (async () => {
      throw new Error("ECONNREFUSED");
    }) as unknown as typeof fetch;
    const p = new RpcBalanceProvider({ rpcUrl: "http://fake", expectedChainId: "1", fetchImpl });
    const r = await p.lookup(ADDR);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.failureReason).toBe("rpc-unavailable");
  });

  it("refuses when balance reply is empty", async () => {
    const { fetchImpl } = makeFakeFetch({ chainIdHex: "0x1", balanceHex: "malformed-empty" });
    const p = new RpcBalanceProvider({ rpcUrl: "http://fake", expectedChainId: "1", fetchImpl });
    const r = await p.lookup(ADDR);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.failureReason).toBe("balance-malformed");
  });

  it("refuses when balance reply is not a hex/decimal string", async () => {
    const { fetchImpl } = makeFakeFetch({ chainIdHex: "0x1", balanceHex: "malformed-string" });
    const p = new RpcBalanceProvider({ rpcUrl: "http://fake", expectedChainId: "1", fetchImpl });
    const r = await p.lookup(ADDR);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.failureReason).toBe("balance-malformed");
  });

  it("refuses when RPC URL is empty", async () => {
    const p = new RpcBalanceProvider({ rpcUrl: "", expectedChainId: "1" });
    const r = await p.lookup(ADDR);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.failureReason).toBe("rpc-url-missing");
  });
});

describe("balanceFailureToPayoutReason", () => {
  it("maps every failure variant to a stable payout-rejection reason", () => {
    expect(balanceFailureToPayoutReason("signer-address-missing")).toBe("config-missing");
    expect(balanceFailureToPayoutReason("rpc-url-missing")).toBe("config-missing");
    expect(balanceFailureToPayoutReason("signer-address-invalid")).toBe("tampered-attempt");
    expect(balanceFailureToPayoutReason("chain-id-mismatch")).toBe("tampered-attempt");
    expect(balanceFailureToPayoutReason("balance-malformed")).toBe("tampered-attempt");
    expect(balanceFailureToPayoutReason("rpc-unavailable")).toBe("reserve-balance-violation");
    expect(balanceFailureToPayoutReason("unknown")).toBe("unknown");
  });
});

describe("BalanceAwarePayoutGuard", () => {
  function constProvider(lookup: BalanceLookup): BalanceProvider {
    return {
      async lookup() {
        return lookup;
      },
      invalidate() {},
    };
  }

  it("refuses when balance lookup fails (rpc unavailable)", async () => {
    const dir = mkdtempSync(join(tmpdir(), "ba-"));
    const auditor = new PayoutAuditor(dir);
    const guard = new BalanceAwarePayoutGuard({
      signerAddress: ADDR,
      balanceProvider: constProvider({
        ok: false,
        failureReason: "rpc-unavailable",
        message: "no node",
        fetchedAt: new Date().toISOString(),
      }),
      cfg: { mandatoryArtifactHash: false },
      auditor,
    });
    const r = await guard.decide({
      receipt: makeReceipt(),
      recipient: RECIPIENT,
      amountRaw: 1n,
      artifactHash: "a",
      chainId: "1",
    });
    expect(r.allowed).toBe(false);
    expect(r.evaluation.reason).toBe("reserve-balance-violation");
    expect(r.record.allowed).toBe(false);
    rmSync(dir, { recursive: true });
  });

  it("refuses when a successful lookup leaves the signer under reserve", async () => {
    const dir = mkdtempSync(join(tmpdir(), "ba-"));
    const auditor = new PayoutAuditor(dir);
    const guard = new BalanceAwarePayoutGuard({
      signerAddress: ADDR,
      balanceProvider: constProvider({
        ok: true,
        balance: { address: ADDR, raw: 100n, decimal: "100", formattedANM: "0", reachable: true },
        observedChainId: "1",
        fetchedAt: new Date().toISOString(),
        cached: false,
      }),
      cfg: { mandatoryArtifactHash: false, reserveBalanceRaw: 80n },
      auditor,
    });
    const r = await guard.decide({
      receipt: makeReceipt(),
      recipient: RECIPIENT,
      amountRaw: 50n,
      artifactHash: "a",
      chainId: "1",
    });
    expect(r.allowed).toBe(false);
    expect(r.evaluation.reason).toBe("reserve-balance-violation");
    rmSync(dir, { recursive: true });
  });

  it("allows when balance is comfortably above reserve and invalidates cache after", async () => {
    let invalidated = 0;
    const provider: BalanceProvider = {
      async lookup() {
        return {
          ok: true as const,
          balance: { address: ADDR, raw: 1_000n, decimal: "1000", formattedANM: "0", reachable: true },
          observedChainId: "1",
          fetchedAt: new Date().toISOString(),
          cached: false,
        };
      },
      invalidate() {
        invalidated++;
      },
    };
    const dir = mkdtempSync(join(tmpdir(), "ba-"));
    const auditor = new PayoutAuditor(dir);
    const guard = new BalanceAwarePayoutGuard({
      signerAddress: ADDR,
      balanceProvider: provider,
      cfg: { mandatoryArtifactHash: false, reserveBalanceRaw: 100n },
      auditor,
    });
    const r = await guard.decide({
      receipt: makeReceipt(),
      recipient: RECIPIENT,
      amountRaw: 50n,
      artifactHash: "a",
      chainId: "1",
    });
    expect(r.allowed).toBe(true);
    expect(invalidated).toBe(1);
    rmSync(dir, { recursive: true });
  });
});
