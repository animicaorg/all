import { NextRequest, NextResponse } from "next/server";
import { requireUser } from "@/src/server/auth/require";
import { prisma } from "@/src/server/db/prisma";

export async function GET(req: NextRequest) {
  const userResult = await requireUser();
  if ("error" in userResult) return userResult.error;

  const requestId = req.nextUrl.searchParams.get("requestId");

  if (requestId === "latest") {
    const session = await prisma.walletSession.findFirst({
      where: { userId: userResult.user.id, status: "active" },
      orderBy: { createdAt: "desc" }
    });
    return NextResponse.json({ ok: true, status: session ? "approved" : "none", session });
  }

  if (!requestId) return NextResponse.json({ error: "requestId required" }, { status: 400 });

  const request = await prisma.walletConnectRequest.findFirst({
    where: { id: requestId, userId: userResult.user.id }
  });
  if (!request) return NextResponse.json({ error: "request not found" }, { status: 404 });

  const session = request.status === "approved"
    ? await prisma.walletSession.findFirst({ where: { userId: userResult.user.id }, orderBy: { createdAt: "desc" } })
    : null;

  return NextResponse.json({ ok: true, status: request.status, session });
}
