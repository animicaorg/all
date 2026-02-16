import { NextRequest, NextResponse } from "next/server";
import { chatCompletion } from "@/src/services/llmClient";
import { checkProxyRateLimit } from "@/src/server/llm/proxyRateLimit";

export async function POST(req: NextRequest) {
  const ip = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "local";
  const rl = checkProxyRateLimit(`chat:${ip}`);
  if (!rl.allowed) {
    return NextResponse.json({ error: "Rate limited", retryAfterMs: rl.retryAfterMs }, { status: 429 });
  }

  const body = await req.json().catch(() => null);
  if (!body || typeof body.prompt !== "string") {
    return NextResponse.json({ error: "Invalid payload: prompt is required" }, { status: 400 });
  }

  const result = await chatCompletion({
    prompt: body.prompt,
    mode: typeof body.mode === "string" ? body.mode : "strict",
    context: typeof body.context === "object" && body.context ? body.context : undefined
  });

  return NextResponse.json(result);
}
