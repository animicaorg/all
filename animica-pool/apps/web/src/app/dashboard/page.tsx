"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api } from "@/lib/api";

interface Me {
  user: { email: string; role: string; displayName?: string; referralCode?: string } | null;
}

export default function Dashboard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["me"],
    queryFn: () => api<Me>("/api/auth/me"),
    retry: false,
  });

  if (isLoading) return <p className="text-white/60">Loading…</p>;
  if (error || !data?.user) {
    return (
      <p className="card">
        You’re not signed in. <Link href="/login" className="text-neon-blue">Sign in</Link>.
      </p>
    );
  }
  const u = data.user;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="text-white/60">{u.displayName || u.email} · {u.role}</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Tile href="/api-keys" title="API keys" desc="Create & manage inference keys" />
        <Tile href="/credits" title="Credits" desc="Buy AI credits with crypto" />
        <Tile href="/mine" title="Mining" desc="Set up CPU mining" />
        <Tile href="/workers" title="Workers" desc="Connect a CPU/GPU machine" />
        <Tile href="/payouts" title="Payouts" desc="Wallets & payout preferences" />
        {u.role === "ADMIN" && <Tile href="/admin" title="Admin" desc="Revenue & payouts" />}
      </div>
      {u.referralCode && (
        <p className="text-sm text-white/50">Referral code: <code className="text-neon-green">{u.referralCode}</code></p>
      )}
    </div>
  );
}

function Tile({ href, title, desc }: { href: string; title: string; desc: string }) {
  return (
    <Link href={href} className="card hover:border-neon-violet/60">
      <h3 className="font-medium">{title}</h3>
      <p className="mt-1 text-sm text-white/60">{desc}</p>
    </Link>
  );
}
