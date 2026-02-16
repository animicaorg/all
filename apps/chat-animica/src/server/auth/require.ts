import { NextResponse } from "next/server";
import { getSessionUser } from "@/src/server/auth/session";
import { prisma } from "@/src/server/db/prisma";

export async function requireUser() {
  const user = await getSessionUser();
  if (!user) {
    return { error: NextResponse.json({ error: "Unauthorized" }, { status: 401 }) };
  }
  return { user };
}

export async function requireActiveSubscription(userId: string) {
  const sub = await prisma.subscription.findFirst({ where: { userId, status: "ACTIVE" } });
  if (!sub) {
    return { error: NextResponse.json({ error: "Active subscription required" }, { status: 402 }) };
  }
  return { subscription: sub };
}
