import React, { useEffect, useMemo, useRef, useState } from "react";
import { classNames } from "../../utils/classnames";
import { shortHash } from "../../utils/format";
// Prefer project helpers if available
// eslint-disable-next-line @typescript-eslint/no-unused-vars
import * as AICFService from "../../services/aicf";

/**
 * This dashboard shows high-level AICF throughput and economics:
 * - Jobs / minute (rolling)
 * - Compute units processed
 * - Payouts to providers
 *
 * It polls on a short interval and renders small sparklines.
 * The implementation is resilient to slightly different field names
 * that upstream RPCs might use (ts/createdAt, units/computeUnits, amount/value, …).
 */

// ---- Types (loose on purpose to avoid tight coupling) ----
type AnyJob = {
  id?: string;
  status?: string;
  // timestamps (epoch ms or seconds)
  ts?: number;
  createdAt?: number;
  time?: number;
  // compute
  units?: number;
  computeUnits?: number;
  // misc
  provider?: string;
};

type AnySettlement = {
  id?: string;
  // timestamps
  ts?: number;
  createdAt?: number;
  time?: number;
  // payout amount (string, hex, or number of smallest units)
  amount?: string | number;
  value?: string | number;
  // misc
  provider?: string;
};

type Provider = {
  id?: string;
  address?: string;
  name?: string;
  stake?: string | number;
};

// ---- Config ----
const REFRESH_MS = 15_000;
const WINDOW_MINUTES = 60; // rolling window for charts
const BUCKET_MS = 60_000; // 1 minute buckets

// ---- Utility parsing/formatting ----
function getTs(x: { ts?: number; createdAt?: number; time?: number } | undefined): number | undefined {
  if (!x) return;
  const raw = x.ts ?? x.createdAt ?? x.time;
  if (raw == null) return;
  // allow both seconds and ms
  return raw > 10_000_000_000 ? raw : raw * 1000;
}

function getUnits(j: AnyJob): number {
  const v = j.units ?? j.computeUnits ?? 0;
  return typeof v === "number" ? v : Number(v ?? 0) || 0;
}

function isHex(s: any): s is string {
  return typeof s === "string" && /^0x[0-9a-fA-F]+$/.test(s);
}
function hexToNumber(s: string): number {
  try {
    return Number(BigInt(s));
  } catch {
    return 0;
  }
}
function getAmount(s: AnySettlement): number {
  const v = s.amount ?? s.value ?? 0;
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    if (isHex(v)) return hexToNumber(v);
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  }
  return 0;
}

function fmtInt(n: number): string {
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000) return (n / 1_000_000_000).toFixed(2) + "B";
  if (abs >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (abs >= 10_000) return Math.round(n / 1000) + "k";
  return String(Math.round(n));
}

function avg(arr: number[]): number {
  return arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;
}

function sum(arr: number[]): number {
  return arr.reduce((a, b) => a + b, 0);
}

// ---- Bucketing helpers ----
function makeBuckets(sinceMs: number, untilMs: number, stepMs: number): number[] {
  const arr: number[] = [];
  for (let t = sinceMs; t <= untilMs; t += stepMs) arr.push(t);
  return arr;
}

function seriesFrom<T>(
  items: T[],
  timeFn: (x: T) => number | undefined,
  valueFn: (x: T) => number,
  sinceMs: number,
  untilMs: number,
  bucketMs: number
): number[] {
  const buckets = makeBuckets(sinceMs, untilMs, bucketMs);
  const out = new Array(Math.max(buckets.length - 1, 0)).fill(0) as number[];
  for (const it of items) {
    const ts = timeFn(it);
    if (ts == null || ts < sinceMs || ts > untilMs) continue;
    const idx = Math.min(Math.floor((ts - sinceMs) / bucketMs), out.length - 1);
    if (idx >= 0) out[idx] += valueFn(it);
  }
  return out;
}

// ---- Minimal inline sparkline (avoids strict dependency on chart lib) ----
function MiniSpark({ data, width = 140, height = 40, className }: { data: number[]; width?: number; height?: number; className?: string }) {
  const max = Math.max(...data, 1);
  const stepX = data.length > 1 ? width / (data.length - 1) : width;
  const pts = data.map((v, i) => {
    const x = i * stepX;
    const y = height - (v / max) * height;
    return `${x},${y}`;
  });
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} className={classNames("spark", className)}>
      <polyline fill="none" stroke="currentColor" strokeWidth="2" points={pts.join(" ")} />
    </svg>
  );
}

// ---- Data loader ----
async function fetchProviders(): Promise<Provider[]> {
  if (typeof AICFService.listProviders === "function") {
    return (await AICFService.listProviders()) as Provider[];
  }
  return [];
}

async function fetchJobs(sinceMs: number): Promise<AnyJob[]> {
  if (typeof AICFService.listJobs === "function") {
    return (await AICFService.listJobs({ since: sinceMs })) as AnyJob[];
  }
  // Fallback: try an RPC style if someone injected a custom client
  return [];
}

async function fetchSettlements(sinceMs: number): Promise<AnySettlement[]> {
  if (typeof AICFService.listSettlements === "function") {
    return (await AICFService.listSettlements({ since: sinceMs })) as AnySettlement[];
  }
  return [];
}

// ---- Component ----
export default function AICFDashboard() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [jobs, setJobs] = useState<AnyJob[]>([]);
  const [settlements, setSettlements] = useState<AnySettlement[]>([]);
  const [err, setErr] = useState<string>();
  const [loading, setLoading] = useState<boolean>(true);
  const timer = useRef<number | undefined>(undefined);

  const now = Date.now();
  const sinceMs = now - WINDOW_MINUTES * 60 * 1000;

  async function refresh() {
    try {
      setErr(undefined);
      const [p, j, s] = await Promise.all([fetchProviders(), fetchJobs(sinceMs), fetchSettlements(sinceMs)]);
      setProviders(p || []);
      setJobs((j || []).sort((a, b) => (getTs(a)! - getTs(b)!)));
      setSettlements((s || []).sort((a, b) => (getTs(a)! - getTs(b)!)));
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    timer.current = window.setInterval(refresh, REFRESH_MS);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- Metrics ----
  const jobSeries = useMemo(
    () => seriesFrom(jobs, getTs, () => 1, sinceMs, now, BUCKET_MS),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [jobs.length]
  );

  const unitsSeries = useMemo(
    () => seriesFrom(jobs, getTs, getUnits, sinceMs, now, BUCKET_MS),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [jobs.length]
  );

  const payoutSeries = useMemo(
    () => seriesFrom(settlements, getTs, getAmount, sinceMs, now, BUCKET_MS),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [settlements.length]
  );

  const jobsPerMinNow = useMemo(() => {
    const tail = jobSeries.slice(-5); // 5-min avg
    return avg(tail);
  }, [jobSeries]);

  const jobsPerMinPrev = useMemo(() => {
    const tail = jobSeries.slice(-10, -5);
    return avg(tail);
  }, [jobSeries]);

  const unitsNow = useMemo(() => sum(unitsSeries.slice(-60)), [unitsSeries]);
  const unitsPrev = useMemo(() => sum(unitsSeries.slice(-120, -60)), [unitsSeries]);

  const payoutsNow = useMemo(() => sum(payoutSeries.slice(-60)), [payoutSeries]);
  const payoutsPrev = useMemo(() => sum(payoutSeries.slice(-120, -60)), [payoutSeries]);

  const jobsTotal = jobs.length;
  const providersOnline = providers.length;

  return (
    <section className="page">
      <header className="page-header">
        <h1>AICF Overview</h1>
        <div className="dim small">Window: last {WINDOW_MINUTES} min • Auto-refreshing</div>
      </header>

      {err ? <div className="alert warn">{err}</div> : null}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-16">
        <MetricCard
          title="Jobs / min"
          primary={jobsPerMinNow}
          previous={jobsPerMinPrev}
          series={jobSeries}
          suffix=""
          hint={`${fmtInt(jobsTotal)} jobs in window`}
        />

        <MetricCard
          title="Compute units"
          primary={unitsNow}
          previous={unitsPrev}
          series={unitsSeries}
          suffix=""
          hint="sum of units over window"
        />

        <MetricCard
          title="Payouts"
          primary={payoutsNow}
          previous={payoutsPrev}
          series={payoutSeries}
          suffix=" µ"
          hint="aggregate payouts (smallest units)"
        />

        <Card>
          <div className="card-title">Providers</div>
          <div className="metric">{fmtInt(providersOnline)}</div>
          <div className="dim small">active providers seen in window</div>
          {providersOnline ? (
            <div className="mt-2">
              <ul className="list">
                {providers.slice(0, 5).map((p, i) => (
                  <li key={p.id || p.address || i} className="mono dim small">
                    {p.name ? <strong className="normal">{p.name}</strong> : null}{" "}
                    {p.address ? <span>{shortHash(p.address, 6)}</span> : null}
                  </li>
                ))}
                {providersOnline > 5 ? (
                  <li className="dim small">+ {providersOnline - 5} more…</li>
                ) : null}
              </ul>
            </div>
          ) : null}
        </Card>
      </div>

      {loading ? <div className="mt-3 dim">Loading…</div> : null}
    </section>
  );
}

// ---- Presentational bits ----
function Card({ children }: { children: React.ReactNode }) {
  return <div className="card">{children}</div>;
}

function Delta({ now, prev }: { now: number; prev: number }) {
  if (!Number.isFinite(now) || !Number.isFinite(prev)) return null;
  const d = now - prev;
  const pct = prev === 0 ? (now > 0 ? 100 : 0) : (d / prev) * 100;
  const sign = d > 0 ? "+" : d < 0 ? "−" : "";
  const tone = d > 0 ? "pos" : d < 0 ? "neg" : "muted";
  return (
    <span className={classNames("delta", tone)}>
      {sign}
      {Math.abs(pct).toFixed(1)}%
    </span>
  );
}

function MetricCard({
  title,
  primary,
  previous,
  series,
  suffix,
  hint,
}: {
  title: string;
  primary: number;
  previous: number;
  series: number[];
  suffix?: string;
  hint?: string;
}) {
  return (
    <Card>
      <div className="card-title">{title}</div>
      <div className="metric">
        {fmtInt(primary)}
        {suffix ? <span className="dim">{suffix}</span> : null} <Delta now={primary} prev={previous} />
      </div>
      {hint ? <div className="dim small">{hint}</div> : null}
      <div className="mt-2">
        <MiniSpark data={series.slice(-60)} />
      </div>
    </Card>
  );
}

/* Lightweight styles (relies on app tokens, but safe if absent) */
declare module "*.css";
