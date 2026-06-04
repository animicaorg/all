import { afterEach, describe, expect, it, vi } from "vitest";
import { makeAnthropicProvider } from "@/server/providers/anthropic";
import type { ModelMap } from "@/server/providers/upstream";

const modelMap: ModelMap = {
  "anm-claude-haiku": {
    model: "claude-haiku-4-5-20251001",
    costInPer1kUsd: "0.0008",
    costOutPer1kUsd: "0.004",
  },
};

const provider = makeAnthropicProvider({ apiKey: "k", modelMap });

afterEach(() => vi.unstubAllGlobals());

describe("anthropic (Claude) adapter", () => {
  it("maps system+messages and parses content blocks", async () => {
    const fetchFn = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            content: [
              { type: "text", text: "Hello" },
              { type: "text", text: " world" },
            ],
            usage: { input_tokens: 7, output_tokens: 3 },
          }),
          { status: 200 },
        ),
    );
    vi.stubGlobal("fetch", fetchFn);

    const r = await provider.chat({
      model: "anm-claude-haiku",
      messages: [
        { role: "system", content: "be terse" },
        { role: "user", content: "hi" },
      ],
      maxTokens: 256,
    });

    expect(r.output).toBe("Hello world");
    expect(r.inputTokens).toBe(7);
    expect(r.outputTokens).toBe(3);
    // 7/1000*0.0008 + 3/1000*0.004 = 0.0000056 + 0.000012 = 0.0000176
    expect(r.providerCostUsd).toBe("0.0000176");

    const [url, init] = fetchFn.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/v1/messages");
    expect((init.headers as Record<string, string>)["x-api-key"]).toBe("k");
    const body = JSON.parse(init.body as string);
    expect(body.model).toBe("claude-haiku-4-5-20251001");
    expect(body.system).toBe("be terse"); // system extracted out of messages
    expect(body.messages).toEqual([{ role: "user", content: "hi" }]);
    expect(body.max_tokens).toBe(256);
  });

  it("does not support embeddings", async () => {
    await expect(provider.embed({ model: "anm-claude-haiku", input: ["x"] })).rejects.toThrow(
      /not supported/i,
    );
  });
});
