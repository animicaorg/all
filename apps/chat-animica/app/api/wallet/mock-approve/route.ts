import { NextRequest, NextResponse } from "next/server";
import { requireUser } from "@/src/server/auth/require";
import { prisma } from "@/src/server/db/prisma";
import { env } from "@/src/server/env";
import { signNonce } from "@/src/server/wallet/connect";

export async function POST(req: NextRequest) {
  if (env.WALLET_MOCK !== "1") return NextResponse.json({ error: "WALLET_MOCK disabled" }, { status: 403 });

  const userResult = await requireUser();
  if ("error" in userResult) return userResult.error;

  const { requestId } = await req.json().catch(() => ({}));
  if (!requestId) return NextResponse.json({ error: "requestId required" }, { status: 400 });

  const request = await prisma.walletConnectRequest.findFirst({ where: { id: requestId, userId: userResult.user.id } });
  if (!request) return NextResponse.json({ error: "request not found" }, { status: 404 });

  const callbackUrl = new URL("/api/wallet/callback", req.nextUrl.origin);
  const callbackRes = await fetch(callbackUrl, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      requestId,
      approved: true,
      accounts: ["anim1mockdev00001"],
      sessionPublicKey: "mock-session-public-key",
      sessionToken: "mock-session-token",
      nonceSignature: signNonce(request.nonce)
    })
  });

  return NextResponse.json(await callbackRes.json(), { status: callbackRes.status });
}
