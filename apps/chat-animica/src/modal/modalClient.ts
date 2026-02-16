import { env } from "@/src/server/env";

export type ModalChatResponse = {
  content: string;
  abi: unknown[];
  manifest: Record<string, unknown>;
  requestId: string;
};

export class ModalClientError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly debug?: unknown
  ) {
    super(message);
  }
}

async function requestWithTimeout(url: string, init: RequestInit, timeoutMs: number) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function withRetry<T>(fn: () => Promise<T>, retries = 3) {
  let lastError: unknown;
  for (let i = 0; i <= retries; i += 1) {
    try {
      return await fn();
    } catch (error: any) {
      lastError = error;
      const status = error?.status ?? 0;
      if (i === retries || ![429, 500, 502, 503, 504].includes(status)) throw error;
      await new Promise((resolve) => setTimeout(resolve, 300 * 2 ** i));
    }
  }
  throw lastError;
}

export async function callModalChat(input: { prompt: string; context?: unknown; mode: string }): Promise<ModalChatResponse> {
  if (!env.MODAL_CHAT_URL) {
    throw new ModalClientError("LLM endpoint is not configured", 500, { missing: "MODAL_CHAT_URL" });
  }

  return withRetry(async () => {
    const res = await requestWithTimeout(env.MODAL_CHAT_URL, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(input)
    }, 18_000);

    if (!res.ok) {
      let debug: unknown = null;
      try { debug = await res.json(); } catch {}
      throw new ModalClientError(debug && typeof debug === "object" && "error" in (debug as any) ? String((debug as any).error) : "Modal request failed", res.status, debug);
    }

    const json = await res.json();
    return {
      content: String(json.content ?? ""),
      abi: Array.isArray(json.abi) ? json.abi : [],
      manifest: typeof json.manifest === "object" && json.manifest ? json.manifest : {},
      requestId: String(json.requestId ?? crypto.randomUUID())
    };
  });
}
