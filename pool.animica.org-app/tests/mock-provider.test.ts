import { describe, expect, it } from "vitest";
import { estimateTokens, mockProvider } from "@/server/providers/mock";

describe("mock provider", () => {
  it("estimates tokens ~chars/4", () => {
    expect(estimateTokens("")).toBe(0);
    expect(estimateTokens("a")).toBe(1);
    expect(estimateTokens("abcdefgh")).toBe(2);
  });

  it("is deterministic for the same chat input", async () => {
    const req = {
      model: "anm-fast-8b",
      messages: [{ role: "user" as const, content: "hello world" }],
    };
    const a = await mockProvider.chat(req);
    const b = await mockProvider.chat(req);
    expect(a).toEqual(b);
    expect(a.output).toContain("hello world");
    expect(a.inputTokens).toBeGreaterThan(0);
    expect(a.outputTokens).toBeGreaterThan(0);
    expect(Number(a.providerCostUsd)).toBeGreaterThan(0);
  });

  it("produces fixed-dimension deterministic embeddings", async () => {
    const a = await mockProvider.embed({ model: "anm-embed", input: ["x", "y"] });
    const b = await mockProvider.embed({ model: "anm-embed", input: ["x", "y"] });
    expect(a.embeddings.length).toBe(2);
    expect(a.embeddings[0].length).toBe(16);
    expect(a).toEqual(b);
  });
});
