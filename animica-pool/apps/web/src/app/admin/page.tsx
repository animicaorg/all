"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface Overview {
  totalUsers: number;
  miningAccounts: number;
  activeWorkers: number;
  activeRentals: number;
  inferenceRequestsToday: number;
  pendingPayouts: number;
}

export default function AdminPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin-overview"],
    queryFn: () => api<Overview>("/api/admin/overview"),
    retry: false,
  });

  if (isLoading) return <p className="text-white/60">Loading…</p>;
  if (error) return <p className="card">Admin only — sign in as an admin.</p>;

  const stats: [string, number][] = [
    ["Total users", data!.totalUsers],
    ["Mining accounts", data!.miningAccounts],
    ["Active workers", data!.activeWorkers],
    ["Active rentals", data!.activeRentals],
    ["Inference (24h)", data!.inferenceRequestsToday],
    ["Pending payouts", data!.pendingPayouts],
  ];

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Admin · overview</h1>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {stats.map(([label, value]) => (
          <div key={label} className="card">
            <p className="text-sm text-white/50">{label}</p>
            <p className="mt-1 text-3xl font-semibold text-neon-blue">{value}</p>
          </div>
        ))}
      </div>
      <p className="text-sm text-white/40">
        Provider management, payout review, revenue ledger, mining/worker/customer management land in later phases.
      </p>
    </div>
  );
}
