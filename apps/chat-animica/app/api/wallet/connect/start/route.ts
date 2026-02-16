import { NextRequest, NextResponse } from "next/server";
import { requireUser } from "@/src/server/auth/require";
import { walletConnectStartSchema } from "@/src/shared/schemas";
import { prisma } from "@/src/server/db/prisma";
import { env } from "@/src/server/env";
import { getRequestLogger } from "@/src/server/logging/requestLogger";
import { redis } from "@/src/server/db/redis";
import { ConnectRequestPayload, encodeRequest, makeNonce, signConnectPayload } from "@/src/server/wallet/connect";

const WINDOW_SECONDS = 60;
const LIMIT = 12;

export async function POST(req: NextRequest) {
  const log = getRequestLogger(req);
  const userResult = await requireUser();
  if ("error" in userResult) return userResult.error;

  const ip = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "unknown";
  const rlKey = `wallet:start:${userResult.user.id}:${ip}`;
  const count = await redis.incr(rlKey);
  if (count === 1) await redis.expire(rlKey, WINDOW_SECONDS);
  if (count > LIMIT) {
    log.warn({ userId: userResult.user.id, ip, count }, "wallet connect start rate limited");
    return NextResponse.json({ error: "Too many connect attempts" }, { status: 429 });
  }

  const parsed = walletConnectStartSchema.safeParse(await req.json().catch(() => ({})));
  if (!parsed.success) return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });

  const origin = env.NEXT_PUBLIC_APP_ORIGIN ?? req.nextUrl.origin;
  const callback = env.WALLET_CONNECT_CALLBACK_URL ?? `${origin}/api/wallet/callback`;
  const payload: ConnectRequestPayload = {
    v: 1,
    app: "Animica Studio",
    origin,
    nonce: makeNonce(),
    ts: Math.floor(Date.now() / 1000),
    callback,
    scopes: ["accounts", "signTx"],
    chainId: parsed.data.chainId
  };

  const signature = signConnectPayload(payload);
  const expiresAt = new Date(Date.now() + 5 * 60_000);
  const created = await prisma.walletConnectRequest.create({
    data: {
      userId: userResult.user.id,
      nonce: payload.nonce,
      payload: payload as unknown as object,
      signature,
      status: "pending",
      expiresAt
    }
  });

  await redis.set(`wallet:request:${created.id}`, "pending", "EX", 5 * 60);
  const request = encodeRequest(payload, signature);
  const universalLink = `https://wallet.animica.org/connect?request=${request}`;
  const deepLink = `animicawallet://connect?request=${request}`;

  log.info({ requestId: created.id, userId: userResult.user.id }, "wallet connect request created");

  return NextResponse.json({
    ok: true,
    requestId: created.id,
    requestPayload: payload,
    universalLink,
    deepLink,
    expiresAt: expiresAt.toISOString()
  });
}
