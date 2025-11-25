import React, { useMemo } from "react";
import { Link } from "react-router-dom";
import { cn } from "../../utils/classnames";
import { shortHash } from "../../utils/format";
import { ago } from "../../utils/time";
import { GammaPanel } from "../../components/poies/GammaPanel";
// Optional PoIES panels — rendered only if data is present.
import PoIESBreakdown from "../../components/poies/PoIESBreakdown";
import FairnessPanel from "../../components/poies/FairnessPanel";
// Global store (Zustand-style). If your app exports a different hook, adjust here.
import { useStore } from "../../state/store";

/** Local safe helpers (avoid tight coupling to slices' exact shapes). */
function getHead(store: any) {
  return store.network?.head;
}
function getLatencyMs(store: any): number | undefined {
  return store.network?.latencyMs;
}
function getPeers(store: any): number | undefined {
  return store.network?.peers;
}
function getBlocks(store: any): any[] {
  return store.blocks?.latest ?? [];
}
function getPoIES(store: any) {
  return store.poies ?? {};
}
function safeHeight(head: any): number | undefined {
  return head?.height ?? head?.number;
}
function safeHash(head: any): string | undefined {
  return head?.hash ?? head?.blockHash;
}
function safeTimestampMs(head: any): number | undefined {
  // prefer ms if present, else seconds → ms
  if (!head) return undefined;
  if (typeof head.timestamp_ms === "number") return head.timestamp_ms;
  if (typeof head.timestamp === "number") {
    return head.timestamp > 10_000_000_000 ? head.timestamp : head.timestamp * 1000;
  }
  return undefined;
}

function computeAvgBlockTimeMs(blocks: any[], maxSamples = 64): number | undefined {
  const list = blocks.slice(-maxSamples);
  if (list.length < 2) return undefined;
  const ts = list
    .map((b) =>
      typeof b.timestamp_ms === "number"
        ? b.timestamp_ms
        : (typeof b.timestamp === "number" ? (b.timestamp > 10_000_000_000 ? b.timestamp : b.timestamp * 1000) : undefined)
    )
    .filter((x) => typeof x === "number") as number[];
  if (ts.length < 2) return undefined;
  let gaps: number[] = [];
  for (let i = 1; i < ts.length; i++) gaps.push(ts[i] - ts[i - 1]);
  return gaps.reduce((a, b) => a + b, 0) / gaps.length;
}

function computeTps(blocks: any[], windowSec = 60): number | undefined {
  if (!blocks.length) return undefined;
  // Consider blocks within the last windowSec from the latest
  const nowMs = (() => {
    const tail = blocks[blocks.length - 1];
    return typeof tail?.timestamp_ms === "number"
      ? tail.timestamp_ms
      : (typeof tail?.timestamp === "number" ? (tail.timestamp > 10_000_000_000 ? tail.timestamp : tail.timestamp * 1000) : Date.now());
  })();
  const cutoff = nowMs - windowSec * 1000;
  let txSum = 0;
  let spanMs = 0;
  let firstTs: number | undefined;
  for (let i = blocks.length - 1; i >= 0; i--) {
    const b = blocks[i];
    const t = typeof b.timestamp_ms === "number"
      ? b.timestamp_ms
      : (typeof b.timestamp === "number" ? (b.timestamp > 10_000_000_000 ? b.timestamp : b.timestamp * 1000) : undefined);
    if (t === undefined) continue;
    if (t < cutoff) break;
    firstTs = t;
    const count = typeof b.txCount === "number" ? b.txCount
      : Array.isArray(b.txs) ? b.txs.length
      : (Array.isArray(b.transactions) ? b.transactions.length : 0);
    txSum += count;
  }
  if (firstTs !== undefined) {
    spanMs = Math.max(1, nowMs - firstTs);
    return txSum / (spanMs / 1000);
  }
  return undefined;
}

const StatCard: React.FC<{
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  className?: string;
}> = ({ label, value, hint, className }) => (
  <div className={cn("rounded-lg border p-4 space-y-1", className)}>
    <div className="text-xs text-muted-foreground">{label}</div>
    <div className="text-xl font-semibold tabular-nums">{value}</div>
    {hint ? <div className="text-xs text-muted-foreground">{hint}</div> : null}
  </div>
);

const SectionTitle: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <h2 className="text-base font-semibold text-foreground">{children}</h2>
);

const HomePage: React.FC = () => {
  const head = useStore(getHead);
  const peers = useStore(getPeers);
  const latencyMs = useStore(getLatencyMs);
  const blocks = useStore(getBlocks);
  const poies = useStore(getPoIES);

  const height = safeHeight(head);
  const hash = safeHash(head);
  const tsMs = safeTimestampMs(head);

  const avgBtMs = useMemo(() => computeAvgBlockTimeMs(blocks, 64), [blocks]);
  const tps = useMemo(() => computeTps(blocks, 60), [blocks]);

  // PoIES/gamma series (expecting store.poies.gammaSeries shape)
  const gammaSeries: { t: number; height: number; gamma: number }[] =
    Array.isArray(poies?.gammaSeries) ? poies.gammaSeries : [];
  const theta: number = typeof poies?.theta === "number" ? poies.theta : 1.0;

  // Optional proof mix / fairness series — render only if present.
  const mixSeries = Array.isArray(poies?.mixSeries) ? poies.mixSeries : [];
  const fairnessSeries = Array.isArray(poies?.fairnessSeries) ? poies.fairnessSeries : [];

  return (
    <div className="container mx-auto p-4 space-y-8">
      {/* Head & quick stats */}
      <div className="space-y-3">
        <SectionTitle>Network overview</SectionTitle>
        <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Head height"
            value={
              height !== undefined ? (
                <Link
                  to={`/blocks/${height}`}
                  className="hover:underline underline-offset-2"
                >
                  {height}
                </Link>
              ) : (
                "—"
              )
            }
            hint={
              tsMs
                ? `at ${new Date(tsMs).toLocaleTimeString()} (${ago(tsMs)} ago)`
                : "—"
            }
          />
          <StatCard
            label="Head hash"
            value={
              hash ? (
                <Link to={`/tx/${hash}`} className="font-mono hover:underline">
                  {shortHash(hash)}
                </Link>
              ) : (
                "—"
              )
            }
          />
          <StatCard
            label="Peers"
            value={peers ?? "—"}
            hint="connected"
          />
          <StatCard
            label="RPC latency"
            value={
              typeof latencyMs === "number"
                ? `${Math.round(latencyMs)} ms`
                : "—"
            }
          />
        </div>
      </div>

      {/* Performance */}
      <div className="space-y-3">
        <SectionTitle>Performance</SectionTitle>
        <div className="grid gap-3 grid-cols-1 sm:grid-cols-2">
          <StatCard
            label="Throughput (approx)"
            value={tps !== undefined ? `${tps.toFixed(2)} TPS` : "—"}
            hint="~last 60s window from observed blocks"
          />
          <StatCard
            label="Avg block time"
            value={
              avgBtMs !== undefined ? `${(avgBtMs / 1000).toFixed(2)} s` : "—"
            }
            hint="rolling over latest 64 blocks"
          />
        </div>
      </div>

      {/* PoIES panels */}
      <div className="space-y-4">
        <SectionTitle>PoIES</SectionTitle>
        <GammaPanel
          data={gammaSeries}
          theta={theta}
          alpha={0.12}
          height={220}
          className="border"
        />

        {/* Render additional PoIES panels only if data present */}
        {Array.isArray(mixSeries) && mixSeries.length > 0 ? (
          <div className="rounded-lg border p-4">
            <PoIESBreakdown data={mixSeries} />
          </div>
        ) : null}

        {Array.isArray(fairnessSeries) && fairnessSeries.length > 0 ? (
          <div className="rounded-lg border p-4">
            <FairnessPanel data={fairnessSeries} />
          </div>
        ) : null}
      </div>
    </div>
  );
};

export default HomePage;
