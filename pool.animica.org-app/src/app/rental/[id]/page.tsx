import { notFound } from "next/navigation";
import { prisma } from "@/server/db";
import { getSessionUser } from "@/server/auth";

export const dynamic = "force-dynamic";

const STATUS_COPY: Record<string, string> = {
  WAITING_PAYMENT: "Waiting for your payment to confirm.",
  PAYMENT_DETECTED: "Payment detected — confirming on-chain.",
  PAYMENT_CONFIRMED: "Paid. Engaging your rig shortly…",
  ACTIVE: "Active — the rig is mining for you.",
  COMPLETING: "Rental ended. Settling the owner payout.",
  OWNER_PAID: "Owner paid. Finishing up.",
  COMPLETE: "Complete.",
  REFUND_DUE: "Rig under-delivered — preparing a pro-rata refund.",
  REFUND_PROCESSING: "Refund in progress.",
  REFUNDED: "Refunded.",
  EXPIRED: "Expired (no payment received).",
  FAILED: "Failed.",
  CANCELLED: "Cancelled.",
  NEEDS_ADMIN_REVIEW: "Under review by an operator.",
};

export default async function RentalPage({ params }: { params: { id: string } }) {
  const user = await getSessionUser();
  if (!user) {
    return (
      <p className="rounded-lg border border-white/10 bg-white/5 p-4 text-sm">
        <a href="/app/login" className="text-blue-400">Sign in</a> to view this rental.
      </p>
    );
  }
  const rental = await prisma.rental.findUnique({ where: { id: params.id }, include: { rig: true } });
  if (!rental) notFound();
  const isParty = rental.renterUserId === user.id || rental.rig.ownerUserId === user.id || user.isAdmin;
  if (!isParty) notFound();

  return (
    <div className="mx-auto max-w-xl space-y-5">
      <h1 className="text-2xl font-semibold">Rental {rental.id.slice(0, 8)}</h1>
      <div className="rounded-xl border border-white/10 bg-white/5 p-5 text-sm">
        <Row k="Rig" v={rental.rig.workerName} />
        <Row k="Status" v={`${rental.status} — ${STATUS_COPY[rental.status] ?? ""}`} />
        <Row k="Hours" v={String(rental.hours)} />
        <Row k="Coins" v={rental.coins + (rental.anmMode ? ` (${rental.anmMode})` : "")} />
        <Row k="Total" v={`$${rental.grossUsd}`} />
        <Row k="Owner net (95%)" v={`$${rental.ownerNetUsd}`} />
        {rental.windowEndAt && <Row k="Window ends" v={rental.windowEndAt.toISOString()} />}
        {rental.refundUsd && <Row k="Refund" v={`$${rental.refundUsd}`} />}
      </div>
      {rental.status === "WAITING_PAYMENT" && rental.invoiceUrl && (
        <a href={rental.invoiceUrl} className="inline-block rounded-lg bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-500">
          Complete payment →
        </a>
      )}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between border-b border-white/5 py-1.5 last:border-0">
      <span className="text-white/50">{k}</span>
      <span className="text-right">{v}</span>
    </div>
  );
}
