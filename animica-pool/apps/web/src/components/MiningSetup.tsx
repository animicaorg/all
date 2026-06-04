"use client";

import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { api } from "@/lib/api";

type Mode = "ANM_ONLY" | "XMR_ONLY" | "DUAL_50_50";
const ASSETS = ["ANM", "XMR", "SOL", "USDT", "BTC", "SPLIT_ANM_XMR", "ORIGINAL_ASSET"];

interface Stats {
  account: { username: string; workerName: string; mode: string; payoutAsset: string; anmAddress: string; xmrAddress?: string; payoutAddress?: string };
  connection: { poolUrl: string; command: string };
  poolFeePercent: number;
  stats: { source: string; hashrate: number; hashrate24h: number; acceptedShares: number; rejectedShares: number; pendingBalanceAnm: number; paidBalanceAnm: number; blocksFound: number; contributionPct: number };
  estimatedEarningsUsd: number;
}
interface Worker { id: string; name: string; status: string }

export function MiningSetup({ mode, title }: { mode: Mode; title: string }) {
  const qc = useQueryClient();
  const wantsXmr = mode !== "ANM_ONLY";

  const { data: stats } = useQuery({ queryKey: ["mining-stats"], queryFn: () => api<Stats>("/api/mining/stats"), retry: false });
  const { data: workers } = useQuery({ queryKey: ["workers"], queryFn: () => api<Worker[]>("/api/workers"), retry: false });

  // Minimal fields. Everything else is defaulted or behind "Advanced".
  const [anmAddress, setAnmAddress] = useState("");
  const [xmrAddress, setXmrAddress] = useState("");
  const [workerName, setWorkerName] = useState("");
  const [adv, setAdv] = useState(false);
  const [username, setUsername] = useState("");
  const [payoutAsset, setPayoutAsset] = useState("ANM");
  const [payoutAddress, setPayoutAddress] = useState("");
  const [prefilled, setPrefilled] = useState(false);

  // Prefill once from an existing mining account, and default the worker name to
  // the first registered worker so a registered machine sets up in one field.
  useEffect(() => {
    if (prefilled) return;
    const a = stats?.account;
    if (a || workers) {
      setAnmAddress((v) => v || a?.anmAddress || "");
      setXmrAddress((v) => v || a?.xmrAddress || "");
      setWorkerName((v) => v || a?.workerName || workers?.[0]?.name || "rig-01");
      setUsername((v) => v || a?.username || "");
      setPayoutAsset((v) => (a?.payoutAsset ? a.payoutAsset : v));
      setPayoutAddress((v) => v || a?.payoutAddress || "");
      setPrefilled(true);
    }
  }, [stats, workers, prefilled]);

  const save = useMutation({
    mutationFn: () => {
      // Derive a valid username (>=3 chars) so the user never has to think about it.
      const derived = (username || workerName || anmAddress.slice(0, 16) || "miner").trim();
      return api("/api/mining/account", {
        method: "POST",
        body: { mode, anmAddress: anmAddress.trim(), xmrAddress: xmrAddress.trim() || undefined, workerName: workerName.trim() || "rig-01", username: derived, payoutAsset, payoutAddress: payoutAddress.trim() || undefined },
      });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["mining-stats"] }),
  });

  const f = "w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 mt-1";
  const hasWorkers = (workers ?? []).length > 0;
  const canSave = anmAddress.trim().length > 4 && (!wantsXmr || xmrAddress.trim().length > 4);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">{title}</h1>

      <div className="card space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="font-medium text-neon-blue">Set up mining</h2>
          {hasWorkers
            ? <span className="text-xs text-neon-green">{workers!.length} registered worker{workers!.length > 1 ? "s" : ""}</span>
            : <Link href="/workers" className="text-xs text-white/50 hover:text-white">Register a worker →</Link>}
        </div>

        {/* The one field that always matters. */}
        <label className="block text-sm">
          Your Animica address (anim1…) — where mined ANM is credited
          <input className={f} value={anmAddress} onChange={(e) => setAnmAddress(e.target.value)} placeholder="anim1…" />
        </label>

        {wantsXmr && (
          <label className="block text-sm">
            Monero payout address
            <input className={f} value={xmrAddress} onChange={(e) => setXmrAddress(e.target.value)} placeholder="4… or 8…" />
          </label>
        )}

        {hasWorkers && (
          <label className="block text-sm">
            Worker
            <select className={f} value={workerName} onChange={(e) => { setWorkerName(e.target.value); if (!username) setUsername(""); }}>
              {workers!.map((w) => <option key={w.id} value={w.name}>{w.name}{w.status ? ` (${w.status})` : ""}</option>)}
            </select>
          </label>
        )}

        <button className="btn-primary" onClick={() => save.mutate()} disabled={save.isPending || !canSave}>
          {save.isPending ? "Saving…" : "Start mining this mode"}
        </button>
        {save.isError && <p className="text-sm text-red-400">{(save.error as Error).message}</p>}
        {save.isSuccess && <p className="text-sm text-neon-green">Saved — connect your miner with the command below.</p>}

        <button type="button" onClick={() => setAdv((v) => !v)} className="text-xs text-white/50 hover:text-white">
          {adv ? "Hide" : "Show"} advanced (payout asset, worker label)
        </button>
        {adv && (
          <div className="grid gap-3 sm:grid-cols-2 border-t border-white/10 pt-3">
            {!hasWorkers && <label className="text-sm">Worker name<input className={f} value={workerName} onChange={(e) => setWorkerName(e.target.value)} placeholder="rig-01" /></label>}
            <label className="text-sm">Username (defaults to worker)<input className={f} value={username} onChange={(e) => setUsername(e.target.value)} placeholder={workerName || "miner"} /></label>
            <label className="text-sm">Payout asset
              <select className={f} value={payoutAsset} onChange={(e) => setPayoutAsset(e.target.value)}>
                {ASSETS.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            </label>
            <label className="text-sm">Payout address (if not ANM)<input className={f} value={payoutAddress} onChange={(e) => setPayoutAddress(e.target.value)} /></label>
          </div>
        )}
        <p className="text-xs text-white/40">Optional ANM payout works for every mode — keep payout asset on ANM.</p>
      </div>

      {stats && (
        <>
          <div className="card">
            <h2 className="font-medium text-neon-blue">Connect your miner</h2>
            <p className="mt-1 text-sm text-white/60">Pool: <code>{stats.connection.poolUrl}</code></p>
            <pre className="mt-2 overflow-x-auto rounded-lg bg-black/40 p-3 text-sm text-neon-green">{stats.connection.command}</pre>
          </div>
          <div className="card">
            <div className="flex items-center justify-between">
              <h2 className="font-medium text-neon-blue">Live stats</h2>
              <span className="text-xs text-white/40">{stats.stats.source === "mock" ? "mock data" : "live pool"}</span>
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <Stat label="Mode" value={stats.account.mode} />
              <Stat label="Hashrate" value={`${stats.stats.hashrate.toFixed(0)} H/s`} />
              <Stat label="24h hashrate" value={`${stats.stats.hashrate24h.toFixed(0)} H/s`} />
              <Stat label="Accepted shares" value={String(stats.stats.acceptedShares)} />
              <Stat label="Rejected shares" value={String(stats.stats.rejectedShares)} />
              <Stat label="Blocks found" value={String(stats.stats.blocksFound)} />
              <Stat label="Pending (ANM)" value={stats.stats.pendingBalanceAnm.toFixed(4)} />
              <Stat label="Paid (ANM)" value={stats.stats.paidBalanceAnm.toFixed(4)} />
              <Stat label="Est. earnings" value={`$${stats.estimatedEarningsUsd.toFixed(4)}`} />
            </div>
            <p className="mt-2 text-xs text-white/40">Pool fee: {stats.poolFeePercent}% · payout asset: {stats.account.payoutAsset}</p>
          </div>
        </>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/5 bg-black/20 p-3">
      <p className="text-xs text-white/50">{label}</p>
      <p className="mt-1 font-medium">{value}</p>
    </div>
  );
}
