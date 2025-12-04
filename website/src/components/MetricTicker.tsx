import React, { useEffect, useMemo, useRef, useState } from "react";
import { rpcUrl } from "../env";

/**
 * MetricTicker
 * Live head height & approximate TPS via JSON-RPC polling.
 *
 * Usage in Astro:
 *   ---
 *   import MetricTicker from "../components/MetricTicker.tsx";
 *   ---
 *   <MetricTicker client:load />
 */

type JsonRpcResult<T> = { jsonrpc: "2.0"; id: number | string; result?: T; error?: { code: number; message: string; data?: unknown } };

type Head = {
  number: number;      // height
  hash: string;
  time?: number;       // seconds since epoch (preferred)
  timestamp?: number;  // alt field name
};

type Block = {
  number: number;
  hash: string;
  parentHash: string;
  timestamp?: number;
  time?: number;
  txs?: unknown[];        // Animica schema (if present)
  transactions?: unknown[]; // alt/compat
};

type Props = {
  pollMs?: number;         // default 2000
  windowSeconds?: number;  // TPS window (default 60s)
  className?: string;
  compact?: boolean;
};

const RPC_URL = rpcUrl ?? "/rpc";

async function rpcCall<T = unknown>(method: string, params?: unknown): Promise<T> {
  const id = Math.floor(Math.random() * 1e9);
  const res = await fetch(RPC_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id, method, params }),
  });
  if (!res.ok) {
    throw new Error(`RPC HTTP ${res.status}`);
  }
  const body = (await res.json()) as JsonRpcResult<T>;
  if (body.error) {
    throw new Error(`RPC ${method} error ${body.error.code}: ${body.error.message}`);
  }
  return body.result as T;
}

async function getHead(): Promise<Head> {
  // Animica RPC: chain.getHead
  return rpcCall<Head>("chain.getHead");
}

async function getBlockByNumber(n: number): Promise<Block | null> {
  // Try the Animica-flavored signature first; fall back to a simpler one.
  try {
    // Prefer including transactions to compute TPS
    const b = await rpcCall<Block>("chain.getBlockByNumber", [n, { includeTxs: true }]);
    return b;
  } catch {
    try {
      const b = await rpcCall<Block>("chain.getBlockByNumber", [n, true]); // some nodes accept boolean includeTxs
      return b;
    } catch {
      // Final fallback: attempt without flag
      try {
        return await rpcCall<Block>("chain.getBlockByNumber", [n]);
      } catch {
        return null;
      }
    }
  }
}

type Sample = { tMs: number; txCount: number };

export default function MetricTicker({ pollMs = 2000, windowSeconds = 60, className = "", compact = false }: Props) {
  const [height, setHeight] = useState<number | null>(null);
  const [lastHash, setLastHash] = useState<string | null>(null);
  const [status, setStatus] = useState<"ok" | "warn" | "error" | "idle">("idle");
  const [lastUpdateMs, setLastUpdateMs] = useState<number | null>(null);

  const samplesRef = useRef<Sample[]>([]);
  const lastFetchedHeightRef = useRef<number>(-1);
  const timerRef = useRef<number | null>(null);

  const windowMs = windowSeconds * 1000;

  // Compute TPS from samples within window
  const tps = useMemo(() => {
    const now = Date.now();
    const windowStart = now - windowMs;
    const samples = samplesRef.current.filter((s) => s.tMs >= windowStart);
    if (samples.length === 0) return 0;

    // Sum tx across samples in window
    const totalTx = samples.reduce((acc, s) => acc + s.txCount, 0);
    const spanMs = Math.max(1, Math.min(now, Math.max(...samples.map((s) => s.tMs))) - Math.min(...samples.map((s) => s.tMs)));
    return totalTx / (spanMs / 1000);
  }, [lastUpdateMs, windowMs]);

  const headAge = useMemo(() => {
    if (!lastUpdateMs) return null;
    const diff = Math.max(0, Date.now() - lastUpdateMs);
    return diff;
  }, [lastUpdateMs]);

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      try {
        const head = await getHead();
        if (cancelled) return;

        const h = head.number;
        setHeight(h);
        setLastHash(head.hash || null);

        const prev = lastFetchedHeightRef.current;
        // Fetch any missed blocks (if we jumped forward)
        const start = prev >= 0 ? prev + 1 : h;
        for (let n = start; n <= h; n++) {
          const block = await getBlockByNumber(n);
          if (!block) continue;
          const txs = (Array.isArray(block.txs) ? block.txs : Array.isArray(block.transactions) ? block.transactions : []) as unknown[];
          const tSec = (block.timestamp ?? block.time ?? head.timestamp ?? head.time ?? Math.floor(Date.now() / 1000)) as number;
          const tMs = (typeof tSec === "number" ? tSec * 1000 : Date.now());

          samplesRef.current.push({ tMs, txCount: txs.length });
          // Keep a modest buffer
          if (samplesRef.current.length > 512) {
            samplesRef.current.splice(0, samplesRef.current.length - 512);
          }
        }
        lastFetchedHeightRef.current = h;

        // Trim old samples to window*2
        const cutoff = Date.now() - windowMs * 2;
        samplesRef.current = samplesRef.current.filter((s) => s.tMs >= cutoff);

        setLastUpdateMs(Date.now());
        setStatus("ok");
      } catch (err) {
        console.error("[MetricTicker] poll error:", err);
        // Degrade but keep trying
        setStatus((s) => (s === "error" ? "error" : "warn"));
      }
    }

    // kick immediately
    tick();

    timerRef.current = window.setInterval(tick, pollMs) as unknown as number;

    return () => {
      cancelled = true;
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, [pollMs, windowMs]);

  // Presentation helpers
  const tpsStr = useMemo(() => {
    if (!isFinite(tps)) return "—";
    if (tps >= 1000) return `${tps.toFixed(0)}`;
    if (tps >= 100) return `${tps.toFixed(1)}`;
    return `${tps.toFixed(2)}`;
  }, [tps]);

  const ageStr = useMemo(() => {
    if (headAge == null) return "—";
    const s = Math.floor(headAge / 1000);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const rs = s % 60;
    return `${m}m ${rs}s`;
  }, [headAge]);

  const badgeCls =
    status === "ok"
      ? "bg-emerald-500"
      : status === "warn"
      ? "bg-amber-500"
      : status === "error"
      ? "bg-rose-500"
      : "bg-zinc-500";

  if (compact) {
    return (
      <div className={`inline-flex items-center gap-3 text-sm ${className}`}>
        <span className={`inline-block size-2 rounded-full ${badgeCls}`} />
        <span className="tabular-nums">Head: {height ?? "—"}</span>
        <span aria-hidden="true">•</span>
        <span className="tabular-nums">TPS: {tpsStr}</span>
        <span className="opacity-70">(age {ageStr})</span>
      </div>
    );
  }

  return (
    <div
      className={`flex items-center gap-4 rounded-lg border px-3 py-2 text-sm
        border-[color-mix(in_srgb,var(--text-primary)_/_.12,transparent)]
        bg-[var(--bg-elevated)] ${className}`}
      aria-live="polite"
    >
      <span className={`inline-block size-2 rounded-full ${badgeCls}`} aria-label={`status ${status}`} />
      <div className="flex items-baseline gap-2">
        <span className="opacity-80">Head</span>
        <span className="font-semibold tabular-nums">{height ?? "—"}</span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="opacity-80">TPS</span>
        <span className="font-semibold tabular-nums">{tpsStr}</span>
      </div>
      <div className="ml-auto flex items-baseline gap-2 opacity-75">
        <span>Age</span>
        <span className="tabular-nums">{ageStr}</span>
      </div>
    </div>
  );
}
