import { describe, expect, it } from "vitest";
import { validateAndRewrite } from "../src/server/chat/validator";

describe("validateAndRewrite", () => {
  it("rewrites when contract keyword missing", () => {
    const result = validateAndRewrite("fn x() -> u64 { return 1; }");
    expect(result.ok).toBe(true);
    expect(result.status).toBe("rewritten");
    expect(result.rewriteCount).toBe(1);
    expect(result.content.startsWith("contract")).toBe(true);
  });

  it("fails for forbidden scaffold words", () => {
    const result = validateAndRewrite("contract A { // to-do later }");
    expect(result.ok).toBe(false);
    expect(result.status).toBe("invalid");
  });
});
