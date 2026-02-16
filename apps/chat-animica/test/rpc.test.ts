import { describe, expect, it } from "vitest";
import { methodResolver, paramEncoder } from "../src/server/rpc/animicaRpc";

describe("methodResolver", () => {
  it("picks canonical discovered method", () => {
    const selected = methodResolver(["foo", "tx.submitRawTransaction"]);
    expect(selected).toBe("tx.submitRawTransaction");
  });

  it("falls back to first candidate", () => {
    const selected = methodResolver(["foo"]);
    expect(selected).toBe("tx_sendRawTransaction");
  });
});

describe("paramEncoder", () => {
  it("uses positional first when rawTx scalar param", () => {
    const encoded = paramEncoder([{ name: "rawTx", schema: { type: "string" } }], "0xabc");
    expect(encoded.primary).toEqual(["0xabc"]);
    expect(encoded.alternate).toEqual([{ rawTx: "0xabc" }]);
  });

  it("uses object first when object param", () => {
    const encoded = paramEncoder([{ name: "rawTx", schema: { type: "object" } }], "0xabc");
    expect(encoded.primary).toEqual([{ rawTx: "0xabc" }]);
    expect(encoded.alternate).toEqual(["0xabc"]);
  });
});
