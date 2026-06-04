import Link from "next/link";
import { prisma } from "@/server/db";
import { getSessionUser } from "@/server/auth";

export const dynamic = "force-dynamic";

export default async function OwnerPage() {
  const user = await getSessionUser();
  if (!user) {
    return (
      <p className="rounded-lg border border-white/10 bg-white/5 p-4 text-sm">
        <a href="/app/login" className="text-blue-400">Sign in</a> to list and manage your rigs.
      </p>
    );
  }
  const rigs = await prisma.rig.findMany({ where: { ownerUserId: user.id }, orderBy: { createdAt: "desc" } });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Your rigs</h1>
        <Link href="/owner/list" className="rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-500">
          List a rig
        </Link>
      </div>
      {rigs.length === 0 && <p className="text-white/60">You haven’t listed any rigs yet.</p>}
      <div className="space-y-3">
        {rigs.map((rig) => (
          <div key={rig.id} className="rounded-xl border border-white/10 bg-white/5 p-4">
            <div className="flex items-center justify-between">
              <span className="font-medium">{rig.workerName}</span>
              <span className="text-xs text-white/40">{rig.status}</span>
            </div>
            <div className="mt-1 text-sm text-white/60">
              ${rig.pricePerHourUsd}/hr · {rig.supportedCoins} · payout {rig.payoutCurrency}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
