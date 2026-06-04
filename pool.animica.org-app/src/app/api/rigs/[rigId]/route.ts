// GET /app/api/rigs/[rigId] — public detail for one listed rig.
import { NextResponse } from "next/server";
import { prisma } from "@/server/db";
import { poolClient } from "@/server/poolClient";

export const dynamic = "force-dynamic";

export async function GET(_req: Request, { params }: { params: { rigId: string } }) {
  const rig = await prisma.rig.findUnique({ where: { rigId: params.rigId } });
  if (!rig || rig.status !== "ACTIVE") {
    return NextResponse.json({ error: "not found" }, { status: 404 });
  }
  let live = null;
  try {
    live = await poolClient.rigStats(rig.rigId);
  } catch {
    /* best-effort */
  }
  return NextResponse.json({
    rig: {
      rigId: rig.rigId,
      workerName: rig.workerName,
      pricePerHourUsd: rig.pricePerHourUsd,
      supportedCoins: rig.supportedCoins,
    },
    live,
  });
}
