import React, { useMemo } from "react";
import { cn } from "../../utils/classnames";

/** ---------- Store access (loose coupling to slices) ---------- */
import { useStore } from "../../state/store";

function getBlocks(store: any) {
  return store.blocks?.latest ?? [];
}
function getTxs(store: any) {
  // Prefer a txs slice if present, else derive from recent blocks.
  if (Array.isArray(store.txs?.recent)) return store.txs.recent;
  const blocks = store.blocks?.latest ?? [];
  // Flatten a few most recent blocks for a lightweight approximation.
  const tail = blocks.slice(-20);
  return tail.flatMap((b: any) => (Array.isArray(b.txs) ? b.txs : b.transactions ?? []));
}
function getDA(store: any) {
  return store.da ?? {};
}
function getAICF(store: any) {
  return store.aicf ?? {};
}

/** ---------- Local helpers ---------- */
function tsMsOf(x: any): number | undefined {
  if (!x) return undefined;
  if (typeof x.timestamp_ms === "number") return x.timestamp_ms;
  if (typeof x.timestamp === "number") {
    return x.timestamp > 10_000_000_000 ? x.timestamp : x.timestamp * 1000;
  }
  return undefined;
}

function median(nums: number[]): number | undefined {
  const arr = nums.slice().sort((a, b) => a - b);
  if (!arr.length) return undefined;
  const mid = Math.floor(arr.length / 2);
  return arr.length % 2 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2;
}

function formatBytes(n?: number): string {
  if (n === undefined) return "—";
  const k = 1024;
  if (n < k) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let i = -1;
  let v = n;
  do {
    v /= k;
    i++;
  } while (v >= k && i < units.length - 1);
  return `${v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2)} ${units[i]}`;
}

function formatNumber(n?: number, opts: Intl.NumberFormatOptions = {}): string {
  if (n === undefined || Number.isNaN(n)) return "—";
  return new Intl.NumberFormat(undefined, opts).format(n);
}

/** TPS from recent blocks within windowSec. */
function computeTpsFromBlocks(blocks: any[], windowSec = 60): number | undefined {
  if (!blocks.length) return undefined;
  const nowMs = tsMsOf(blocks[blocks.length - 1]) ?? Date.now();
  const cutoff = nowMs - windowSec * 1000;
  let txSum = 0;
  let firstTs: number | undefined;
  for (let i = blocks.length - 1; i >= 0; i--) {
    const b = blocks[i];
    const t = tsMsOf(b);
    if (t === undefined) continue;
    if (t < cutoff) break;
    firstTs = t;
    const count =
      typeof b.txCount === "number"
        ? b.txCount
        : Array.isArray(b.txs)
        ? b.txs.length
        : Array.isArray(b.transactions)
        ? b.transactions.length
        : 0;
    txSum += count;
  }
  if (firstTs === undefined) return undefined;
  const span = Math.max(1, nowMs - firstTs);
  return txSum / (span / 1000);
}

/** Try to extract an absolute fee (native units) from a tx or receipt. */
function extractFee(tx: any): number | undefined {
  // Direct fee on tx/receipt
  if (typeof tx.fee === "number") return tx.fee;
  if (typeof tx.fee_native === "number") return tx.fee_native;
  if (typeof tx?.receipt?.fee === "number") return tx.receipt.fee;

  // Derive from gas * price if both are present.
  const gasUsed =
    tx.gasUsed ?? tx.gas_used ?? tx.receipt?.gasUsed ?? tx.receipt?.gas_used;
  const price =
    tx.effectiveGasPrice ??
    tx.effective_gas_price ??
    tx.gasPrice ??
    tx.gas_price ??
    tx.maxFeePerGas ??
    tx.max_fee_per_gas;

  if (typeof gasUsed === "number" && typeof price === "number") {
    return gasUsed * price;
  }
  return undefined;
}

/** Compute median fee from a window of recent txs. */
function computeMedianFeeFromTxs(txs: any[], sample = 200): number | undefined {
  const fees: number[] = [];
  for (const tx of txs.slice(-sample)) {
    const f = extractFee(tx);
    if (typeof f === "number" && Number.isFinite(f) && f >= 0) fees.push(f);
  }
  return median(fees);
}

/** Sum blob sizes within windowSec. */
function computeDaBytes(da: any, windowSec = 10 * 60): number | undefined {
  const blobs = Array.isArray(da?.blobs) ? da.blobs : [];
  if (!blobs.length) return undefined;
  const nowMs = Date.now();
  const cutoff = nowMs - windowSec * 1000;
  let sum = 0;
  for (const b of blobs) {
    const t =
      typeof b.timestamp_ms === "number"
        ? b.timestamp_ms
        : typeof b.timestamp === "number"
        ? b.timestamp > 10_000_000_000
          ? b.timestamp
          : b.timestamp * 1000
        : nowMs;
    if (t < cutoff) continue;
    const size =
      typeof b.size === "number"
        ? b.size
        : typeof b.length === "number"
        ? b.length
        : Array.isArray(b.bytes)
        ? b.bytes.length
        : 0;
    sum += size;
  }
  return sum;
}

/** Sum AICF units (AI/Quantum) within windowSec from jobs or settlements. */
function computeAicfUnits(aicf: any, windowSec = 10 * 60): number | undefined {
  const nowMs = Date.now();
  const cutoff = nowMs - windowSec * 1000;
  const lists: any[][] = [];
  if (Array.isArray(aicf?.settlements)) lists.push(aicf.settlements);
  if (Array.isArray(aicf?.jobs)) lists.push(aicf.jobs);

  let sum = 0;
  let found = false;
  for (const list of lists) {
    for (const it of list) {
      const t =
        tsMsOf(it) ??
        tsMsOf(it.job) ??
        (typeof it.settled_at_ms === "number" ? it.settled_at_ms : undefined) ??
        nowMs;
      if (t < cutoff) continue;

      let units: number | undefined =
        it.units ??
        it.job?.units ??
        it.cost_units ??
        it.cost?.units ??
        (typeof it.cost === "number" ? it.cost : undefined);
      if (typeof units === "number" && Number.isFinite(units)) {
        sum += units;
        found = true;
      }
    }
  }
  return found ? sum : undefined;
}

/** ---------- UI ---------- */
const StatCard: React.FC<{
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  className?: string;
}> = ({ label, value, hint, className }) => (
  <div className={cn("rounded-lg border p-3 space-y-1", className)}>
    <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
      {label}
    </div>
    <div className="text-lg font-semibold tabular-nums leading-tight">{value}</div>
    {hint ? <div className="text-xs text-muted-foreground">{hint}</div> : null}
  </div>
);

export type QuickStatsProps = {
  className?: string;
  /** Window for throughput calculation (seconds). Default 60s. */
  tpsWindowSec?: number;
  /** Window for DA/AICF aggregation (seconds). Default 10m. */
  aggWindowSec?: number;
  /** Optional formatter for fees (default: compact w/ 2 sig figs). */
  formatFee?: (fee?: number) => string;
};

/**
 * QuickStats — small cards showing:
 *  - Tx/s (approx from recent blocks)
 *  - Median fee (from recent tx window)
 *  - DA bytes (total within window)
 *  - AICF units (total within window)
 *
 * Heavily defensive against shape drift; safe to drop in on Home.
 */
const QuickStats: React.FC<QuickStatsProps> = ({
  className,
  tpsWindowSec = 60,
  aggWindowSec = 10 * 60,
  formatFee,
}) => {
  const blocks = useStore(getBlocks);
  const txs = useStore(getTxs);
  const da = useStore(getDA);
  const aicf = useStore(getAICF);

  const tps = useMemo(
    () => computeTpsFromBlocks(blocks, tpsWindowSec),
    [blocks, tpsWindowSec]
  );
  const medianFee = useMemo(
    () => computeMedianFeeFromTxs(txs, 200),
    [txs]
  );
  const daBytes = useMemo(
    () => computeDaBytes(da, aggWindowSec),
    [da, aggWindowSec]
  );
  const aicfUnits = useMemo(
    () => computeAicfUnits(aicf, aggWindowSec),
    [aicf, aggWindowSec]
  );

  const fmtFee =
    formatFee ??
    ((fee?: number) =>
      fee === undefined
        ? "—"
        : new Intl.NumberFormat(undefined, {
            notation: "compact",
            maximumFractionDigits: 2,
          }).format(fee));

  return (
    <div className={cn("grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-4", className)}>
      <StatCard
        label="Tx / s"
        value={tps !== undefined ? `${tps.toFixed(2)}` : "—"}
        hint={`~last ${tpsWindowSec}s from observed blocks`}
      />
      <StatCard
        label="Median fee"
        value={fmtFee(medianFee)}
        hint="~last 200 tx"
      />
      <StatCard
        label="DA bytes"
        value={daBytes !== undefined ? formatBytes(daBytes) : "—"}
        hint={`~last ${Math.round(aggWindowSec / 60)}m aggregated`}
      />
      <StatCard
        label="AICF units"
        value={aicfUnits !== undefined ? formatNumber(aicfUnits, { maximumFractionDigits: 0 }) : "—"}
        hint={`~last ${Math.round(aggWindowSec / 60)}m aggregated`}
      />
    </div>
  );
};

export default QuickStats;
