import React, { useMemo } from "react";
import { Link } from "react-router-dom";
import { cn } from "../../utils/classnames";
import { shortHash } from "../../utils/format";
import { ago } from "../../utils/time";
import { GammaPanel } from "../../components/poies/GammaPanel";
// Optional PoIES panels — rendered only if data is present.
// Temporarily commented out due to import issues
// import PoIESBreakdown from "../../components/poies/PoIESBreakdown";
// import FairnessPanel from "../../components/poies/FairnessPanel";
// Global store (Zustand-style). If your app exports a different hook, adjust here.
import { useExplorerStore } from "../../state/store";

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
  icon?: React.ReactNode;
}> = ({ label, value, hint, className, icon }) => (
  <div className={cn("card", "p-5 space-y-2 hover:shadow-md transition-shadow duration-200", className)}>
    <div className="flex items-center justify-between">
      <div className="text-xs uppercase tracking-wide text-muted font-semibold">{label}</div>
      {icon && <div className="text-accent opacity-70">{icon}</div>}
    </div>
    <div className="text-2xl font-bold tabular-nums">{value}</div>
    {hint ? <div className="text-xs text-muted">{hint}</div> : null}
  </div>
);

const SectionTitle: React.FC<{ children: React.ReactNode; subtitle?: string }> = ({ children, subtitle }) => (
  <div className="space-y-1">
    <h2 className="text-xl font-bold tracking-tight">{children}</h2>
    {subtitle && <p className="text-sm text-muted">{subtitle}</p>}
  </div>
);

const HomePage: React.FC = () => {
  const head = useExplorerStore(getHead);
  const peers = useExplorerStore(getPeers);
  const latencyMs = useExplorerStore(getLatencyMs);
  const blocks = useExplorerStore(getBlocks);
  const poies = useExplorerStore(getPoIES);

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
    <div className="container mx-auto p-6 space-y-10">
      {/* Hero Section */}
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight">Animica Explorer</h1>
        <p className="text-lg text-muted">Real-time blockchain data and analytics</p>
      </div>

      {/* Head & quick stats */}
      <div className="space-y-4">
        <SectionTitle subtitle="Live network statistics and block information">
          Network Overview
        </SectionTitle>
        <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Head Height"
            icon={
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="7" height="7" rx="1" />
                <rect x="14" y="3" width="7" height="7" rx="1" />
                <rect x="14" y="14" width="7" height="7" rx="1" />
                <rect x="3" y="14" width="7" height="7" rx="1" />
              </svg>
            }
            value={
              height !== undefined ? (
                <Link
                  to={`/blocks/${height}`}
                  className="hover:text-accent transition-colors"
                >
                  {height.toLocaleString()}
                </Link>
              ) : (
                <span className="text-muted">—</span>
              )
            }
            hint={
              tsMs
                ? `${ago(tsMs)} ago`
                : "Waiting for data..."
            }
          />
          <StatCard
            label="Latest Hash"
            icon={
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M4 7h16M4 12h16M4 17h16" />
              </svg>
            }
            value={
              hash ? (
                <Link to={`/tx/${hash}`} className="font-mono text-sm hover:text-accent transition-colors">
                  {shortHash(hash)}
                </Link>
              ) : (
                <span className="text-muted">—</span>
              )
            }
            hint="Block hash"
          />
          <StatCard
            label="Connected Peers"
            icon={
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                <circle cx="9" cy="7" r="4" />
                <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                <path d="M16 3.13a4 4 0 0 1 0 7.75" />
              </svg>
            }
            value={peers !== undefined ? peers : <span className="text-muted">—</span>}
            hint="Network nodes"
          />
          <StatCard
            label="RPC Latency"
            icon={
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
            }
            value={
              typeof latencyMs === "number"
                ? `${Math.round(latencyMs)} ms`
                : <span className="text-muted">—</span>
            }
            hint="Response time"
          />
        </div>
      </div>

      {/* Performance */}
      <div className="space-y-4">
        <SectionTitle subtitle="Transaction throughput and block production metrics">
          Performance Metrics
        </SectionTitle>
        <div className="grid gap-4 grid-cols-1 sm:grid-cols-2">
          <StatCard
            label="Transactions Per Second"
            icon={
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
                <polyline points="17 6 23 6 23 12" />
              </svg>
            }
            value={tps !== undefined ? `${tps.toFixed(2)} TPS` : <span className="text-muted">—</span>}
            hint="Last 60 seconds"
          />
          <StatCard
            label="Average Block Time"
            icon={
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
            }
            value={
              avgBtMs !== undefined ? `${(avgBtMs / 1000).toFixed(2)}s` : <span className="text-muted">—</span>
            }
            hint="Rolling 64 blocks"
          />
        </div>
      </div>

      {/* PoIES panels */}
      <div className="space-y-4">
        <SectionTitle subtitle="Proof-of-Integrated-External-Services consensus metrics">
          PoIES Analytics
        </SectionTitle>
        <div className="card p-6">
          <GammaPanel
            data={gammaSeries}
            theta={theta}
            alpha={0.12}
            height={240}
          />
        </div>

        {/* Additional PoIES panels temporarily disabled due to import issues */}
        {/* {Array.isArray(mixSeries) && mixSeries.length > 0 ? (
          <div className="card p-6">
            <PoIESBreakdown data={mixSeries} />
          </div>
        ) : null}

        {Array.isArray(fairnessSeries) && fairnessSeries.length > 0 ? (
          <div className="card p-6">
            <FairnessPanel data={fairnessSeries} />
          </div>
        ) : null} */}
      </div>

      {/* Empty State for No Data */}
      {(!height || height === 0) && (
        <div className="card p-12 text-center">
          <div className="space-y-4">
            <div className="flex justify-center">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-muted">
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
            </div>
            <div className="space-y-2">
              <h3 className="text-lg font-semibold">No Connection to Node</h3>
              <p className="text-muted max-w-md mx-auto">
                Unable to fetch blockchain data. Please ensure the RPC node is running and accessible at the configured URL.
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default HomePage;
