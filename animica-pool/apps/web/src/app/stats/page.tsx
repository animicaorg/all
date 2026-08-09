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
  // ANM price is sourced from the live NonKYC ANM/USDT feed (same-origin
  // /anm-price.json, republished every 60s by the anm-price timer).
  const { data: anm } = useQuery({ queryKey: ["anm-price"], queryFn: () => getJson<any>("/anm-price.json"), refetchInterval: 60000 });

  const blockItems: any[] = blocks?.items ?? blocks?.recent_blocks ?? [];
  // Prefer the NonKYC feed's pre-formatted display; fall back to the
  // internal revenue price only if the feed is unavailable.
  const anmDisplay: string | null = anm?.display ?? (rev?.prices?.anmUsd != null ? String(rev.prices.anmUsd) : null);
  const anmIndicative = Boolean(anm?.is_indicative);
  const anmPriceLabel = anmDisplay ? `${anmIndicative ? "~" : ""}$${anmDisplay}` : "—";

  // FORK_SERVICE_CARVE advance notice. Every figure comes from the pool API, which
  // derives them from consensus (consensus.rewards + core.network_params) — none of
  // the percentages or the activation height are restated here, because a "coming
  // soon" panel that drifts from the rule is worse than no panel.
  const carve = pool?.service_carve ?? null;
  const fmtAnm = (n: unknown) => {
    const v = Number(n ?? 0) / 1e9;
    return `${v % 1 === 0 ? v.toFixed(0) : v.toFixed(2)} ANM`;
  };
  const carveEta = (blocksLeft: number) => {
    const mins = blocksLeft; // ~60s target spacing
    if (mins < 90) return `~${mins} min`;
    if (mins < 60 * 36) return `~${Math.round(mins / 60)} h`;
    return `~${Math.round(mins / 1440)} days`;
  };

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

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Network hashrate" value={hr(Number(pool?.network_hashrate_hps ?? 0))} />
        <Stat label="Active miners" value={String(pool?.num_miners ?? pool?.miners ?? 0)} />
        <Stat label="Blocks found" value={String(pool?.blocks_found_total ?? 0)} />
        <Stat label="ANM price" value={anmPriceLabel} />
      </section>

      <p className="-mt-4 text-xs text-white/40">
        {pool?.hashrate_source === "reported"
          ? `Live miner-reported (${pool?.reporting_miners ?? 0} reporting)`
          : "Estimated from share work (no miners reporting yet)"}
      </p>

      {/* "Active miners" counts distinct addresses with PROVEN work that are here now.
          Connected miners additionally includes anyone authorized and hashing who has
          not landed a first share yet — a real category, since a miner on the full
          block target only submits when it finds a block. Sockets are shown last
          because one machine can hold hundreds of them and it is not a miner count. */}
      {pool?.connected_miners != null && (
        <p className="-mt-6 text-xs text-white/40">
          {Number(pool.connected_miners)} connected
          {Number(pool?.unproven_miners ?? 0) > 0
            ? ` (${Number(pool.unproven_miners)} awaiting a first share)`
            : ""}
          {pool?.num_connections != null
            ? ` · ${Number(pool.num_connections)} stratum connection${Number(pool.num_connections) === 1 ? "" : "s"}`
            : ""}
          . &ldquo;Active miners&rdquo; counts only addresses with recent proven work.
        </p>
      )}

      {carve && (
        <section className="card space-y-5">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-white/45">
                Inference rewards per block
              </p>
              <h2 className="mt-1 text-2xl font-semibold tracking-tight text-white">
                {carve.active ? (
                  <>Live since block {Number(carve.activation_height).toLocaleString()}</>
                ) : (
                  <>Starts at block {Number(carve.activation_height).toLocaleString()}</>
                )}
              </h2>
            </div>
            {!carve.active && (
              <span className="badge">
                {Number(carve.blocks_remaining).toLocaleString()} blocks &middot;{" "}
                {carveEta(Number(carve.blocks_remaining))}
              </span>
            )}
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <div className="rounded-xl border border-white/10 bg-black/20 p-4">
              <p className="text-xs text-white/50">Reserved for inference</p>
              <p className="mt-1 text-3xl font-semibold tracking-tight text-neon-live">
                {fmtAnm(carve.inference_per_block)}
              </p>
              <p className="mt-1 text-xs text-white/40">
                {carve.inference_pct}% of every block
              </p>
            </div>
            <div className="rounded-xl border border-white/10 bg-black/20 p-4">
              <p className="text-xs text-white/50">Miner share</p>
              <p className="mt-1 text-3xl font-semibold tracking-tight text-white">
                {fmtAnm(carve.miner_per_block)}
              </p>
              <p className="mt-1 text-xs text-white/40">
                {carve.miner_pct}% &middot; {carve.active ? "now" : "from activation"}
                {!carve.active && (
                  <>
                    {" "}
                    (today {fmtAnm(carve.miner_per_block_now)}, {carve.miner_pct_now}%)
                  </>
                )}
              </p>
            </div>
            <div className="rounded-xl border border-white/10 bg-black/20 p-4">
              <p className="text-xs text-white/50">Foundation treasury</p>
              <p className="mt-1 text-3xl font-semibold tracking-tight text-white">
                {fmtAnm(carve.treasury_per_block)}
              </p>
              <p className="mt-1 text-xs text-white/40">{carve.treasury_pct}% of every block</p>
            </div>
          </div>

          {/* The honest part. Withholding is unconditional, and nothing is claiming the
              slice yet, so a miner reading this must not conclude 25% is already
              reaching providers — nor that the cut is conditional on demand. */}
          <div className="space-y-2 text-sm text-white/60">
            <p>
              From block {Number(carve.activation_height).toLocaleString()},{" "}
              {carve.inference_pct}% of every block is reserved for inference and media work
              and is <span className="text-white">withheld from the block reward whether or
              not anyone claims it</span>. Total emission and the halving schedule do not
              change &mdash; only the division does.
            </p>
            <p>
              On a block where no inference work is claimed, the reserved{" "}
              {fmtAnm(carve.inference_per_block)} goes to the {carve.unclaimed_goes_to}, which
              then receives {Number(carve.treasury_pct) + Number(carve.inference_pct)}% of that
              block. Providers are paid out of this slice as they settle work on chain.
            </p>
            {!carve.active && (
              <p className="text-white/45">
                Nothing has changed yet: miners are paid{" "}
                {fmtAnm(carve.miner_per_block_now)} ({carve.miner_pct_now}%) per block today.
                Run <code className="rounded bg-black/40 px-1.5 py-0.5 text-white/70">animica --version</code>{" "}
                &ge; 9.5.2 before the activation height.
              </p>
            )}
          </div>
        </section>
      )}

      {anmDisplay && (
        <p className="-mt-6 text-xs text-white/40">
          ANM price: {anmIndicative ? "indicative (bid/ask mid)" : "last trade"} on{" "}
          <a
            href={anm?.market_url || "https://nonkyc.io/market/ANM_USDT"}
            target="_blank"
            rel="noopener"
            className="text-neon-blue hover:underline"
          >
            NonKYC ANM/USDT
          </a>
          {typeof anm?.change_percent === "number" && anm.change_percent !== 0 ? (
            <span className={anm.change_percent >= 0 ? "text-neon-green" : "text-red-400"}>
              {" · "}
              {anm.change_percent >= 0 ? "+" : ""}
              {anm.change_percent.toFixed(2)}% 24h
            </span>
          ) : null}
        </p>
      )}

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

function Stat({ label, value, sub }: { label: string; value: string; sub?: boolean }) {
  return (
    <div className="card">
      <p className="text-xs font-medium uppercase tracking-wide text-white/45">{label}</p>
      <p className={`mt-2 font-semibold tracking-tight ${sub ? "text-xl text-white" : "text-3xl text-neon-blue"}`}>{value}</p>
    </div>
  );
}
function Row({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl border border-white/10 bg-black/20 p-3.5"><p className="text-xs text-white/50">{label}</p><p className="mt-1 font-medium text-white">{value}</p></div>;
}
