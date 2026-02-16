import { getLlmRuntimeConfig } from "@/src/server/llm/runtimeConfig";

type LlmRequest = {
  prompt: string;
  mode: string;
  context?: Record<string, unknown>;
};

export type LlmResponse = {
  content: string;
  abi: unknown[];
  manifest: Record<string, unknown>;
  requestId: string;
  source: "modal" | "local-fallback";
};

export class LlmClientError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly details?: unknown
  ) {
    super(message);
  }
}

const RETRY_STATUSES = new Set([408, 429, 500, 502, 503, 504]);

async function requestWithTimeout(url: string, init: RequestInit, timeoutMs: number) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function withRetry<T>(fn: () => Promise<T>, retries = 2): Promise<T> {
  let lastError: unknown;
  for (let i = 0; i <= retries; i += 1) {
    try {
      return await fn();
    } catch (error: any) {
      lastError = error;
      const status = Number(error?.status || 0);
      if (i >= retries || !RETRY_STATUSES.has(status)) break;
      await new Promise((resolve) => setTimeout(resolve, 300 * 2 ** i));
    }
  }
  throw lastError;
}

function localFallback(input: LlmRequest, reason: string): LlmResponse {
  return {
    content: `contract Generated {\n  // local fallback (${reason})\n  fn prompt() -> string { return \"${input.prompt.slice(0, 72).replace(/"/g, "'")}\" }\n}`,
    abi: [{ name: "prompt", type: "function" }],
    manifest: { provider: "local-fallback", reason },
    requestId: crypto.randomUUID(),
    source: "local-fallback"
  };
}

async function callModalPath(path: string, input: LlmRequest): Promise<LlmResponse> {
  const cfg = getLlmRuntimeConfig();
  if (!cfg.endpointUrl) {
    return localFallback(input, cfg.fallbackReason ?? "missing_endpoint");
  }

  const target = new URL(path, cfg.endpointUrl).toString();
  try {
    return await withRetry(async () => {
      const res = await requestWithTimeout(target, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(input)
      }, 20_000);

      if (!res.ok) {
        const details = await res.text();
        throw new LlmClientError(`Modal request failed (${res.status})`, res.status, details.slice(0, 800));
      }

      const payload = await res.json();
      return {
        content: String(payload.content ?? ""),
        abi: Array.isArray(payload.abi) ? payload.abi : [],
        manifest: typeof payload.manifest === "object" && payload.manifest ? payload.manifest : {},
        requestId: String(payload.requestId ?? crypto.randomUUID()),
        source: "modal"
      } satisfies LlmResponse;
    });
  } catch (error: any) {
    console.error("[llm-client] Modal endpoint unreachable; falling back locally", {
      message: error?.message,
      status: error?.status,
      endpoint: cfg.endpointUrl
    });
    return localFallback(input, "modal_unreachable");
  }
}

export async function chatCompletion(input: LlmRequest): Promise<LlmResponse> {
  return callModalPath("/v1/chat", input);
}

export async function generateContractCompletion(input: LlmRequest): Promise<LlmResponse> {
  return callModalPath("/v1/contracts/generate", input);
}
