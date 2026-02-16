import { NextRequest, NextResponse } from "next/server";
import { rpcCompatCall } from "@/src/server/rpc/animicaRpc";

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({}));
  if (typeof body.rawTx !== "string") return NextResponse.json({ error: "rawTx required" }, { status: 400 });
  try {
    const result = await rpcCompatCall("debug.explainReject", [body.rawTx]);
    return NextResponse.json({ ok: true, result });
  } catch (error: any) {
    return NextResponse.json({ error: error?.message ?? "Method unavailable" }, { status: 501 });
  }
}
