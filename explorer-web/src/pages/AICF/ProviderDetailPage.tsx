import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { classNames } from "../../utils/classnames";
import { shortHash } from "../../utils/format";
import { ago } from "../../utils/time";
import TimeSeriesChart from "../../components/charts/TimeSeriesChart";
import Sparkline from "../../components/charts/Sparkline";
import GaugeChart from "../../components/charts/GaugeChart";
import * as AICFService from "../../services/aicf";

// -------------------------------------------------------------------------------------
// Types (kept intentionally flexible to accommodate mock/real service variations)
// -------------------------------------------------------------------------------------
type Provider = {
  id?: string;
  address?: string;
  name?: string;
  stake?: number | string;
  meta?: Record<string, unknown>;
};

type Job = {
  id?: string;
  providerId?: string;
  submittedAt?: number; // epoch ms
  startedAt?: number;
  finishedAt?: number;
  durationMs?: number;
  model?: string;
  kind?: "AI" | "Quantum" | string;
  status?: "success" | "failed" | "running" | "queued" | string;
  units?: number;
  cost?: number | string;
};

type SLAPoint = {
  t: number; // epoch ms
  successRate?: number; // 0..1
  p50?: number; // ms
  p95?: number; // ms
  jobsPerMin?: number;
};

type SLAResponse = {
  series?: SLAPoint[];
  // optional pre-aggregates
  avgLatencyMs?: number;
  p95LatencyMs?: number;
  successRate?: number; // 0..1
  window?: { from: number; to: number };
};

const REFRESH_MS_DEFAULT = 30_000;

const RANGES = [
  { id: "1h", label: "1h", ms: 60 * 60 * 1000 },
  { id: "24h", label: "24h", ms: 24 * 60 * 60 * 1000 },
  { id: "7d", label: "7d", ms: 7 * 24 * 60 * 60 * 1000 },
] as const;

function isHex(x: unknown): x is string {
  return typeof x === "string" && /^0x[0-9a-fA-F]+$/.test(x);
}
function toNumber(x: unknown): number {
  if (typeof x === "number") return x;
  if (typeof x === "string") {
    if (isHex(x)) {
      try {
        return Number(BigInt(x));
      } catch {
        return 0;
      }
    }
    const n = Number(x);
    return Number.isFinite(n) ? n : 0;
  }
  return 0;
}
function fmtPct01(x?: number): string {
  if (typeof x !== "number" || !Number.isFinite(x)) return "—";
  return (x * 100).toFixed(2) + "%";
}
function fmtMs(ms?: number): string {
  if (!Number.isFinite(ms || NaN)) return "—";
  const n = ms as number;
  if (n < 1_000) return `${Math.round(n)} ms`;
  if (n < 60_000) return `${(n / 1000).toFixed(2)} s`;
  return `${(n / 60_000).toFixed(2)} min`;
}
function fmtInt(n?: number): string {
  if (!Number.isFinite(n || NaN)) return "—";
  const v = n as number;
  if (Math.abs(v) >= 1_000_000_000) return (v / 1_000_000_000).toFixed(2) + "B";
  if (Math.abs(v) >= 1_000_000) return (v / 1_000_000).toFixed(2) + "M";
  if (Math.abs(v) >= 10_000) return Math.round(v / 1_000) + "k";
  return String(Math.round(v));
}
function calcDurationMs(j: Job): number {
  if (Number.isFinite(j.durationMs)) return j.durationMs as number;
  if (j.startedAt && j.finishedAt) return Math.max(0, (j.finishedAt as number) - (j.startedAt as number));
  if (j.submittedAt && j.finishedAt) return Math.max(0, (j.finishedAt as number) - (j.submittedAt as number));
  return 0;
}

// -------------------------------------------------------------------------------------
// Page Component
// -------------------------------------------------------------------------------------
export default function ProviderDetailPage() {
  const params = useParams();
  const [sp, setSp] = useSearchParams();
  const providerIdParam = params.id || sp.get("id") || sp.get("address") || "";

  const [provider, setProvider] = useState<Provider | null>(null);
  const [sla, setSla] = useState<SLAResponse | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string>();
  const [range, setRange] = useState<(typeof RANGES)[number]>(() => {
    const rid = sp.get("range");
    const found = RANGES.find((r) => r.id === rid);
    return found || RANGES[1]; // default 24h
  });

  const [page, setPage] = useState(0);
  const pageSize = 50;

  const timer = useRef<number | undefined>(undefined);

  const refresh = useCallback(async () => {
    if (!providerIdParam) return;
    setErr(undefined);
    try {
      setLoading(true);
      const now = Date.now();
      const from = now - range.ms;

      // Flexible service calls (real/mocks may differ)
      const [p, slaResp, jobList] = await Promise.all([
        (AICFService as any).getProvider?.(providerIdParam) ??
          (AICFService as any).findProvider?.(providerIdParam) ??
          (AICFService as any).listProviders?.().then((arr: Provider[] = []) =>
            arr.find((x) => x.id === providerIdParam || x.address === providerIdParam || x.name === providerIdParam)
          ),
        (AICFService as any).getProviderSLA?.(providerIdParam, { from, to: now }) ??
          (AICFService as any).getSLA?.({ providerId: providerIdParam, from, to: now }),
        (AICFService as any).listJobs?.({
          providerId: providerIdParam,
          from,
          to: now,
          limit: pageSize,
          offset: page * pageSize,
        }) ??
          (AICFService as any).getJobs?.(providerIdParam, { from, to: now, limit: pageSize, offset: page * pageSize }),
      ]);

      setProvider(p ?? null);
      setSla(normalizeSLA(slaResp));
      setJobs(Array.isArray(jobList) ? jobList.map(normalizeJob) : []);
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, providerIdParam, range.ms]);

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerIdParam, range, page]);

  useEffect(() => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(refresh, REFRESH_MS_DEFAULT);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [refresh]);

  // push range to URL (for shareable links)
  useEffect(() => {
    setSp((prev) => {
      prev.set("range", range.id);
      return prev;
    });
  }, [range, setSp]);

  const series = useMemo(() => {
    const arr = sla?.series || [];
    return {
      successRate: arr.map((p) => ({ x: p.t, y: clamp01(p.successRate ?? 0) })),
      p50: arr.map((p) => ({ x: p.t, y: safeNum(p.p50) })),
      p95: arr.map((p) => ({ x: p.t, y: safeNum(p.p95) })),
      jobsPerMin: arr.map((p) => ({ x: p.t, y: safeNum(p.jobsPerMin) })),
    };
  }, [sla]);

  const aggregates = useMemo(() => {
    const sr = typeof sla?.successRate === "number" ? sla!.successRate! : avg(series.successRate.map((p) => p.y)) / 100;
    const p50 =
      Number.isFinite(sla?.avgLatencyMs || NaN) ? sla!.avgLatencyMs! : avg(series.p50.map((p) => p.y));
    const p95 =
      Number.isFinite(sla?.p95LatencyMs || NaN) ? sla!.p95LatencyMs! : avg(series.p95.map((p) => p.y));
    const jpm = avg(series.jobsPerMin.map((p) => p.y));
    const ok = jobs.filter((j) => (j.status || "").toLowerCase() === "success").length;
    const failed = jobs.filter((j) => (j.status || "").toLowerCase() === "failed").length;
    return { sr, p50, p95, jpm, ok, failed };
  }, [jobs, series.jobsPerMin, series.p50, series.p95, series.successRate, sla]);

  return (
    <section className="page">
      <header className="page-header">
        <div className="row wrap gap-12 items-end">
          <h1>
            Provider:{" "}
            <span className="mono">
              {provider?.name || shortHash(provider?.address || provider?.id || providerIdParam, 8)}
            </span>
          </h1>
          <div className="row gap-8">
            <RangeSelector value={range.id} onChange={(id) => setRange(RANGES.find((r) => r.id === id) || RANGES[1])} />
            <button className="btn small" onClick={() => refresh()} disabled={loading}>
              Refresh
            </button>
          </div>
        </div>
        {provider?.address ? (
          <div className="dim small mono">Address: {shortHash(provider.address, 10)}</div>
        ) : null}
        {err ? <div className="alert warn mt-2">{err}</div> : null}
      </header>

      {/* KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-12 mb-16">
        <StatCard title="Success Rate">
          <div className="row gap-8 items-center">
            <GaugeChart value={(aggregates.sr || 0) * 100} min={0} max={100} />
            <div className="col">
              <div className="big">{fmtPct01(aggregates.sr)}</div>
              <div className="dim small">over selected window</div>
            </div>
          </div>
        </StatCard>
        <StatCard title="Latency p50">
          <div className="big">{fmtMs(aggregates.p50)}</div>
          <div className="dim small">median</div>
        </StatCard>
        <StatCard title="Latency p95">
          <div className="big">{fmtMs(aggregates.p95)}</div>
          <div className="dim small">tail</div>
        </StatCard>
        <StatCard title="Jobs / min">
          <div className="big">{fmtInt(aggregates.jpm)}</div>
          <div className="dim small">avg</div>
        </StatCard>
        <StatCard title="Succeeded">
          <div className="big">{fmtInt(aggregates.ok)}</div>
          <div className="dim small">in page</div>
        </StatCard>
        <StatCard title="Failed">
          <div className="big">{fmtInt(aggregates.failed)}</div>
          <div className="dim small">in page</div>
        </StatCard>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-20">
        <div className="card">
          <div className="card-title">SLA — Success Rate</div>
          <div className="dim small mb-2">
            Share of completed jobs that were successful (0–100%). Higher is better.
          </div>
          <TimeSeriesChart
            // @ts-ignore - chart accepts generic series
            series={[
              { id: "success", name: "Success %", data: series.successRate.map((p) => ({ x: p.x, y: p.y })) },
            ]}
            yFormatter={(y: number) => y.toFixed(0) + "%"}
            yDomain={[0, 100]}
          />
        </div>

        <div className="card">
          <div className="card-title">Latency — p50 / p95</div>
          <div className="dim small mb-2">Milliseconds to complete jobs.</div>
          <TimeSeriesChart
            // @ts-ignore
            series={[
              { id: "p50", name: "p50", data: series.p50 },
              { id: "p95", name: "p95", data: series.p95 },
            ]}
            yFormatter={(y: number) => fmtMs(y)}
          />
        </div>

        <div className="card">
          <div className="card-title">Jobs Per Minute</div>
          <TimeSeriesChart
            // @ts-ignore
            series={[{ id: "jpm", name: "Jobs/min", data: series.jobsPerMin }]}
            yFormatter={(y: number) => fmtInt(y)}
          />
        </div>

        <div className="card">
          <div className="card-title">Recent Trend</div>
          <div className="row gap-20 items-end">
            <div className="col">
              <div className="dim small mb-1">Success %</div>
              <Sparkline
                // @ts-ignore
                data={series.successRate.slice(-60)}
                yFormatter={(y: number) => y.toFixed(1) + "%"}
              />
            </div>
            <div className="col">
              <div className="dim small mb-1">Latency p50</div>
              <Sparkline
                // @ts-ignore
                data={series.p50.slice(-60)}
                yFormatter={(y: number) => fmtMs(y)}
              />
            </div>
            <div className="col">
              <div className="dim small mb-1">Jobs/min</div>
              <Sparkline
                // @ts-ignore
                data={series.jobsPerMin.slice(-60)}
                yFormatter={(y: number) => fmtInt(y)}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Jobs table */}
      <section className="mt-20">
        <div className="row spread items-center mb-2">
          <div className="card-title">Jobs ({jobs.length})</div>
          <div className="row gap-8">
            <button className="btn small" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}>
              Prev
            </button>
            <span className="dim small">Page {page + 1}</span>
            <button className="btn small" onClick={() => setPage((p) => p + 1)} disabled={jobs.length < pageSize}>
              Next
            </button>
          </div>
        </div>

        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Kind</th>
                <th>Model</th>
                <th>Submitted</th>
                <th className="right">Duration</th>
                <th>Status</th>
                <th className="right">Units</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => {
                const dur = calcDurationMs(j);
                const status = (j.status || "").toLowerCase();
                return (
                  <tr key={j.id}>
                    <td className="mono">{shortHash(j.id || "", 10)}</td>
                    <td>{j.kind || "—"}</td>
                    <td className="mono">{j.model || "—"}</td>
                    <td title={j.submittedAt ? new Date(j.submittedAt).toISOString() : undefined}>
                      {j.submittedAt ? ago(j.submittedAt) : "—"}
                    </td>
                    <td className="right">{fmtMs(dur)}</td>
                    <td>
                      <StatusBadge status={status as any} />
                    </td>
                    <td className="right">{Number.isFinite(j.units || NaN) ? fmtInt(j.units) : "—"}</td>
                  </tr>
                );
              })}
              {!jobs.length && !loading ? (
                <tr>
                  <td className="center dim" colSpan={7}>
                    No jobs found for this window.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>

        {loading ? <div className="mt-2 dim">Loading…</div> : null}
      </section>
    </section>
  );
}

// -------------------------------------------------------------------------------------
// UI bits
// -------------------------------------------------------------------------------------
function StatCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card">
      <div className="card-title">{title}</div>
      <div>{children}</div>
    </div>
  );
}

function StatusBadge({ status }: { status: "success" | "failed" | "running" | "queued" | string }) {
  const s = (status || "").toLowerCase();
  const tone =
    s === "success" ? "ok" : s === "failed" ? "bad" : s === "running" ? "warn" : s === "queued" ? "muted" : "muted";
  return <span className={classNames("tag", tone)}>{status || "—"}</span>;
}

function RangeSelector({
  value,
  onChange,
}: {
  value: (typeof RANGES)[number]["id"];
  onChange: (val: (typeof RANGES)[number]["id"]) => void;
}) {
  return (
    <div className="row gap-6">
      {RANGES.map((r) => (
        <button
          key={r.id}
          className={classNames("btn", "tiny", value === r.id && "primary")}
          onClick={() => onChange(r.id)}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

// -------------------------------------------------------------------------------------
// Normalizers & helpers
// -------------------------------------------------------------------------------------
function normalizeSLA(resp: any): SLAResponse | null {
  if (!resp) return null;
  const series: SLAPoint[] = Array.isArray(resp.series)
    ? resp.series.map((p: any) => ({
        t: toNumber(p.t) || Date.now(),
        successRate: typeof p.successRate === "number" ? p.successRate : toNumber(p.successRate) / 100 || undefined,
        p50: safeNum(p.p50),
        p95: safeNum(p.p95),
        jobsPerMin: safeNum(p.jobsPerMin),
      }))
    : [];
  const window = resp.window
    ? { from: toNumber(resp.window.from), to: toNumber(resp.window.to) }
    : undefined;
  const sr =
    typeof resp.successRate === "number"
      ? resp.successRate
      : Number.isFinite(resp.successRate)
      ? Number(resp.successRate)
      : undefined;
  return {
    series,
    avgLatencyMs: safeNum(resp.avgLatencyMs),
    p95LatencyMs: safeNum(resp.p95LatencyMs),
    successRate: sr,
    window,
  };
}

function normalizeJob(j: any): Job {
  return {
    id: j.id ?? j.jobId ?? j.hash ?? "",
    providerId: j.providerId ?? j.provider ?? j.address,
    submittedAt: toNumber(j.submittedAt ?? j.createdAt ?? j.timeSubmitted),
    startedAt: toNumber(j.startedAt ?? j.timeStarted),
    finishedAt: toNumber(j.finishedAt ?? j.timeFinished ?? j.completedAt),
    durationMs: safeNum(j.durationMs),
    model: j.model ?? j.program ?? j.circuitName ?? "",
    kind: j.kind ?? j.type ?? "AI",
    status: j.status ?? j.state ?? "success",
    units: Number.isFinite(j.units) ? Number(j.units) : undefined,
    cost: j.cost,
  };
}

function clamp01(x: number): number {
  if (!Number.isFinite(x)) return 0;
  return Math.max(0, Math.min(100, x * 100));
}
function safeNum(x: any): number | undefined {
  const n = toNumber(x);
  return Number.isFinite(n) ? n : undefined;
}
function avg(arr: number[]): number {
  const xs = arr.filter((n) => Number.isFinite(n));
  if (!xs.length) return 0;
  return xs.reduce((a, b) => a + b, 0) / xs.length;
}
