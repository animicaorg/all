import { NextResponse } from "next/server";
import { requireUser } from "@/src/server/auth/require";
import { getUserState, setKnowledgePackState } from "@/src/server/project/userState";

export async function GET() {
  const user = await requireUser();
  if ("error" in user) return user.error;
  const state = await getUserState(user.user.id);
  return NextResponse.json(state.knowledgePack);
}

export async function POST() {
  const user = await requireUser();
  if ("error" in user) return user.error;

  await setKnowledgePackState(user.user.id, { status: "building" });
  await new Promise((resolve) => setTimeout(resolve, 250));
  const ready = await setKnowledgePackState(user.user.id, { status: "ready", lastBuiltAt: new Date().toISOString() });
  return NextResponse.json(ready);
}
