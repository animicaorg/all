import { describe, expect, it } from "vitest";
import { buildDeployCborTx } from "../src/server/tx/buildTx";

describe("integration-like deploy formatting", () => {
  it("builds cbor tx payload", () => {
    const tx = buildDeployCborTx({
      chainId: 1,
      nonce: 0,
      gasLimit: 1200000,
      fee: 1,
      from: "anim1dev",
      bytecode: "0xdeadbeef",
      args: ""
    });
    expect(tx.startsWith("0x")).toBe(true);
    expect(tx.length).toBeGreaterThan(10);
  });
});
