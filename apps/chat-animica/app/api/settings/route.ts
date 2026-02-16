import { NextRequest, NextResponse } from "next/server";
import { requireUser } from "@/src/server/auth/require";
import { getUserState, updateMode } from "@/src/server/project/userState";

export async function GET() {
  const user = await requireUser();
  if ("error" in user) return user.error;
  const state = await getUserState(user.user.id);
  return NextResponse.json(state.settings);
}

export async function POST(req: NextRequest) {
  const user = await requireUser();
  if ("error" in user) return user.error;
  const body = await req.json().catch(() => ({}));
  if (!["strict", "possibility"].includes(body.mode)) return NextResponse.json({ error: "mode must be strict or possibility" }, { status: 400 });
  const next = await updateMode(user.user.id, body.mode);
  return NextResponse.json(next.settings);
}
