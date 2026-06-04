// POST /app/api/auth/logout
import { NextResponse } from "next/server";
import { endSession } from "@/server/auth";

export const dynamic = "force-dynamic";

export async function POST() {
  await endSession();
  return NextResponse.json({ ok: true });
}
