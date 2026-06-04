// POST /v1/embeddings — OpenAI-compatible embeddings endpoint.

import { z } from "zod";
import { authenticateApiKey } from "@/server/apiAuth";
import { errorResponse } from "@/server/ai/openai";
import { mapInferenceError, serveEmbed } from "@/server/ai/serve";
import { takeToken } from "@/lib/rateLimit";
import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const Body = z.object({
  model: z.string().min(1),
  // OpenAI accepts a string or an array of strings.
  input: z.union([z.string(), z.array(z.string()).min(1)]),
  encoding_format: z.enum(["float", "base64"]).optional(),
});

export async function POST(req: Request) {
  const authed = await authenticateApiKey(req);
  if (!authed) {
    return errorResponse(401, "Invalid or missing API key.", "authentication_error");
  }

  const rl = takeToken(`embed:${authed.apiKey.id}`, { limit: 120, windowMs: 60_000 });
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

  const input = Array.isArray(parsed.data.input) ? parsed.data.input : [parsed.data.input];

  try {
    const envelope = await serveEmbed(authed, {
      model: parsed.data.model,
      input,
      encodingFormat: parsed.data.encoding_format,
    });
    return NextResponse.json(envelope);
  } catch (err) {
    return mapInferenceError(err);
  }
}
