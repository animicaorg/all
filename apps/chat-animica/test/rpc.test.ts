import { describe, expect, it } from "vitest";
import { methodResolver, paramEncoder } from "../src/server/rpc/animicaRpc";

describe("methodResolver", () => {
  it("picks discovered method in priority order", () => {
    const selected = methodResolver(["foo", "tx.submitRawTransaction"]);
    expect(selected).toBe("tx.submitRawTransaction");
  });

  it("falls back to candidate when none discovered", () => {
    const selected = methodResolver(["foo"]);
    expect(selected).toBe("tx.sendRawTransaction");
  });
});

describe("paramEncoder", () => {
  it("uses positional first for scalar schema", () => {
    const encoded = paramEncoder([{ name: "rawTx", schema: { type: "string" } }], "0xabc");
    expect(encoded.primary).toEqual(["0xabc"]);
    expect(encoded.alternate).toEqual([{ rawTx: "0xabc" }]);
  });

  it("uses object first for object schema", () => {
    const encoded = paramEncoder([{ name: "payload", schema: { type: "object" } }], "0xabc");
    expect(encoded.primary).toEqual([{ payload: "0xabc" }]);
    expect(encoded.alternate).toEqual(["0xabc"]);
  });
});
