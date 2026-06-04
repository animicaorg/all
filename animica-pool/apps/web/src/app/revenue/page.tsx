"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface PublicRev {
  revenue30dUsd: number; net30dUsd: number; allTimeNetUsd: number; grossMarginPct: number;
  redistribution: { providers: number; treasury: number; referral: number };
  allocation: { providersUsd: number; treasuryUsd: number; referralUsd: number };
}
interface Referrals {
  code: string | null; link: string | null; signups: number; pendingUsd: number; paidUsd: number;
  referrals: { id: string; rewardUsd: string; status: string }[];
}

export default function RevenuePage() {
  const { data: rev } = useQuery({ queryKey: ["revenue-public"], queryFn: () => api<PublicRev>("/api/revenue/public") });
  const { data: ref } = useQuery({ queryKey: ["referrals"], queryFn: () => api<Referrals>("/api/referrals"), retry: false });

  return (
    <div className="space-y-8">
      <section className="space-y-4">
        <h1 className="text-2xl font-semibold">Revenue & redistribution</h1>
        <p className="text-white/60">Net realized profit is redistributed across compute providers, the Animica treasury, and referrals.</p>
        {rev && (
          <>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Stat label="Revenue (30d)" value={`$${rev.revenue30dUsd.toFixed(2)}`} />
              <Stat label="Net (30d)" value={`$${rev.net30dUsd.toFixed(2)}`} />
              <Stat label="Net (all-time)" value={`$${rev.allTimeNetUsd.toFixed(2)}`} />
              <Stat label="Gross margin" value={`${rev.grossMarginPct.toFixed(0)}%`} />
            </div>
            <div className="card">
              <h2 className="font-medium text-neon-blue">Redistribution split</h2>
              <div className="mt-2 grid gap-3 sm:grid-cols-3 text-sm">
                <Row label={`Compute providers (${rev.redistribution.providers}%)`} value={`$${rev.allocation.providersUsd.toFixed(2)}`} />
                <Row label={`Treasury (${rev.redistribution.treasury}%)`} value={`$${rev.allocation.treasuryUsd.toFixed(2)}`} />
                <Row label={`Referrals (${rev.redistribution.referral}%)`} value={`$${rev.allocation.referralUsd.toFixed(2)}`} />
              </div>
            </div>
          </>
        )}
      </section>

      {ref?.code && (
        <section className="card space-y-3">
          <h2 className="font-medium text-neon-blue">Your referrals</h2>
          <p className="text-sm text-white/70">Share your link and earn a % of referred customers&apos; purchases.</p>
          <pre className="overflow-x-auto rounded-lg bg-black/40 p-3 text-sm text-neon-green">{ref.link}</pre>
          <div className="grid gap-3 sm:grid-cols-3 text-sm">
            <Row label="Signups" value={String(ref.signups)} />
            <Row label="Pending rewards" value={`$${ref.pendingUsd.toFixed(2)}`} />
            <Row label="Paid rewards" value={`$${ref.paidUsd.toFixed(2)}`} />
          </div>
        </section>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div className="card"><p className="text-sm text-white/50">{label}</p><p className="mt-1 text-2xl font-semibold text-neon-blue">{value}</p></div>;
}
function Row({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg border border-white/5 bg-black/20 p-3"><p className="text-xs text-white/50">{label}</p><p className="mt-1 font-medium">{value}</p></div>;
}
