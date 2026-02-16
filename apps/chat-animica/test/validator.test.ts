import { describe, expect, it } from "vitest";
import { validateAndRewrite } from "../src/server/chat/validator";
import { SUPPORTED_MAINNET_RPC_METHODS } from "../src/lib/animicaGuardrails";
import { goldenConversations } from "./fixtures/chat-golden";

describe("validateAndRewrite", () => {
  it("rewrites when contract keyword missing", () => {
    const result = validateAndRewrite("fn x() -> u64 { return 1; }");
    expect(result.ok).toBe(true);
    expect(result.status).toBe("rewritten");
    expect(result.content.startsWith("contract")).toBe(true);
  });

  it("replaces banned scaffold language", () => {
    const result = validateAndRewrite("contract A { // assume this exists }");
    expect(result.ok).toBe(true);
    expect(result.content.toLowerCase()).not.toContain("assume this exists");
  });

  it.each(goldenConversations)("golden: $name", ({ modelOutput, expectMethods }) => {
    const result = validateAndRewrite(modelOutput);
    expect(result.ok).toBe(true);
    expect(result.content).toMatch(/minimal example/i);
    expect(result.content).toMatch(/deployment recipe/i);

    for (const method of expectMethods) {
      expect(result.content).toContain(method);
    }

    const methods = result.content.match(/\b(?:tx|state|chain)\.[A-Za-z][A-Za-z0-9]*\b/g) ?? [];
    for (const method of methods) {
      expect(SUPPORTED_MAINNET_RPC_METHODS).toContain(method as any);
    }
  });
});
