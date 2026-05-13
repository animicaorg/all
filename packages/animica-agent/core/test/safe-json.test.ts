import { describe, expect, it } from "vitest";

import { redact, safeParse, safeStringify, toBigInt } from "../src/safe-json.js";

describe("safe-json", () => {
  it("round-trips bigint via wrapper", () => {
    const v = { a: 1, b: 2n ** 200n, c: [10n, 20n] };
    const text = safeStringify(v);
    const back = safeParse<typeof v>(text);
    expect(back.b).toBe(2n ** 200n);
    expect(back.c[0]).toBe(10n);
  });

  it("emits hex bigint when hex option is on", () => {
    const text = safeStringify({ n: 255n }, { hex: true });
    expect(text).toBe('{"n":"0xff"}');
  });

  it("toBigInt accepts decimal, hex, and bigint inputs", () => {
    expect(toBigInt("10")).toBe(10n);
    expect(toBigInt("0xff")).toBe(255n);
    expect(toBigInt(42)).toBe(42n);
    expect(() => toBigInt("not-a-number")).toThrow();
  });

  it("redacts well-known secret keys regardless of casing", () => {
    const r = redact({ Password: "x", api_key: "y", nested: { Secret: "z" }, ok: 1 }) as Record<string, unknown>;
    expect(r.Password).toBe("[REDACTED]");
    expect(r.api_key).toBe("[REDACTED]");
    expect((r.nested as Record<string, unknown>).Secret).toBe("[REDACTED]");
    expect(r.ok).toBe(1);
  });

  it("does not coerce decimal strings into bigint", () => {
    // The wrapper-only round trip prevents block numbers from being silently mutated.
    expect(safeParse('{"x":"100"}')).toEqual({ x: "100" });
  });

  it("survives deep nesting with bigint payloads", () => {
    const v = { tx: { value: 10n ** 30n, gas: { used: 21000n } } };
    expect(safeParse(safeStringify(v))).toEqual(v);
  });
});
