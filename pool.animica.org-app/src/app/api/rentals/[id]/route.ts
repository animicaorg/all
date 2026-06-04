// GET /app/api/rentals/[id] — rental status (renter or rig owner only).
import { NextResponse } from "next/server";
import { requireUser } from "@/server/auth";
import { prisma } from "@/server/db";

export const dynamic = "force-dynamic";

export async function GET(_req: Request, { params }: { params: { id: string } }) {
  let user;
  try {
    user = await requireUser();
  } catch {
    return NextResponse.json({ error: "auth required" }, { status: 401 });
  }
  const rental = await prisma.rental.findUnique({
    where: { id: params.id },
    include: { rig: true },
  });
  if (!rental) return NextResponse.json({ error: "not found" }, { status: 404 });
  const isRenter = rental.renterUserId === user.id;
  const isOwner = rental.rig.ownerUserId === user.id;
  if (!isRenter && !isOwner && !user.isAdmin) {
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }
  return NextResponse.json({
    rental: {
      id: rental.id,
      status: rental.status,
      hours: rental.hours,
      grossUsd: rental.grossUsd,
      ownerNetUsd: rental.ownerNetUsd,
      marketplaceFeeUsd: rental.marketplaceFeeUsd,
      coins: rental.coins,
      anmMode: rental.anmMode,
      windowStartAt: rental.windowStartAt,
      windowEndAt: rental.windowEndAt,
      measuredUptimeSeconds: rental.measuredUptimeSeconds,
      refundUsd: rental.refundUsd,
      ownerEarnedUsd: rental.ownerEarnedUsd,
      invoiceUrl: rental.invoiceUrl,
      workerName: rental.rig.workerName,
      role: isOwner ? "owner" : "renter",
    },
  });
}
