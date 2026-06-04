// GET /app/api/rigs — public catalogue of listed rigs + live pool stats.
// Owner addresses are never exposed; rigs are referenced by rigId.
import { NextResponse } from "next/server";
import { prisma } from "@/server/db";
import { poolClient, type RigSummary } from "@/server/poolClient";

export const dynamic = "force-dynamic";

export async function GET() {
  const listed = await prisma.rig.findMany({
    where: { status: "ACTIVE" },
    orderBy: { createdAt: "desc" },
  });
  let liveById = new Map<string, RigSummary>();
  try {
    const rigs = await poolClient.listRigs();
    liveById = new Map(rigs.map((r) => [r.rig_id, r]));
  } catch {
    /* pool unreachable — return listings without live stats */
  }
  const items = listed.map((rig) => {
    const live = liveById.get(rig.rigId) ?? null;
    return {
      rigId: rig.rigId,
      workerName: rig.workerName,
      pricePerHourUsd: rig.pricePerHourUsd,
      supportedCoins: rig.supportedCoins,
      online: live?.online ?? false,
      rented: live?.rented ?? false,
      hashrate_1m: live?.hashrate_1m ?? 0,
      hashrate_15m: live?.hashrate_15m ?? 0,
      hashrate_1h: live?.hashrate_1h ?? 0,
      pool_mode: live?.pool_mode ?? null,
    };
  });
  return NextResponse.json({ items });
}
