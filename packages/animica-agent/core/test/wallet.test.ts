import { describe, expect, it } from "vitest";

import { formatANM, parseANM } from "../src/wallet.js";
import { isLikelyAnimicaAddress } from "../src/rpc.js";

describe("wallet helpers", () => {
  it("formatANM matches parseANM round-trip", () => {
    expect(formatANM(parseANM("1.234567"))).toBe("1.234567");
    expect(formatANM(parseANM("0"))).toBe("0");
  });
  it("formatANM truncates beyond the requested precision", () => {
    expect(formatANM(parseANM("0.123456789"), 4)).toBe("0.1234");
  });
  it("parseANM rejects bogus input", () => {
    expect(() => parseANM("abc")).toThrow();
  });
  it("isLikelyAnimicaAddress recognizes lowercase anm prefix", () => {
    expect(isLikelyAnimicaAddress("anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l")).toBe(true);
    expect(isLikelyAnimicaAddress("ANM1QPZRY9X8GF2TVDW0S3JN54KHCE6MUA7L")).toBe(false);
    expect(isLikelyAnimicaAddress("0xabcdef")).toBe(false);
  });
});
