// GET /v1/models — OpenAI-compatible model list. Public (matches OpenAI).

import { listModels } from "@/server/ai/catalog";
import { modelObject } from "@/server/ai/openai";
import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json({
    object: "list",
    data: listModels().map(modelObject),
  });
}
