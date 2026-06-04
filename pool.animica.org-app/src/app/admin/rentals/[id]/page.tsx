import { notFound } from "next/navigation";
import { prisma } from "@/server/db";
import { getSessionUser } from "@/server/auth";
import { AdminActions } from "@/components/AdminActions";

export const dynamic = "force-dynamic";

export default async function AdminRentalPage({ params }: { params: { id: string } }) {
  const user = await getSessionUser();
  if (!user?.isAdmin) {
    return <p className="rounded-lg border border-white/10 bg-white/5 p-4 text-sm">Admin only.</p>;
  }
  const rental = await prisma.rental.findUnique({
    where: { id: params.id },
    include: { rig: true, renter: { select: { email: true } } },
  });
  if (!rental) notFound();

  const rows: [string, string][] = [
    ["Status", rental.status],
    ["Rig", `${rental.rig.workerName} (${rental.rig.rigId.slice(0, 12)}…)`],
    ["Owner address", rental.rig.ownerAddress],
    ["Renter", rental.renter.email],
    ["Hours", String(rental.hours)],
    ["Coins", rental.coins + (rental.anmMode ? ` (${rental.anmMode})` : "")],
    ["Gross / Owner net / Fee", `$${rental.grossUsd} / $${rental.ownerNetUsd} / $${rental.marketplaceFeeUsd}`],
    ["Measured uptime (s)", String(rental.measuredUptimeSeconds ?? "—")],
    ["Owner earned / Refund", `$${rental.ownerEarnedUsd ?? "—"} / $${rental.refundUsd ?? "—"}`],
    ["Owner payout", `${rental.ownerPayoutCurrency ?? "—"} → ${rental.ownerPayoutAddress ?? "—"}`],
    ["NP payment / payout / refund", `${rental.npPaymentId ?? "—"} / ${rental.npPayoutId ?? "—"} / ${rental.npRefundPayoutId ?? "—"}`],
    ["Window", `${rental.windowStartAt?.toISOString() ?? "—"} → ${rental.windowEndAt?.toISOString() ?? "—"}`],
  ];

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <h1 className="text-2xl font-semibold">Rental {rental.id.slice(0, 8)}</h1>
      <div className="rounded-xl border border-white/10 bg-white/5 p-5 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-4 border-b border-white/5 py-1.5 last:border-0">
            <span className="text-white/50">{k}</span>
            <span className="text-right break-all">{v}</span>
          </div>
        ))}
      </div>
      <AdminActions rentalId={rental.id} />
    </div>
  );
}
