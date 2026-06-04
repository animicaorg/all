// GET /app/api/auth/me
import { NextResponse } from "next/server";
import { getSessionUser } from "@/server/auth";

export const dynamic = "force-dynamic";

export async function GET() {
  const user = await getSessionUser();
  return NextResponse.json({ user });
}
