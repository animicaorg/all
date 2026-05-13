import { describe, expect, it } from "vitest";

import { RpcClient, probeNode } from "../src/rpc.js";

function mockFetch(responses: Record<string, unknown>): typeof fetch {
  return (async (_url: RequestInfo | URL, init?: RequestInit) => {
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : {};
    const method = body.method as string;
    if (responses[method] !== undefined) {
      return new Response(JSON.stringify({ jsonrpc: "2.0", id: body.id, result: responses[method] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    return new Response(JSON.stringify({ jsonrpc: "2.0", id: body.id, error: { code: -32601, message: "method not found" } }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as unknown as typeof fetch;
}

describe("rpc", () => {
  it("encodes bigint params as hex", async () => {
    let captured: string | undefined;
    const fetchImpl: typeof fetch = (async (_url, init) => {
      captured = init!.body as string;
      return new Response(JSON.stringify({ jsonrpc: "2.0", id: 1, result: "0x1" }));
    }) as unknown as typeof fetch;
    const c = new RpcClient({ url: "http://x", fetchImpl });
    await c.call({ method: "x_demo", params: [255n] });
    expect(captured).toContain('"0xff"');
  });

  it("probeNode collects chainId and blockNumber", async () => {
    const fetchImpl = mockFetch({
      animica_chainId: "0x2a",
      animica_blockNumber: "0x10",
      animica_clientVersion: "test/0.0.0",
    });
    // We rebind globalThis.fetch only for this call.
    const original = globalThis.fetch;
    globalThis.fetch = fetchImpl;
    try {
      const r = await probeNode("http://x", 500);
      expect(r.reachable).toBe(true);
      expect(r.chainId).toBe(42n);
      expect(r.blockNumber).toBe(16n);
    } finally {
      globalThis.fetch = original;
    }
  });
});
