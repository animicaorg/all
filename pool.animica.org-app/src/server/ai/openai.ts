// OpenAI-compatible response shapes + error envelope. Keeping these in one
// place means the route handlers stay thin and every error looks like the
// `{ error: { message, type, code } }` shape SDKs expect.

import { NextResponse } from "next/server";
import type { CatalogModel } from "./catalog";

export type OpenAiErrorType =
  | "invalid_request_error"
  | "authentication_error"
  | "insufficient_quota"
  | "rate_limit_error"
  | "server_error";

export function errorResponse(
  status: number,
  message: string,
  type: OpenAiErrorType,
  code?: string,
): NextResponse {
  return NextResponse.json(
    { error: { message, type, code: code ?? null, param: null } },
    { status },
  );
}

export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export function chatCompletionEnvelope(opts: {
  id: string;
  model: string;
  content: string;
  promptTokens: number;
  completionTokens: number;
}) {
  return {
    id: `chatcmpl-${opts.id}`,
    object: "chat.completion" as const,
    created: Math.floor(Date.now() / 1000),
    model: opts.model,
    choices: [
      {
        index: 0,
        message: { role: "assistant" as const, content: opts.content },
        logprobs: null,
        finish_reason: "stop" as const,
      },
    ],
    usage: {
      prompt_tokens: opts.promptTokens,
      completion_tokens: opts.completionTokens,
      total_tokens: opts.promptTokens + opts.completionTokens,
    },
  };
}

/** Little-endian float32 → base64, matching OpenAI's base64 embedding format. */
function floatsToBase64(vec: number[]): string {
  const buf = Buffer.alloc(vec.length * 4);
  for (let i = 0; i < vec.length; i++) buf.writeFloatLE(vec[i], i * 4);
  return buf.toString("base64");
}

export function embeddingEnvelope(opts: {
  model: string;
  embeddings: number[][];
  promptTokens: number;
  // The OpenAI SDK defaults to "base64" and decodes client-side; honour it.
  encodingFormat?: "float" | "base64";
}) {
  const asBase64 = opts.encodingFormat === "base64";
  return {
    object: "list" as const,
    data: opts.embeddings.map((embedding, index) => ({
      object: "embedding" as const,
      index,
      embedding: asBase64 ? floatsToBase64(embedding) : embedding,
    })),
    model: opts.model,
    usage: {
      prompt_tokens: opts.promptTokens,
      total_tokens: opts.promptTokens,
    },
  };
}

export function modelObject(model: CatalogModel) {
  return {
    id: model.id,
    object: "model" as const,
    created: 0,
    owned_by: "animica",
    // non-standard but useful extras
    kind: model.kind,
    pricing: {
      input_per_1k_usd: model.priceInPer1kUsd,
      output_per_1k_usd: model.priceOutPer1kUsd,
    },
  };
}
