import { describe, it, expect } from "vitest";
import type { ProviderAdapter, ProviderInferenceResult, InferenceRequest } from "@animica/shared";
import { routeChatCompletion, orderedEnabled } from "./router.js";

function fakeAdapter(name: string, opts: { enabled?: boolean; fail?: boolean } = {}): ProviderAdapter {
  return {
    name,
    isEnabled: async () => opts.enabled ?? true,
    healthCheck: async () => ({ provider: name, healthy: true }),
    estimateCost: async () => ({ provider: name, estimatedCostUsd: 0, estimatedInputTokens: 1, estimatedOutputTokens: 1 }),
    runChatCompletion: async (req: InferenceRequest): Promise<ProviderInferenceResult> => {
      if (opts.fail) throw new Error(`${name} down`);
      return { provider: name, model: req.model, requestId: req.requestId, status: "success", inputTokens: 1, outputTokens: 1, providerCostUsd: 0, latencyMs: 1, output: `from ${name}` };
    },
  };
}

const req: InferenceRequest = { requestId: "req_1", model: "anm-fast-8b", kind: "chat", messages: [{ role: "user", content: "hi" }] };

describe("provider router", () => {
  it("orders enabled adapters by priority (bittensor before mock)", async () => {
    const ordered = await orderedEnabled([fakeAdapter("mock"), fakeAdapter("bittensor")]);
    expect(ordered.map((a) => a.name)).toEqual(["bittensor", "mock"]);
  });

  it("filters out disabled adapters", async () => {
    const ordered = await orderedEnabled([fakeAdapter("runpod", { enabled: false }), fakeAdapter("mock")]);
    expect(ordered.map((a) => a.name)).toEqual(["mock"]);
  });

  it("falls back to the next provider on failure", async () => {
    const { result, attempts } = await routeChatCompletion(req, [
      fakeAdapter("bittensor", { fail: true }),
      fakeAdapter("mock"),
    ]);
    expect(result.provider).toBe("mock");
    expect(attempts[0]).toMatchObject({ provider: "bittensor", ok: false });
  });

  it("throws when no providers are enabled", async () => {
    await expect(routeChatCompletion(req, [fakeAdapter("mock", { enabled: false })])).rejects.toThrow(/no inference providers/i);
  });
});
