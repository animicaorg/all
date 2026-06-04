// GET  /app/api/owner/listings  → the caller's rigs (with live pool stats)
// POST /app/api/owner/listings  → verify address ownership, then create/
//                                 update a listing for (workerName, address)
import { NextResponse } from "next/server";
import { z } from "zod";
import { requireUser } from "@/server/auth";
import { prisma } from "@/server/db";
import { poolClient } from "@/server/poolClient";
import { auditLog } from "@/lib/audit";
import { rigIdFor, isAnim1, isCoins } from "@/lib/rig";

export const dynamic = "force-dynamic";

export async function GET() {
  let user;
  try {
    user = await requireUser();
  } catch {
    return NextResponse.json({ error: "auth required" }, { status: 401 });
  }
  const rigs = await prisma.rig.findMany({
    where: { ownerUserId: user.id },
    orderBy: { createdAt: "desc" },
  });
  // Annotate with live pool stats (best-effort).
  const items = await Promise.all(
    rigs.map(async (rig) => {
      let live = null;
      try {
        live = await poolClient.rigStats(rig.rigId);
      } catch {
        /* pool unreachable — show listing without live stats */
      }
      return { ...rig, live };
    }),
  );
  return NextResponse.json({ items });
}

const CreateBody = z.object({
  address: z.string(),
  workerName: z.string().min(1).max(64),
  claimWorker: z.string().min(1),
  pricePerHourUsd: z.string(),
  payoutCurrency: z.string().min(1),
  payoutAddress: z.string().min(1),
  supportedCoins: z.string(),
});

export async function POST(req: Request) {
  let user;
  try {
    user = await requireUser();
  } catch {
    return NextResponse.json({ error: "auth required" }, { status: 401 });
  }
  let body: z.infer<typeof CreateBody>;
  try {
    body = CreateBody.parse(await req.json());
  } catch {
    return NextResponse.json({ error: "invalid listing payload" }, { status: 400 });
  }
  if (!isAnim1(body.address)) {
    return NextResponse.json({ error: "address must be anim1…" }, { status: 400 });
  }
  if (!isCoins(body.supportedCoins)) {
    return NextResponse.json({ error: "supportedCoins must be ANM, XMR, or BOTH" }, { status: 400 });
  }
  const price = Number(body.pricePerHourUsd);
  if (!Number.isFinite(price) || price <= 0) {
    return NextResponse.json({ error: "pricePerHourUsd must be > 0" }, { status: 400 });
  }

  // Prove the owner controls this address (mined with the claim worker).
  let proven = false;
  try {
    const res = await poolClient.ownershipVerify(body.address, body.claimWorker);
    proven = res.proven;
  } catch (err) {
    return NextResponse.json({ error: `ownership check failed: ${err}` }, { status: 502 });
  }
  if (!proven) {
    return NextResponse.json(
      { error: "Ownership not proven yet — mine with the claim worker for ~2 minutes, then retry." },
      { status: 409 },
    );
  }

  const rigId = rigIdFor(body.workerName, body.address);
  const existing = await prisma.rig.findUnique({ where: { rigId } });
  if (existing && existing.ownerUserId !== user.id) {
    return NextResponse.json({ error: "rig already claimed by another account" }, { status: 409 });
  }
  const rig = await prisma.rig.upsert({
    where: { rigId },
    create: {
      ownerUserId: user.id,
      rigId,
      workerName: body.workerName,
      ownerAddress: body.address,
      ownershipProvenAt: new Date(),
      pricePerHourUsd: body.pricePerHourUsd,
      payoutCurrency: body.payoutCurrency.toLowerCase(),
      payoutAddress: body.payoutAddress,
      supportedCoins: body.supportedCoins,
      status: "ACTIVE",
    },
    update: {
      ownershipProvenAt: new Date(),
      pricePerHourUsd: body.pricePerHourUsd,
      payoutCurrency: body.payoutCurrency.toLowerCase(),
      payoutAddress: body.payoutAddress,
      supportedCoins: body.supportedCoins,
      status: "ACTIVE",
    },
  });
  await auditLog({
    action: "rig.listed",
    entityType: "Rig",
    entityId: rig.id,
    actor: user.email,
    metadata: { rigId, coins: body.supportedCoins, pricePerHourUsd: body.pricePerHourUsd },
  });
  return NextResponse.json({ rig });
}
