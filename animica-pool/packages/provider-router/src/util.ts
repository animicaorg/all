import type { InferenceRequest } from "@animica/shared";

/** Rough token estimate (≈4 chars/token) — good enough for cost gating. */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  return Math.max(1, Math.ceil(text.length / 4));
}

export function requestInputText(req: InferenceRequest): string {
  if (req.messages?.length) return req.messages.map((m) => m.content).join("\n");
  if (req.prompt) return req.prompt;
  if (Array.isArray(req.input)) return req.input.join("\n");
  if (typeof req.input === "string") return req.input;
  return "";
}

export async function withTimeout<T>(p: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
  });
  try {
    return await Promise.race([p, timeout]);
  } finally {
    clearTimeout(timer!);
  }
}
