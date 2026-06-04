// POST /v1/chat/completions — OpenAI-compatible chat endpoint.
// (Served under basePath at /app/v1/...; a next.config rewrite + nginx expose
//  it at the domain root /v1/... — see next.config.mjs.)

import { z } from "zod";
import { authenticateApiKey } from "@/server/apiAuth";
import { errorResponse } from "@/server/ai/openai";
import { mapInferenceError, serveChat } from "@/server/ai/serve";
import { takeToken } from "@/lib/rateLimit";
import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const Body = z.object({
  model: z.string().min(1),
  messages: z
    .array(
      z.object({
        role: z.enum(["system", "user", "assistant", "tool"]),
        content: z.string(),
      }),
    )
    .min(1),
  max_tokens: z.number().int().positive().optional(),
  temperature: z.number().min(0).max(2).optional(),
  stream: z.boolean().optional(),
});

export async function POST(req: Request) {
  const authed = await authenticateApiKey(req);
  if (!authed) {
    return errorResponse(401, "Invalid or missing API key.", "authentication_error");
  }

  const rl = takeToken(`chat:${authed.apiKey.id}`, { limit: 60, windowMs: 60_000 });
  if (!rl.ok) {
    return errorResponse(429, "Rate limit exceeded.", "rate_limit_error");
  }

  let json: unknown;
  try {
    json = await req.json();
  } catch {
    return errorResponse(400, "Request body must be valid JSON.", "invalid_request_error");
  }

  const parsed = Body.safeParse(json);
  if (!parsed.success) {
    return errorResponse(400, parsed.error.issues[0]?.message ?? "Invalid request.", "invalid_request_error");
  }
  if (parsed.data.stream) {
    return errorResponse(
      400,
      "Streaming (stream:true) is not yet supported. Use a non-streaming request.",
      "invalid_request_error",
      "streaming_unsupported",
    );
  }

  try {
    const envelope = await serveChat(authed, {
      model: parsed.data.model,
      messages: parsed.data.messages,
      maxTokens: parsed.data.max_tokens,
      temperature: parsed.data.temperature,
    });
    return NextResponse.json(envelope);
  } catch (err) {
    return mapInferenceError(err);
  }
}
