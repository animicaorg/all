"use client";

import { useQuery } from "@tanstack/react-query";

async function getJson<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(path, { credentials: "include" });
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null;
  }
}

function hr(h: number): string {
  if (!h) return "0 H/s";
  if (h >= 1e9) return `${(h / 1e9).toFixed(2)} GH/s`;
  if (h >= 1e6) return `${(h / 1e6).toFixed(2)} MH/s`;
  if (h >= 1e3) return `${(h / 1e3).toFixed(2)} kH/s`;
  return `${h.toFixed(0)} H/s`;
}

export default function StatsPage() {
  const { data: pool } = useQuery({ queryKey: ["pool-summary"], queryFn: () => getJson<any>("/api/mining/pool-summary"), refetchInterval: 15000 });
  const { data: xmr } = useQuery({ queryKey: ["xmr-summary"], queryFn: () => getJson<any>("/api/pool/xmr/summary"), refetchInterval: 30000 });
  const { data: blocks } = useQuery({ queryKey: ["recent-blocks"], queryFn: () => getJson<any>("/api/blocks/recent"), refetchInterval: 30000 });
  const { data: rev } = useQuery({ queryKey: ["rev-pub"], queryFn: () => getJson<any>("/api/revenue/public") });

  const blockItems: any[] = blocks?.items ?? blocks?.recent_blocks ?? [];

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold">Pool stats</h1>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Network hashrate" value={hr(Number(pool?.network_hashrate_hps ?? 0))} />
        <Stat label="Active miners" value={String(pool?.num_miners ?? pool?.miners ?? 0)} />
        <Stat label="Blocks found" value={String(pool?.blocks_found_total ?? 0)} />
        <Stat label="ANM price" value={rev?.prices?.anmUsd ? `$${Number(rev.prices.anmUsd)}` : "—"} />
      </section>

      <p className="-mt-4 text-xs text-white/40">
        {pool?.hashrate_source === "reported"
          ? `Live miner-reported (${pool?.reporting_miners ?? 0} reporting)`
          : "Estimated from share work (no miners reporting yet)"}
      </p>

      {pool?.hashrate_source !== "reported" && (
        <section className="grid gap-4 sm:grid-cols-3">
          <Stat label="Network hashrate 1m" value={hr(Number(pool?.hashrate_raw_1m ?? 0))} sub />
          <Stat label="Network hashrate 15m" value={hr(Number(pool?.hashrate_raw_15m ?? 0))} sub />
          <Stat label="Network hashrate 1h" value={hr(Number(pool?.hashrate_raw_1h ?? 0))} sub />
        </section>
      )}

      {(() => {
        const pm = xmr?.projected_monero;
        const pmHps = Number(pm?.projected_monero_hps ?? 0);
        const threads = Number(pm?.est_cpu_threads ?? 0);
        return (
          <section className="card">
            <h2 className="font-medium text-neon-violet">Projected Monero (XMR) hashrate</h2>
            <p className="mt-1 text-xs text-white/40">
              Estimate of the RandomX hashrate this fleet could produce if every miner also dual-mined Monero — derived
              from the Animica (SHA3) network hashrate via a per-thread conversion. Assumes CPU miners; an upper bound.
            </p>
            <div className="mt-3 grid gap-3 sm:grid-cols-3 text-sm">
              <Row label="Projected RandomX" value={hr(pmHps)} />
              <Row label="Est. CPU threads" value={threads >= 1 ? threads.toFixed(0) : threads.toFixed(2)} />
              <Row label="From Animica net" value={hr(Number(pool?.network_hashrate_hps ?? 0))} />
            </div>
          </section>
        );
      })()}

      {xmr?.enabled && (() => {
        const height = Number(xmr.monerod_height ?? 0);
        const target = Number(xmr.monerod_target ?? 0);
        // monerod reports target_height=0 once caught up; treat synced as 100%.
        const pct = xmr.monerod_synced ? 100 : target > height && target > 0 ? (height / target) * 100 : height > 0 ? 100 : 0;
        return (
          <section className="card">
            <h2 className="font-medium text-neon-violet">Monero (XMR) dual-mining</h2>
            <div className="mt-3 space-y-1">
              <div className="flex items-center justify-between text-sm">
                <span className="text-white/60">monerod sync</span>
                <span className={`font-medium ${xmr.monerod_synced ? "text-neon-green" : "text-neon-violet"}`}>
                  {pct.toFixed(pct >= 100 ? 0 : 2)}%{xmr.monerod_synced ? " · synced" : ""}
                </span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-white/10">
                <div className={`h-full rounded-full ${xmr.monerod_synced ? "bg-neon-green" : "bg-neon-violet"}`} style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
              </div>
              {target > 0 && (
                <p className="text-xs text-white/40">block {height.toLocaleString()} / {target.toLocaleString()}</p>
              )}
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-3 text-sm">
              <Row label="XMR miners" value={String(xmr.active_miners ?? 0)} />
              <Row label="XMR blocks" value={String(xmr.blocks_found ?? 0)} />
              <Row label="monerod height" value={height ? height.toLocaleString() : "—"} />
            </div>
          </section>
        );
      })()}

      <section className="space-y-2">
        <h2 className="font-medium">Recent blocks</h2>
        {blockItems.length === 0 ? (
          <p className="text-white/60">No blocks reported yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-white/10">
            <table className="w-full text-sm">
              <thead className="bg-white/5 text-left text-white/50"><tr>
                <th className="px-3 py-2">Height</th><th className="px-3 py-2">Miner</th><th className="px-3 py-2">Reward</th><th className="px-3 py-2">When</th>
              </tr></thead>
              <tbody>
                {blockItems.slice(0, 15).map((b, i) => (
                  <tr key={i} className="border-t border-white/5">
                    <td className="px-3 py-2">{b.height}</td>
                    <td className="px-3 py-2 text-white/60">{(b.worker || b.miner || b.address || "—").toString().slice(0, 20)}</td>
                    <td className="px-3 py-2">{b.reward ?? "—"}</td>
                    <td className="px-3 py-2 text-white/40">{b.timestamp || b.ts || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {rev && (
        <section className="card">
          <h2 className="font-medium text-neon-blue">Platform revenue (30d)</h2>
          <div className="mt-2 grid gap-3 sm:grid-cols-3 text-sm">
            <Row label="Revenue" value={`$${Number(rev.revenue30dUsd ?? 0).toFixed(2)}`} />
            <Row label="Net" value={`$${Number(rev.net30dUsd ?? 0).toFixed(2)}`} />
            <Row label="Gross margin" value={`${Number(rev.grossMarginPct ?? 0).toFixed(0)}%`} />
          </div>
        </section>
      )}
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: boolean }) {
  return (
    <div className="card">
      <p className="text-sm text-white/50">{label}</p>
      <p className={`mt-1 font-semibold ${sub ? "text-xl" : "text-2xl text-neon-blue"}`}>{value}</p>
    </div>
  );
}
function Row({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg border border-white/5 bg-black/20 p-3"><p className="text-xs text-white/50">{label}</p><p className="mt-1 font-medium">{value}</p></div>;
}
