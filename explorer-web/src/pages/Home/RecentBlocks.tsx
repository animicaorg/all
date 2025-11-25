import React, { useMemo } from "react";
import { Link } from "react-router-dom";
import { cn } from "../../utils/classnames";
import { useStore } from "../../state/store";

/** --------- Local utilities (defensive against schema drift) --------- */
function tsMsOf(x: any): number | undefined {
  if (!x) return undefined;
  if (typeof x.timestamp_ms === "number") return x.timestamp_ms;
  if (typeof x.timestamp === "number") {
    return x.timestamp > 10_000_000_000 ? x.timestamp : x.timestamp * 1000;
  }
  return undefined;
}
function ageText(ts?: number): string {
  if (!ts) return "—";
  const diff = Date.now() - ts;
  const s = Math.max(0, Math.floor(diff / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}
function shortHash(h?: string, left = 6, right = 6): string {
  if (!h || typeof h !== "string") return "—";
  const clean = h.startsWith("0x") ? h.slice(2) : h;
  if (clean.length <= left + right) return `0x${clean}`;
  return `0x${clean.slice(0, left)}…${clean.slice(-right)}`;
}
function txCountOf(b: any): number {
  if (!b) return 0;
  if (typeof b.txCount === "number") return b.txCount;
  if (Array.isArray(b.txs)) return b.txs.length;
  if (Array.isArray(b.transactions)) return b.transactions.length;
  return typeof b.num_txs === "number" ? b.num_txs : 0;
}
function daBytesOf(b: any): number {
  // Try to glean DA bytes from block summary if available.
  if (!b) return 0;
  if (typeof b.da_bytes === "number") return b.da_bytes;
  if (typeof b.blob_bytes === "number") return b.blob_bytes;
  if (Array.isArray(b.blobs)) {
    return b.blobs.reduce((acc: number, it: any) => {
      const n =
        typeof it.size === "number"
          ? it.size
          : typeof it.length === "number"
          ? it.length
          : Array.isArray(it.bytes)
          ? it.bytes.length
          : 0;
      return acc + (Number.isFinite(n) ? n : 0);
    }, 0);
  }
  return 0;
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

/** --------- Store selectors --------- */
function selectLatestBlocks(store: any) {
  // Prefer normalized slice shape if present.
  if (Array.isArray(store.blocks?.latest)) return store.blocks.latest;
  if (Array.isArray(store.blocks)) return store.blocks;
  return [];
}
function selectLoading(store: any) {
  return Boolean(store.blocks?.loading);
}

export type RecentBlocksProps = {
  className?: string;
  limit?: number;
};

const RecentBlocks: React.FC<RecentBlocksProps> = ({ className, limit = 10 }) => {
  const loading = useStore(selectLoading);
  const blocksRaw = useStore(selectLatestBlocks);

  const rows = useMemo(() => {
    const list = Array.isArray(blocksRaw) ? blocksRaw : [];
    const sliced = list.slice(-limit).reverse(); // newest first
    return sliced.map((b: any) => {
      const height =
        typeof b.height === "number"
          ? b.height
          : typeof b.number === "number"
          ? b.number
          : undefined;
      const hash = typeof b.hash === "string" ? b.hash : b.block_hash ?? b.id;
      const ts = tsMsOf(b);
      const txs = txCountOf(b);
      const da = daBytesOf(b);
      const producer =
        b.producer ??
        b.miner ??
        b.proposer ??
        (typeof b.sender === "string" ? b.sender : undefined);

      return {
        height,
        hash,
        age: ageText(ts),
        txs,
        da,
        producer,
      };
    });
  }, [blocksRaw, limit]);

  return (
    <section className={cn("rounded-lg border", className)}>
      <div className="flex items-center justify-between p-3 border-b">
        <h3 className="text-sm font-semibold">Recent Blocks</h3>
        <Link
          to="/blocks"
          className="text-xs text-primary hover:underline focus:outline-none focus:ring-2 focus:ring-primary rounded px-2 py-1"
        >
          View all
        </Link>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-[11px] uppercase text-muted-foreground">
            <tr className="text-left">
              <th className="px-3 py-2">Height</th>
              <th className="px-3 py-2">Hash</th>
              <th className="px-3 py-2">Age</th>
              <th className="px-3 py-2">Txs</th>
              <th className="px-3 py-2">DA</th>
              <th className="px-3 py-2">Producer</th>
            </tr>
          </thead>
          <tbody>
            {loading && rows.length === 0 ? (
              Array.from({ length: Math.min(5, limit) }).map((_, i) => (
                <tr key={`skeleton-${i}`} className="animate-pulse">
                  <td className="px-3 py-2"><div className="h-4 w-12 bg-muted rounded" /></td>
                  <td className="px-3 py-2"><div className="h-4 w-32 bg-muted rounded" /></td>
                  <td className="px-3 py-2"><div className="h-4 w-14 bg-muted rounded" /></td>
                  <td className="px-3 py-2"><div className="h-4 w-10 bg-muted rounded" /></td>
                  <td className="px-3 py-2"><div className="h-4 w-12 bg-muted rounded" /></td>
                  <td className="px-3 py-2"><div className="h-4 w-28 bg-muted rounded" /></td>
                </tr>
              ))
            ) : rows.length > 0 ? (
              rows.map((r, idx) => (
                <tr key={`${r.hash ?? idx}`} className={cn("border-t", idx === 0 && "border-0")}>
                  <td className="px-3 py-2 tabular-nums">
                    {typeof r.height === "number" ? (
                      <Link
                        to={`/blocks/${r.height}`}
                        className="text-primary hover:underline focus:outline-none focus:ring-2 focus:ring-primary rounded px-1 -mx-1"
                      >
                        {r.height}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {r.hash ? (
                      <Link
                        to={`/blocks/${r.hash}`}
                        className="font-mono text-xs text-foreground/80 hover:text-foreground hover:underline"
                      >
                        {shortHash(r.hash)}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">{r.age}</td>
                  <td className="px-3 py-2 tabular-nums">{r.txs}</td>
                  <td className="px-3 py-2">{r.da ? formatBytes(r.da) : "—"}</td>
                  <td className="px-3 py-2">
                    {typeof r.producer === "string" ? (
                      <span title={r.producer} className="font-mono text-xs">
                        {shortHash(r.producer, 6, 4)}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td className="px-3 py-4 text-center text-muted-foreground" colSpan={6}>
                  No recent blocks observed.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
};

export default RecentBlocks;
