"use client";

// Public miner-stats lookup: enter an anim1… payout address OR a worker/miner
// name to see that miner's pool stats (hashrate, shares, blocks, credits),
// aggregated across all of the address's workers. Backed by the pool engine's
// GET /api/miners/<identity> (nginx routes /api/miners → :8550), which resolves
// either an address or a worker name. Public, no sign-in.

import { useCallback, useEffect, useState } from "react";

type Ts = { time?: number; bucket?: number; hps?: number; hashrate?: number };
type MinerStats = {
  address: string;
  worker_name?: string;
  hashrate_timeseries?: Ts[];
  last_share?: { time?: number | null; difficulty?: number | null; status?: string | null };
  shares_accepted?: number;
  shares_rejected?: number;
  blocks_found?: number;
  pool_mode?: string;
  credit_total?: string;
  credit_pps?: string;
  credit_solo?: string;
  connected_since?: number | null;
};

function fmtHashrate(hps: number): string {
  if (!hps || hps <= 0) return "0 H/s";
  const u = ["H/s", "KH/s", "MH/s", "GH/s", "TH/s"];
  let i = 0, v = hps;
  while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; }
  return `${v.toFixed(2)} ${u[i]}`;
}
function fmtAnm(nano?: string): string {
  const n = Number(nano || "0");
  if (!Number.isFinite(n) || n <= 0) return "0";
  return (n / 1e9).toLocaleString(undefined, { maximumFractionDigits: 4 });
}
function fmtTime(ts?: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toISOString().replace("T", " ").replace(".000Z", " UTC");
}
function latestHps(s: MinerStats): number {
  const ts = s.hashrate_timeseries || [];
  if (!ts.length) return 0;
  const last = ts[ts.length - 1];
  return Number(last?.hps ?? last?.hashrate ?? 0);
}

export default function MinerLookupPage() {
  const [q, setQ] = useState("");
  const [stats, setStats] = useState<MinerStats | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const search = useCallback(async (identity: string) => {
    const id = identity.trim();
    if (!id) return;
    setLoading(true); setErr(null); setStats(null);
    try {
      const res = await fetch(`/api/miners/${encodeURIComponent(id)}`, { cache: "no-store" });
      if (res.status === 404) {
        setErr(`No pool stats found for “${id}”. Double-check the address/worker name — a miner only appears after it submits its first share.`);
        return;
      }
      if (!res.ok) { setErr("The pool is unavailable right now — try again shortly."); return; }
      const data = (await res.json()) as MinerStats;
      if (!data || !data.address) { setErr(`No pool stats found for “${id}”.`); return; }
      setStats(data);
      const url = new URL(window.location.href);
      url.searchParams.set("q", id);
      window.history.replaceState(null, "", url.toString());
    } catch {
      setErr("Network error — try again.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const pre = new URLSearchParams(window.location.search).get("q");
    if (pre) { setQ(pre); search(pre); }
  }, [search]);

  const accepted = stats?.shares_accepted ?? 0;
  const rejected = stats?.shares_rejected ?? 0;
  const acceptPct = accepted + rejected > 0 ? (accepted / (accepted + rejected)) * 100 : 0;

  return (
    <div className="mx-auto max-w-3xl">
      <p className="text-xs font-semibold uppercase tracking-wider text-neon-green/80">Miner stats</p>
      <h1 className="mt-1 text-3xl font-bold">Look up a miner</h1>
      <p className="mt-2 max-w-2xl text-white/60">
        Enter your <span className="font-mono text-neon-green">anim1…</span> payout address or your
        worker name to see live pool stats — hashrate, shares, blocks, and credited ANM — across all
        of that address&apos;s workers. Public, no sign-in.
      </p>

      <form onSubmit={(e) => { e.preventDefault(); search(q); }} className="mt-6 flex gap-2">
        <input
          className="field flex-1"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="anim1…  or  worker name (e.g. rig-01)"
          autoFocus
        />
        <button className="btn-primary" type="submit" disabled={loading || !q.trim()}>
          {loading ? "Looking up…" : "Look up"}
        </button>
      </form>

      {err && (
        <div className="mt-5 rounded-xl border border-red-400/30 bg-red-500/10 p-4 text-sm text-red-200">
          {err}
        </div>
      )}

      {stats && (
        <div className="card mt-6">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-xl font-bold">{stats.worker_name || "miner"}</h2>
            <span className="rounded-full border border-neon-blue/40 bg-neon-blue/10 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-neon-blue">
              {stats.pool_mode || "pps"}
            </span>
          </div>
          <p className="mt-1 break-all font-mono text-xs text-white/50">{stats.address}</p>

          <div className="mt-5 grid grid-cols-2 gap-5 sm:grid-cols-3">
            <Stat label="Hashrate" value={fmtHashrate(latestHps(stats))} />
            <Stat label="Shares (acc / rej)" value={`${accepted.toLocaleString()} / ${rejected.toLocaleString()}`} sub={`${acceptPct.toFixed(1)}% accepted`} />
            <Stat label="Blocks found" value={String(stats.blocks_found ?? 0)} />
            <Stat label="Credited ANM" value={fmtAnm(stats.credit_total)} sub={`pps ${fmtAnm(stats.credit_pps)} · solo ${fmtAnm(stats.credit_solo)}`} />
            <Stat label="Last share" value={fmtTime(stats.last_share?.time ?? null)} sub={stats.last_share?.status || ""} />
            <Stat label="Connected since" value={fmtTime(stats.connected_since ?? null)} />
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-wide text-white/40">{label}</p>
      <p className="mt-1 break-words text-lg font-bold">{value}</p>
      {sub ? <p className="mt-0.5 text-xs text-white/45">{sub}</p> : null}
    </div>
  );
}
