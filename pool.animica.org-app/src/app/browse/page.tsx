import Link from "next/link";
import { prisma } from "@/server/db";
import { poolClient, type RigSummary } from "@/server/poolClient";

export const dynamic = "force-dynamic";

function fmtHash(h: number): string {
  if (h >= 1e9) return `${(h / 1e9).toFixed(2)} GH/s`;
  if (h >= 1e6) return `${(h / 1e6).toFixed(2)} MH/s`;
  if (h >= 1e3) return `${(h / 1e3).toFixed(2)} kH/s`;
  return `${h.toFixed(1)} H/s`;
}

export default async function BrowsePage() {
  const listed = await prisma.rig.findMany({ where: { status: "ACTIVE" }, orderBy: { createdAt: "desc" } });
  let liveById = new Map<string, RigSummary>();
  try {
    liveById = new Map((await poolClient.listRigs()).map((r) => [r.rig_id, r]));
  } catch {
    /* pool offline */
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Rigs for rent</h1>
      {listed.length === 0 && <p className="text-white/60">No rigs are listed yet.</p>}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {listed.map((rig) => {
          const live = liveById.get(rig.rigId);
          const available = live?.online && !live?.rented;
          return (
            <Link
              key={rig.rigId}
              href={`/rig/${rig.rigId}`}
              className="rounded-xl border border-white/10 bg-white/5 p-4 hover:border-blue-500/50"
            >
              <div className="flex items-center justify-between">
                <span className="font-medium">{rig.workerName}</span>
                <span className={`text-xs ${available ? "text-green-400" : "text-white/40"}`}>
                  {live?.rented ? "rented" : live?.online ? "online" : "offline"}
                </span>
              </div>
              <div className="mt-2 text-sm text-white/60">
                ${rig.pricePerHourUsd}/hr · {rig.supportedCoins}
              </div>
              <div className="mt-1 text-xs text-white/40">
                {live ? fmtHash(live.hashrate_15m) : "—"}
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
