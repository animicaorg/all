import { NextRequest, NextResponse } from "next/server";
import { requireUser } from "@/src/server/auth/require";
import { getUserState, saveProjectMemory } from "@/src/server/project/userState";

export async function GET() {
  const user = await requireUser();
  if ("error" in user) return user.error;
  const state = await getUserState(user.user.id);
  return NextResponse.json(state.memory);
}

export async function PUT(req: NextRequest) {
  const user = await requireUser();
  if ("error" in user) return user.error;
  const body = await req.json().catch(() => ({}));
  if (typeof body.text !== "string" || body.text.trim().length < 3) {
    return NextResponse.json({ error: "text is required" }, { status: 400 });
  }
  const memory = await saveProjectMemory(user.user.id, body.text.trim());
  return NextResponse.json(memory);
}
