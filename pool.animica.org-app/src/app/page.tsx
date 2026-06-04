import Link from "next/link";

export default function HomePage() {
  return (
    <div className="space-y-10">
      <section className="space-y-3">
        <h1 className="text-3xl font-semibold tracking-tight md:text-4xl">
          Rent mining rigs. Get paid in crypto.
        </h1>
        <p className="max-w-2xl text-white/70">
          Owners mine for themselves and rent their rigs out when idle. Renters pay
          by the hour in any crypto and point a rig at their own ANM or Monero
          payout for the rental window. Settlement and payouts run through
          NOWPayments; a 5% marketplace fee applies.
        </p>
      </section>

      <div className="grid gap-4 md:grid-cols-2">
        <Link
          href="/browse"
          className="rounded-xl border border-white/10 bg-white/5 p-6 hover:border-blue-500/50"
        >
          <h2 className="text-xl font-medium">Rent a rig →</h2>
          <p className="mt-2 text-sm text-white/60">
            Browse live rigs, pick hours and coin (ANM PPS/solo, Monero, or both),
            and pay in the crypto of your choice.
          </p>
        </Link>
        <Link
          href="/owner"
          className="rounded-xl border border-white/10 bg-white/5 p-6 hover:border-blue-500/50"
        >
          <h2 className="text-xl font-medium">Rent out your rig →</h2>
          <p className="mt-2 text-sm text-white/60">
            Prove your rig, set an hourly price and payout currency, and earn 95%
            of every rental — auto-converted to your coin. Your rig goes back to
            mining for you the moment a rental ends.
          </p>
        </Link>
      </div>
    </div>
  );
}
