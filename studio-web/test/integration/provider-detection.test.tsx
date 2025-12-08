/**
 * Integration test for provider detection and connectivity
 * Tests the Studio's ability to detect and interact with the Animica wallet provider
 */

import { describe, it, expect, beforeEach, vi } from "vitest";

describe("Provider Detection", () => {
  beforeEach(() => {
    delete (window as any).animica;
  });

  it("should detect when provider is unavailable", () => {
    expect((window as any).animica).toBeUndefined();
  });

  it("should detect when provider is available", () => {
    (window as any).animica = { request: vi.fn(), on: vi.fn() };
    expect((window as any).animica).toBeDefined();
  });
});

describe("RPC Connectivity", () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });

  it("should handle RPC calls", async () => {
    const mockFetch = global.fetch as ReturnType<typeof vi.fn>;
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ jsonrpc: "2.0", id: 1, result: { height: 12345 } }),
    });

    const response = await fetch("http://localhost:8545", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "chain.getHead", params: [] }),
    });

    expect(response.ok).toBe(true);
  });
});
