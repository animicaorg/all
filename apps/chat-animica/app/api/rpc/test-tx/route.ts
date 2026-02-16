import { NextResponse } from "next/server";
import { defensiveSendRawTransaction } from "@/src/server/rpc/animicaRpc";

export async function POST() {
  const fake = "0xdeadbeef";
  const result = await defensiveSendRawTransaction(fake);
  return NextResponse.json(result, { status: result.ok ? 200 : 502 });
}
