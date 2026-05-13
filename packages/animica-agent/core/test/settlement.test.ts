import { describe, expect, it } from "vitest";

import { DEFAULT_CONFIG } from "../src/config.js";
import { checkSettlementReadiness, waitForConfirmation } from "../src/settlement.js";

describe("checkSettlementReadiness", () => {
  it("flags an unreachable RPC and stops the chain of dependent checks", async () => {
    const cfg = { ...DEFAULT_CONFIG, rpcUrl: "http://127.0.0.1:1/rpc" }; // port 1 is reserved, refused immediately
    const r = await checkSettlementReadiness(cfg, { estimatedCostRaw: 1n, txBinary: false });
    expect(r.ok).toBe(false);
    expect(r.firstFailure?.reason).toBe("rpc-unreachable");
  });

  it("succeeds when the tx-binary probe is disabled and no estimate is given", async () => {
    const cfg = { ...DEFAULT_CONFIG, rpcUrl: "http://127.0.0.1:1/rpc" };
    const r = await checkSettlementReadiness(cfg, { txBinary: false });
    // rpc still fails; this asserts the report shape, not success.
    expect(Array.isArray(r.checks)).toBe(true);
    expect(r.firstFailure?.reason).toBe("rpc-unreachable");
  });

  it("runs the tx-binary probe through the spawn shim", async () => {
    let captured: string[] = [];
    // Use a deliberately-refused RPC URL so probeNode fails fast (~ms),
    // not the default 127.0.0.1:8545 which may actually be reachable on
    // dev machines and would make this test hang.
    const cfg = { ...DEFAULT_CONFIG, rpcUrl: "http://127.0.0.1:1/rpc" };
    const r = await checkSettlementReadiness(cfg, {
      txBinary: "fake-bin",
      spawn: ((cmd: string, args: string[]) => {
        captured = [cmd, ...args];
        return { status: 0, stdout: "Usage: tx send [OPTIONS]\n", stderr: "" } as unknown as ReturnType<typeof import("node:child_process").spawnSync>;
      }) as unknown as typeof import("node:child_process").spawnSync,
    });
    const binCheck = r.checks.find((c) => c.reason === "tx-binary-missing");
    expect(binCheck?.ok).toBe(true);
    expect(captured[0]).toBe("fake-bin");
  });
});

describe("waitForConfirmation", () => {
  it("returns confirmed as soon as a receipt with blockNumber is seen", async () => {
    const r = await waitForConfirmation("0xabc", {
      rpcUrl: "http://x",
      maxAttempts: 3,
      intervalMs: 1,
      sleep: async () => {},
      call: async <T>(method: string): Promise<T | null> => {
        if (method === "animica_getTransactionReceipt") return { blockNumber: "0x10", status: "0x1" } as unknown as T;
        return null;
      },
    });
    expect(r.status).toBe("confirmed");
    expect(r.attempts).toBe(1);
    expect(r.receipt?.blockNumber).toBe(16n);
  });

  it("returns rejected on a status=0 receipt", async () => {
    const r = await waitForConfirmation("0xabc", {
      rpcUrl: "http://x",
      maxAttempts: 3,
      intervalMs: 1,
      sleep: async () => {},
      call: async <T>(method: string): Promise<T | null> => {
        if (method === "animica_getTransactionReceipt") return { blockNumber: "0x5", status: "0x0" } as unknown as T;
        return null;
      },
    });
    expect(r.status).toBe("rejected");
  });

  it("returns missing after exhausting attempts", async () => {
    const r = await waitForConfirmation("0xabc", {
      rpcUrl: "http://x",
      maxAttempts: 2,
      intervalMs: 1,
      sleep: async () => {},
      call: async () => null,
    });
    expect(r.status).toBe("missing");
    expect(r.attempts).toBe(2);
  });

  it("returns rpc-error when the call throws a non-HTTP error", async () => {
    const r = await waitForConfirmation("0xabc", {
      rpcUrl: "http://x",
      maxAttempts: 2,
      intervalMs: 1,
      sleep: async () => {},
      call: async () => {
        throw new Error("EAI_AGAIN: dns failure");
      },
    });
    expect(r.status).toBe("rpc-error");
  });
});
