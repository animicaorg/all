"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

function fmtCountdown(s: number): string {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

// Live per-second payout countdown, re-seeded whenever the API value changes.
function usePayoutCountdown(seconds: number | undefined, enabled: boolean): number | null {
  const [remaining, setRemaining] = useState<number | null>(null);
  useEffect(() => {
    if (!enabled || seconds == null) { setRemaining(null); return; }
    setRemaining(seconds);
    const id = setInterval(() => setRemaining((r) => (r == null ? null : Math.max(0, r - 1))), 1000);
    return () => clearInterval(id);
  }, [seconds, enabled]);
  return remaining;
}

// Aggregate AICF worker-fleet stats straight from the node (CORS is open).
// "phones_online" = the browser Serve&Earn page (webllm) + Termux thin workers.
async function getWorkerCount(): Promise<any | null> {
  try {
    const r = await fetch("https://rpc.animica.org/rpc", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "aicf.workerCount", params: { online_window_s: 900 } }),
    });
    const j = await r.json();
    return j?.result ?? null;
  } catch {
    return null;
  }
}

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
  // /api/pool/network carries the scope + sample-size + role fields. The old
  // xmr-summary query was removed: XMR dual-mining was switched off and
  // monerod removed on 2026-07-16, so that endpoint is gone (404).
  const { data: net } = useQuery({ queryKey: ["pool-network"], queryFn: () => getJson<any>("/api/pool/network"), refetchInterval: 30000 });
  const { data: blocks } = useQuery({ queryKey: ["recent-blocks"], queryFn: () => getJson<any>("/api/blocks/recent"), refetchInterval: 30000 });
  const { data: rev } = useQuery({ queryKey: ["rev-pub"], queryFn: () => getJson<any>("/api/revenue/public") });
  const { data: wc } = useQuery({ queryKey: ["aicf-worker-count"], queryFn: getWorkerCount, refetchInterval: 30000 });
  const payoutCountdown = usePayoutCountdown(
    pool?.payout_countdown_seconds != null ? Number(pool.payout_countdown_seconds) : undefined,
    Boolean(pool?.payouts_enabled),
  );

  const blockItems: any[] = blocks?.items ?? blocks?.recent_blocks ?? [];

  return (
    <div className="space-y-10">
      <header className="space-y-4">
        <span className="badge">Live</span>
        <h1 className="text-4xl font-semibold tracking-tightest text-white md:text-5xl">
          Pool <span className="grad-text">stats</span>
        </h1>
        <p className="max-w-2xl text-lg text-white/65">
          Real-time hashrate, miners, blocks, and platform metrics — refreshed automatically.
        </p>
      </header>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        {/* Called "network" only while this pool demonstrably finds ~all
            blocks (pool_block_share_pct >= 95). The API derives that label
            from measured block share, so it degrades honestly. */}
        <Stat
          label={net?.hashrate_scope === "network_equivalent" ? "Network hashrate" : "Pool hashrate"}
          value={hr(Number(pool?.network_hashrate_hps ?? 0))}
          note={net?.pool_block_share_pct != null ? `pool finds ${net.pool_block_share_pct}% of blocks` : undefined}
        />
        {/* Machines that submitted an ACCEPTED SHARE recently — proof of work,
            not socket presence. num_miners counts stratum sockets and once
            read 838 for a single IP holding 835 idle ones. */}
        <Stat label="Mining machines" value={net?.active_machines != null ? String(net.active_machines) : "—"} />
        {/* Live from aicf.workerCount on the node: every registered inference worker
            seen in the last 15 min — full nodes AND the phone/browser lanes
            (pool.animica.org/serve + the animica-serve Termux worker). */}
        <Stat
          label="Serving inference"
          value={wc?.online != null ? String(wc.online) : net?.inference_workers_serving != null ? String(net.inference_workers_serving) : "—"}
          note={wc ? `${wc.phones_online ?? 0} phone/browser · ${Number(wc.jobs_completed_total ?? 0).toLocaleString()} jobs all-time` : undefined}
        />
        <Stat label="Blocks found" value={String(pool?.blocks_found_total ?? 0)} />
        <Stat label="ANM price" value={rev?.prices?.anmUsd ? `$${Number(rev.prices.anmUsd).toFixed(8).replace(/0+$/, "")}` : "—"} />
        <Stat label="Next payout" value={!pool?.payouts_enabled ? "paused" : payoutCountdown != null ? fmtCountdown(payoutCountdown) : "—"} />
      </section>

      <p className="-mt-4 text-xs text-white/40">
        {pool?.hashrate_source === "reported"
          ? `Live miner-reported (${pool?.reporting_miners ?? 0} reporting)`
          : "Estimated from share work (no miners reporting yet)"}
        {net?.hashrate_scope === "network_equivalent" ? (
          <> · Measured from share work. This pool found{" "}
            <strong className="text-white/70">{net?.pool_blocks_in_window ?? "—"} of {net?.chain_blocks_in_window ?? "—"}</strong>{" "}
            blocks in the last 24h ({net?.pool_block_share_pct}%), so pool and network are the same population and this
            is the chain-wide figure. The node&rsquo;s Θ-derived estimate
            {net?.node_theta_hashrate_hps ? <> ({hr(Number(net.node_theta_hashrate_hps))})</> : null} cannot be
            reconciled with that and overstates by ~95×.
            {net?.unseen_hashrate_bound_pct != null && (
              <> Any miner submitting shares to no pool that also found no block would be invisible here; with{" "}
                {net?.pool_blocks_in_window} blocks and none found elsewhere, that is under{" "}
                <strong className="text-white/70">{net.unseen_hashrate_bound_pct}%</strong> of network hash (95% conf).
                Miners that <em>do</em> submit shares are counted whether or not they ever find a block.</>
            )}</>
        ) : (
          <> · Counts shares submitted to <em>this pool</em> only — it finds {net?.pool_block_share_pct ?? "—"}% of
            blocks, so solo and direct miners are not represented here.</>
        )}
      </p>

      {pool?.hashrate_source !== "reported" && (() => {
        // A window with no accepted shares is NOT 0 H/s — it is no sample. At
        // the current share rate the 1m window is usually empty, and printing
        // "0 H/s" there reads as "the network stopped".
        const n = net?.hashrate_window_samples ?? {};
        const scopeWord = net?.hashrate_scope === "network_equivalent" ? "Network" : "Pool";
        const win = (label: string, hps: any, samples: any) => {
          const c = Number(samples ?? 0);
          return (
            <Stat
              key={label}
              label={`${scopeWord} hashrate ${label}`}
              value={c > 0 ? hr(Number(hps ?? 0)) : "no samples"}
              sub
              note={c > 0 ? `${c} share${c === 1 ? "" : "s"}` : "no shares in window"}
            />
          );
        };
        return (
          <section className="grid gap-4 sm:grid-cols-3">
            {win("1m", pool?.hashrate_raw_1m, n.m1)}
            {win("15m", pool?.hashrate_raw_15m, n.m15)}
            {win("1h", pool?.hashrate_raw_1h, n.h1)}
          </section>
        );
      })()}

      {/* Who is actually contributing, split by role. Monero panels removed:
          XMR dual-mining was switched off and monerod removed 2026-07-16, so
          "projected RandomX" advertised a capability that does not exist. */}
      <section className="card">
        <h2 className="font-medium text-neon-violet">Network participation</h2>
        <p className="mt-1 text-xs text-white/40">
          Counted from work actually done, not from connections held open. A machine counts when it submits an
          accepted share; a worker counts as serving inference when its wallet has a fresh heartbeat and advertises
          a live tier.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
          <Row label="Mining machines" value={net?.active_machines != null ? String(net.active_machines) : "—"} />
          <Row label="Mining wallets" value={net?.active_machine_addresses != null ? String(net.active_machine_addresses) : "—"} />
          <Row label="Serving inference" value={net?.inference_workers_serving != null ? String(net.inference_workers_serving) : "—"} />
          <Row
            label="Mining + inference"
            value={net?.dual_role_machines != null ? `${net.dual_role_machines} machines · ${net.dual_role_addresses ?? 0} wallets` : "—"}
          />
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-3 text-sm">
          <Row label="Mining only" value={net?.mining_only_addresses != null ? `${net.mining_only_addresses} wallets` : "—"} />
          <Row label="Inference only" value={net?.inference_only_addresses != null ? `${net.inference_only_addresses} wallets` : "—"} />
          <Row
            label="Named rigs"
            value={net?.active_machines_named != null ? `${net.active_machines_named} of ${net.active_machines ?? 0}` : "—"}
          />
        </div>
        <p className="mt-3 text-[11px] text-white/35">
          Machines are counted over the last {net?.active_machines_window_seconds ? Math.round(Number(net.active_machines_window_seconds) / 60) : 15} minutes.
          A rig that sends no name is identified by its session, so it can be counted again after a reconnect —
          &ldquo;named rigs&rdquo; shows how much of the count is immune to that.
          {net?.inference_wallets_configured != null && (
            <> Inference is measured across {net.inference_wallets_configured} configured wallet{Number(net.inference_wallets_configured) === 1 ? "" : "s"}.</>
          )}
        </p>
      </section>


      <section className="space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-neon-green/80">Recent blocks</h2>
        {blockItems.length === 0 ? (
          <p className="text-white/60">No blocks reported yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-2xl border border-white/10 bg-white/[0.02] backdrop-blur">
            <table className="w-full text-sm">
              <thead className="bg-white/5 text-left text-white/50"><tr>
                <th className="px-4 py-3 font-medium">Height</th><th className="px-4 py-3 font-medium">Miner</th><th className="px-4 py-3 font-medium">Reward</th><th className="px-4 py-3 font-medium">When</th>
              </tr></thead>
              <tbody>
                {blockItems.slice(0, 15).map((b, i) => (
                  <tr key={i} className="border-t border-white/5 transition-colors hover:bg-white/[0.03]">
                    <td className="px-4 py-3">{b.height}</td>
                    <td className="px-4 py-3 text-white/60">{(b.worker || b.miner || b.address || "—").toString().slice(0, 20)}</td>
                    <td className="px-4 py-3">{b.reward ?? "—"}</td>
                    <td className="px-4 py-3 text-white/40">{b.timestamp || b.ts || ""}</td>
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

function Stat({ label, value, sub, note }: { label: string; value: string; sub?: boolean; note?: string }) {
  return (
    <div className="card">
      <p className="text-xs font-medium uppercase tracking-wide text-white/45">{label}</p>
      <p className={`mt-2 font-semibold tracking-tight ${sub ? "text-xl text-white" : "text-3xl text-neon-blue"}`}>{value}</p>
      {/* Sample size / provenance under a number, so a reader can tell a real
          zero from an empty measurement window. */}
      {note ? <p className="mt-1 text-[11px] text-white/35">{note}</p> : null}
    </div>
  );
}
function Row({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl border border-white/10 bg-black/20 p-3.5"><p className="text-xs text-white/50">{label}</p><p className="mt-1 font-medium text-white">{value}</p></div>;
}
