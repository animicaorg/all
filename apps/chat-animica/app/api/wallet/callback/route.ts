import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/src/server/db/prisma";
import { walletCallbackSchema } from "@/src/shared/schemas";
import { getRequestLogger } from "@/src/server/logging/requestLogger";
import { verifyNonceSignature, verifyConnectPayload } from "@/src/server/wallet/connect";
import { redis } from "@/src/server/db/redis";

export async function POST(req: NextRequest) {
  const log = getRequestLogger(req);
  const parsed = walletCallbackSchema.safeParse(await req.json().catch(() => ({})));
  if (!parsed.success) return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });

  const connect = await prisma.walletConnectRequest.findUnique({ where: { id: parsed.data.requestId } });
  if (!connect) return NextResponse.json({ error: "request not found" }, { status: 404 });

  if (connect.status !== "pending") return NextResponse.json({ error: "request already resolved" }, { status: 409 });
  if (new Date() > connect.expiresAt) {
    await prisma.walletConnectRequest.update({ where: { id: connect.id }, data: { status: "expired" } });
    await redis.set(`wallet:request:${connect.id}`, "expired", "EX", 60);
    return NextResponse.json({ error: "request expired" }, { status: 410 });
  }

  const payload = connect.payload as any;
  if (!verifyConnectPayload(payload, connect.signature)) {
    log.warn({ requestId: connect.id }, "connect payload signature mismatch");
    return NextResponse.json({ error: "invalid connect request signature" }, { status: 400 });
  }

  if (!verifyNonceSignature(connect.nonce, parsed.data.nonceSignature)) {
    log.warn({ requestId: connect.id }, "nonce signature mismatch");
    return NextResponse.json({ error: "invalid nonce signature" }, { status: 403 });
  }

  if (!parsed.data.approved) {
    await prisma.walletConnectRequest.update({ where: { id: connect.id }, data: { status: "rejected" } });
    await redis.set(`wallet:request:${connect.id}`, "rejected", "EX", 5 * 60);
    log.info({ requestId: connect.id, userId: connect.userId }, "wallet connect rejected");
    return NextResponse.json({ ok: true, status: "rejected" });
  }

  const accounts = parsed.data.accounts?.filter((account) => account.startsWith("anim")) ?? [];
  if (!accounts.length) return NextResponse.json({ error: "no valid accounts provided" }, { status: 400 });

  const session = await prisma.walletSession.create({
    data: {
      userId: connect.userId,
      type: "deeplink",
      accounts,
      status: "active",
      metadata: {
        sessionPublicKey: parsed.data.sessionPublicKey,
        sessionToken: parsed.data.sessionToken,
        requestId: connect.id
      }
    }
  });

  await prisma.walletConnectRequest.update({ where: { id: connect.id }, data: { status: "approved" } });
  await redis.set(`wallet:request:${connect.id}`, "approved", "EX", 10 * 60);
  log.info({ requestId: connect.id, sessionId: session.id, userId: connect.userId }, "wallet connect approved");

  return NextResponse.json({ ok: true, status: "approved", sessionId: session.id });
}
