import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetProviderRegistry, getProvider } from "@/server/providers/registry";
import { routeChat } from "@/server/providers/router";

describe("router fallback with a real provider configured", () => {
  beforeEach(() => {
    process.env.ANTHROPIC_API_KEY = "test-key";
    resetProviderRegistry();
  });
  afterEach(() => {
    delete process.env.ANTHROPIC_API_KEY;
    resetProviderRegistry();
    vi.unstubAllGlobals();
  });

  it("registers anthropic when its key is present", () => {
    expect(getProvider("anthropic")).toBeDefined();
  });

  it("falls back to mock when the real provider errors", async () => {
    // Anthropic upstream 500s → router should fall through to mock.
    vi.stubGlobal("fetch", vi.fn(async () => new Response("upstream down", { status: 500 })));
    const { provider, result } = await routeChat({
      model: "anm-claude-haiku",
      messages: [{ role: "user", content: "hello" }],
    });
    expect(provider).toBe("mock");
    expect(result.output).toContain("hello");
  });

  it("has no anthropic provider once the key is removed", () => {
    delete process.env.ANTHROPIC_API_KEY;
    resetProviderRegistry();
    expect(getProvider("anthropic")).toBeUndefined();
  });
});
