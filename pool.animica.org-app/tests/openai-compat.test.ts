import { afterEach, describe, expect, it, vi } from "vitest";
import { makeOpenAiCompatProvider } from "@/server/providers/openaiCompat";
import type { ModelMap } from "@/server/providers/upstream";

const modelMap: ModelMap = {
  "anm-fast-8b": { model: "gpt-4o-mini", costInPer1kUsd: "0.001", costOutPer1kUsd: "0.002" },
  "anm-embed": { model: "text-embedding-3-small", costInPer1kUsd: "0.0004", costOutPer1kUsd: "0" },
};

const provider = makeOpenAiCompatProvider({
  name: "openai-compatible",
  baseUrl: "https://upstream.test/v1",
  apiKey: "k",
  modelMap,
});

afterEach(() => vi.unstubAllGlobals());

function stubFetch(body: unknown) {
  const fn = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("openai-compatible adapter", () => {
  it("supports only mapped models", () => {
    expect(provider.supports("anm-fast-8b")).toBe(true);
    expect(provider.supports("nope")).toBe(false);
  });

  it("parses chat + computes provider cost from usage", async () => {
    const fetchFn = stubFetch({
      choices: [{ message: { content: "hi there" } }],
      usage: { prompt_tokens: 10, completion_tokens: 20 },
    });
    const r = await provider.chat({
      model: "anm-fast-8b",
      messages: [{ role: "user", content: "hi" }],
    });
    expect(r.output).toBe("hi there");
    expect(r.inputTokens).toBe(10);
    expect(r.outputTokens).toBe(20);
    // 10/1000*0.001 + 20/1000*0.002 = 0.00001 + 0.00004 = 0.00005
    expect(r.providerCostUsd).toBe("0.00005");
    // sends the UPSTREAM model id, not the catalog id
    const body = JSON.parse((fetchFn.mock.calls[0][1] as RequestInit).body as string);
    expect(body.model).toBe("gpt-4o-mini");
  });

  it("parses embeddings (float arrays, ordered)", async () => {
    stubFetch({
      data: [
        { index: 1, embedding: [0.3, 0.4] },
        { index: 0, embedding: [0.1, 0.2] },
      ],
      usage: { prompt_tokens: 5 },
    });
    const r = await provider.embed({ model: "anm-embed", input: ["a", "b"] });
    expect(r.embeddings).toEqual([
      [0.1, 0.2],
      [0.3, 0.4],
    ]); // reordered by index
    expect(r.providerCostUsd).toBe("0.000002"); // 5/1000*0.0004
  });

  it("throws on non-2xx so the router can fall back", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("boom", { status: 500 })));
    await expect(
      provider.chat({ model: "anm-fast-8b", messages: [{ role: "user", content: "x" }] }),
    ).rejects.toThrow(/500/);
  });
});
