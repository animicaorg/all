import { notFound } from "next/navigation";
import { prisma } from "@/server/db";
import { poolClient } from "@/server/poolClient";
import { getSessionUser } from "@/server/auth";
import { RentForm } from "@/components/RentForm";

export const dynamic = "force-dynamic";

export default async function RigPage({ params }: { params: { rigId: string } }) {
  const rig = await prisma.rig.findUnique({ where: { rigId: params.rigId } });
  if (!rig || rig.status !== "ACTIVE") notFound();
  const user = await getSessionUser();
  let live = null;
  try {
    live = await poolClient.rigStats(rig.rigId);
  } catch {
    /* pool offline */
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{rig.workerName}</h1>
        <p className="mt-1 text-white/60">
          ${rig.pricePerHourUsd}/hr · supports {rig.supportedCoins} ·{" "}
          <span className={live?.online ? "text-green-400" : "text-white/40"}>
            {live?.rented ? "currently rented" : live?.online ? "online" : "offline"}
          </span>
        </p>
      </div>
      {!user ? (
        <p className="rounded-lg border border-white/10 bg-white/5 p-4 text-sm">
          <a href="/app/login" className="text-blue-400">Sign in</a> to rent this rig.
        </p>
      ) : live?.rented ? (
        <p className="rounded-lg border border-white/10 bg-white/5 p-4 text-sm text-white/60">
          This rig is currently rented. Check back when it’s available.
        </p>
      ) : (
        <RentForm
          rigId={rig.rigId}
          pricePerHourUsd={rig.pricePerHourUsd}
          supportedCoins={rig.supportedCoins as "ANM" | "XMR" | "BOTH"}
        />
      )}
    </div>
  );
}
