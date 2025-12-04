import React, { useEffect, useMemo, useRef, useState } from "react";
import { rpcUrl as defaultRpcUrl } from "../env";

/**
 * StatusBadge — RPC health indicator (healthy / degraded / offline)
 *
 * - Pings the configured JSON-RPC endpoint by calling `chain.getHead`.
 * - Measures round-trip latency, tracks consecutive failures & staleness.
 * - Renders a small badge with color + label and optional latency tooltip.
 *
 * Usage (in Astro):
 *   ---
 *   import StatusBadge from "../components/StatusBadge.tsx";
 *   ---
 *   <StatusBadge client:load />
 */

type JsonRpcResult<T> = {
  jsonrpc: "2.0";
  id: number | string;
  result?: T;
  error?: { code: number; message: string; data?: unknown };
};

type Head = { number: number; hash: string; time?: number; timestamp?: number };

export type RpcHealth = "healthy" | "degraded" | "offline";

type Thresholds = {
  /** p99-ish RTT under this is considered healthy */
  healthyLatencyMs: number; // default 300
  /** RTT above this is considered degraded (between healthy and this = warn) */
  degradeLatencyMs: number; // default 1000
  /** mark offline after this many consecutive failures */
  offlineConsecutiveFails: number; // default 3
  /** if last successful ping is older than this, consider offline regardless of failures */
  staleMs: number; // default 20_000
};

type Props = {
  rpcUrl?: string;
  intervalMs?: number; // default 4000
  timeoutMs?: number; // default 2500
  thresholds?: Partial<Thresholds>;
  showLatency?: boolean; // default true
  className?: string;
  size?: "sm" | "md" | "lg"; // visual density only
  onStatusChange?: (s: RpcHealth) => void;
};

const DEFAULT_THRESHOLDS: Thresholds = {
  healthyLatencyMs: 300,
  degradeLatencyMs: 1000,
  offlineConsecutiveFails: 3,
  staleMs: 20_000,
};

async function rpcCall<T = unknown>(
  url: string,
  method: string,
  params?: unknown,
  timeoutMs = 2500
): Promise<{ ok: true; result: T } | { ok: false; error: Error }> {
  const id = Math.floor(Math.random() * 1e9);
  const ctrl = new AbortController();
  const to = setTimeout(() => ctrl.abort(), timeoutMs);

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id, method, params }),
      signal: ctrl.signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = (await res.json()) as JsonRpcResult<T>;
    if (body.error) {
      throw new Error(`RPC ${method} error ${body.error.code}: ${body.error.message}`);
    }
    return { ok: true, result: body.result as T };
  } catch (e: any) {
    return { ok: false, error: e instanceof Error ? e : new Error(String(e)) };
  } finally {
    clearTimeout(to);
  }
}

export default function StatusBadge({
  rpcUrl,
  intervalMs = 4000,
  timeoutMs = 2500,
  thresholds: userThresh,
  showLatency = true,
  className = "",
  size = "md",
  onStatusChange,
}: Props) {
  const URL = rpcUrl || defaultRpcUrl || "/rpc";
  const thresholds = { ...DEFAULT_THRESHOLDS, ...(userThresh || {}) };

  const [status, setStatus] = useState<RpcHealth>("offline");
  const [latencyMs, setLatencyMs] = useState<number | null>(null);
  const [lastOkAt, setLastOkAt] = useState<number | null>(null);
  const [head, setHead] = useState<number | null>(null);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  const failStreakRef = useRef(0);
  const timerRef = useRef<number | null>(null);
  const prevStatusRef = useRef<RpcHealth>(status);

  // derive status
  const derivedStatus: RpcHealth = useMemo(() => {
    const now = Date.now();
    const stale = lastOkAt != null ? now - lastOkAt > thresholds.staleMs : true;
    if (failStreakRef.current >= thresholds.offlineConsecutiveFails || stale) {
      return "offline";
    }
    if (latencyMs == null) return "degraded";
    if (latencyMs <= thresholds.healthyLatencyMs) return "healthy";
    if (latencyMs <= thresholds.degradeLatencyMs) return "degraded";
    return "degraded";
  }, [latencyMs, lastOkAt, thresholds.degradeLatencyMs, thresholds.healthyLatencyMs, thresholds.offlineConsecutiveFails, thresholds.staleMs]);

  useEffect(() => {
    if (derivedStatus !== prevStatusRef.current) {
      prevStatusRef.current = derivedStatus;
      setStatus(derivedStatus);
      onStatusChange?.(derivedStatus);
    } else {
      setStatus(derivedStatus);
    }
  }, [derivedStatus, onStatusChange]);

  useEffect(() => {
    let cancelled = false;

    async function ping() {
      const t0 = performance.now();
      const res = await rpcCall<Head>(URL, "chain.getHead", undefined, timeoutMs);
      const t1 = performance.now();
      if (cancelled) return;

      if (res.ok) {
        const rtt = Math.max(0, t1 - t0);
        setLatencyMs(rtt);
        setLastOkAt(Date.now());
        setHead(res.result?.number ?? null);
        setErrMsg(null);
        failStreakRef.current = 0;
      } else {
        setErrMsg(res.error.message || "RPC error");
        failStreakRef.current += 1;
      }
    }

    // first tick immediately
    ping();
    timerRef.current = window.setInterval(ping, intervalMs) as unknown as number;

    return () => {
      cancelled = true;
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, [URL, intervalMs, timeoutMs]);

  // visuals
  const color =
    status === "healthy" ? "bg-emerald-500" : status === "degraded" ? "bg-amber-500" : "bg-rose-500";

  const ring =
    status === "healthy"
      ? "ring-emerald-500/25"
      : status === "degraded"
      ? "ring-amber-500/25"
      : "ring-rose-500/25";

  const text =
    status === "healthy" ? "Healthy" : status === "degraded" ? "Degraded" : "Offline";

  const sizeCls =
    size === "sm"
      ? "text-xs px-2 py-1 gap-2"
      : size === "lg"
      ? "text-sm px-3 py-2 gap-3"
      : "text-sm px-2.5 py-1.5 gap-2";

  const dotSize = size === "sm" ? "size-2" : size === "lg" ? "size-2.5" : "size-2";

  const tooltip =
    (showLatency && latencyMs != null ? `RTT: ${latencyMs.toFixed(0)} ms\n` : "") +
    (head != null ? `Head: ${head}\n` : "") +
    (lastOkAt ? `Last OK: ${new Date(lastOkAt).toLocaleTimeString()}\n` : "") +
    (errMsg ? `Last error: ${errMsg}` : "");

  return (
    <div
      className={`inline-flex items-center rounded-full border bg-[var(--bg-elevated)]
        border-[color-mix(in_srgb,var(--text-primary)_/_12%,transparent)]
        ring-4 ${ring} ${sizeCls} ${className}`}
      aria-live="polite"
      title={tooltip.trim()}
      role="status"
    >
      <span className={`inline-block rounded-full ${dotSize} ${color}`} aria-hidden="true" />
      <span className="font-medium">{text}</span>
      {showLatency && (
        <span className="ml-1 tabular-nums opacity-75">
          {latencyMs != null ? `${Math.round(latencyMs)} ms` : "—"}
        </span>
      )}
    </div>
  );
}
