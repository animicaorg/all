import { describe, expect, it } from "vitest";

import { AgentError } from "../src/errors.js";
import { ExtensionSigner, NodeWalletSigner } from "../src/signers.js";

describe("ExtensionSigner", () => {
  it("refuses when no provider is injected", async () => {
    const s = new ExtensionSigner();
    await expect(
      s.sign({ payload: { kind: "agent-receipt", data: { x: 1 } }, reason: "x", estimatedCostRaw: 0n }),
    ).rejects.toThrow(/wallet extension provider not detected/i);
  });

  it("personal_sign returns the signature when provider responds", async () => {
    const provider = {
      request: async (args: { method: string; params?: unknown[] }) => {
        expect(args.method).toBe("personal_sign");
        return "0xdeadbeef";
      },
    };
    const s = new ExtensionSigner({ provider });
    const r = await s.sign({ payload: { kind: "agent-receipt", data: { id: "r1" } }, reason: "test", estimatedCostRaw: 100n });
    expect(r.signature).toBe("0xdeadbeef");
  });

  it("anm-transfer routes through animica_sendTransaction with hex value", async () => {
    let captured: { method: string; params?: unknown[] } | null = null;
    const provider = {
      request: async (args: { method: string; params?: unknown[] }) => {
        captured = args;
        if (args.method === "animica_sendTransaction") return { txHash: "0xabc" };
        return null;
      },
    };
    const s = new ExtensionSigner({ provider });
    const r = await s.sign({
      payload: { kind: "anm-transfer", data: { from: "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l", to: "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l", valueRaw: 255n } },
      reason: "test",
      estimatedCostRaw: 255n,
    });
    expect(r.txHash).toBe("0xabc");
    expect(captured?.method).toBe("animica_sendTransaction");
    const p = (captured!.params as Array<{ value: string }>)[0];
    expect(p.value).toBe("0xff");
  });

  it("rejects unknown payload kinds rather than guessing", async () => {
    const provider = { request: async () => "ok" };
    const s = new ExtensionSigner({ provider });
    await expect(
      s.sign({ payload: { kind: "totally-unknown-kind", data: {} }, reason: "test", estimatedCostRaw: 0n }),
    ).rejects.toBeInstanceOf(AgentError);
  });
});

describe("NodeWalletSigner", () => {
  it("refuses non-transfer payloads (no fabricated signature)", async () => {
    const s = new NodeWalletSigner();
    await expect(
      s.sign({ payload: { kind: "agent-receipt", data: { x: 1 } }, reason: "test", estimatedCostRaw: 0n }),
    ).rejects.toThrow(/only supports anm-transfer/);
  });
});
