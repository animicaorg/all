import { describe, it, expect, vi, afterEach } from "vitest";
import type { InferenceRequest } from "@animica/shared";
import { BittensorProvider, OpenRouterProvider, TargonProvider } from "./adapters.js";
import { CHUTES_DEFAULT_MODEL_MAP } from "./config.js";

const req = (model: string): InferenceRequest => ({
  requestId: "req_t", model, kind: "chat", messages: [{ role: "user", content: "hi" }],
});

function chatResponse(inTok: number, outTok: number) {
  return {
    ok: true,
    json: async () => ({
      choices: [{ message: { content: "out" } }],
      usage: { prompt_tokens: inTok, completion_tokens: outTok },
    }),
  } as Response;
}

afterEach(() => vi.restoreAllMocks());

describe("BittensorProvider (Chutes SN64)", () => {
  const cfg = { enabled: true, gatewayUrl: "https://llm.chutes.ai/v1", apiKey: "cpk_test", defaultSubnet: "64", timeoutMs: 5000 };

  it("requires an API key to be enabled", async () => {
    expect(await new BittensorProvider({ ...cfg, apiKey: "" }).isEnabled()).toBe(false);
    expect(await new BittensorProvider(cfg).isEnabled()).toBe(true);
  });

  it("maps anm-* to Chutes upstream model ids and prices from the static map", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(chatResponse(1_000_000, 1_000_000));
    const result = await new BittensorProvider(cfg).runChatCompletion(req("anm-pro-70b"));
    const body = JSON.parse(fetchMock.mock.calls.find((c) => String(c[0]).includes("chat/completions"))![1]!.body as string);
    expect(body.model).toBe(CHUTES_DEFAULT_MODEL_MAP["anm-pro-70b"].upstream);
    // 1M in @ $0.28 + 1M out @ $0.42 = $0.70 — and the catalog model is echoed
    // back to the caller, never the upstream id.
    expect(result.providerCostUsd).toBeCloseTo(0.7, 6);
    expect(result.model).toBe("anm-pro-70b");
  });

  it("declines unmapped models so the router falls through to workers", async () => {
    await expect(new BittensorProvider(cfg).runChatCompletion(req("anm-worker-small")))
      .rejects.toThrow(/no upstream mapping/);
  });

  it("honors a modelMap override", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(chatResponse(10, 10));
    const provider = new BittensorProvider({
      ...cfg,
      modelMap: { "anm-fast-8b": { upstream: "custom/model", inUsdPer1M: 1, outUsdPer1M: 2 } },
    });
    await provider.runChatCompletion(req("anm-fast-8b"));
    const body = JSON.parse(fetchMock.mock.calls.find((c) => String(c[0]).includes("chat/completions"))![1]!.body as string);
    expect(body.model).toBe("custom/model");
  });
});

describe("OpenRouterProvider", () => {
  const cfg = {
    enabled: true, apiKey: "sk-or-test", baseUrl: "https://openrouter.ai/api/v1",
    timeoutMs: 5000, pinProvider: "chutes", allowFallbacks: true,
  };

  it("pins the chutes provider in the request body", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(chatResponse(10, 10));
    await new OpenRouterProvider(cfg).runChatCompletion(req("anm-pro-70b"));
    const body = JSON.parse(fetchMock.mock.calls.find((c) => String(c[0]).includes("chat/completions"))![1]!.body as string);
    expect(body.model).toBe("deepseek/deepseek-v3.2");
    expect(body.provider).toEqual({ order: ["chutes"], allow_fallbacks: true });
  });
});

describe("TargonProvider", () => {
  it("stays disabled without key or url, enabled with both", async () => {
    const base = { enabled: true, apiKey: "", baseUrl: "https://api.targon.com/v1", timeoutMs: 5000 };
    expect(await new TargonProvider(base).isEnabled()).toBe(false);
    expect(await new TargonProvider({ ...base, apiKey: "k" }).isEnabled()).toBe(true);
  });
});
