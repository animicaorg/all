import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { shortHash } from "../../utils/format";
import { ago } from "../../utils/time";
import { classNames } from "../../utils/classnames";
import * as AICFService from "../../services/aicf";

// -------------------------------------------------------------------------------------
// Types
// -------------------------------------------------------------------------------------
type Job = {
  id?: string;
  providerId?: string;
  providerName?: string;
  submittedAt?: number;
  startedAt?: number;
  finishedAt?: number;
  durationMs?: number;
  model?: string;
  kind?: "AI" | "Quantum" | string;
  status?: "success" | "failed" | "running" | "queued" | string;
  units?: number;
};

type JobsQuery = {
  status?: string | string[];
  providerId?: string;
  kind?: string;
  search?: string;
  from?: number;
  to?: number;
  limit?: number;
  offset?: number;
};

const REFRESH_MS_DEFAULT = 20_000;

const RANGES = [
  { id: "1h", label: "1h", ms: 60 * 60 * 1000 },
  { id: "24h", label: "24h", ms: 24 * 60 * 60 * 1000 },
  { id: "7d", label: "7d", ms: 7 * 24 * 60 * 60 * 1000 },
] as const;

const TABS = [
  { id: "queued", statuses: ["queued"], label: "Queued" },
  { id: "running", statuses: ["running"], label: "Running" },
  { id: "completed", statuses: ["success", "failed"], label: "Completed" },
] as const;

// -------------------------------------------------------------------------------------
// Helpers
// -------------------------------------------------------------------------------------
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
function safeNum(x: any): number | undefined {
  const n = toNumber(x);
  return Number.isFinite(n) ? n : undefined;
}
function calcDurationMs(j: Job): number {
  if (Number.isFinite(j.durationMs)) return j.durationMs as number;
  if (j.startedAt && j.finishedAt) return Math.max(0, (j.finishedAt as number) - (j.startedAt as number));
  if (j.submittedAt && j.finishedAt) return Math.max(0, (j.finishedAt as number) - (j.submittedAt as number));
  return 0;
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
function normalizeJob(j: any): Job {
  return {
    id: j.id ?? j.jobId ?? j.hash ?? "",
    providerId: j.providerId ?? j.provider ?? j.address,
    providerName: j.providerName ?? j.provider_label ?? undefined,
    submittedAt: toNumber(j.submittedAt ?? j.createdAt ?? j.timeSubmitted),
    startedAt: toNumber(j.startedAt ?? j.timeStarted),
    finishedAt: toNumber(j.finishedAt ?? j.timeFinished ?? j.completedAt),
    durationMs: safeNum(j.durationMs),
    model: j.model ?? j.program ?? j.circuitName ?? "",
    kind: j.kind ?? j.type ?? "AI",
    status: (j.status ?? j.state ?? "success").toLowerCase(),
    units: Number.isFinite(j.units) ? Number(j.units) : undefined,
  };
}

// -------------------------------------------------------------------------------------
// Page
// -------------------------------------------------------------------------------------
export default function JobsPage() {
  const [sp, setSp] = useSearchParams();

  const initialTab = (sp.get("tab") as (typeof TABS)[number]["id"]) || "queued";
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>(initialTab);

  const initialRange =
    RANGES.find((r) => r.id === sp.get("range")) || RANGES[0]; // default 1h for job views
  const [range, setRange] = useState<(typeof RANGES)[number]>(initialRange);

  const [kind, setKind] = useState<string>(sp.get("kind") || "all");
  const [provider, setProvider] = useState<string>(sp.get("provider") || "");
  const [search, setSearch] = useState<string>(sp.get("q") || "");
  const [completedFilter, setCompletedFilter] = useState<"all" | "success" | "failed">(
    (sp.get("completed") as any) || "all"
  );

  const [page, setPage] = useState(0);
  const pageSize = 50;

  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string>();

  const [counts, setCounts] = useState<{ queued: number; running: number; success: number; failed: number }>({
    queued: 0,
    running: 0,
    success: 0,
    failed: 0,
  });

  const timer = useRef<number | undefined>(undefined);

  const statusesForTab = useMemo(() => {
    const t = TABS.find((t) => t.id === tab)!;
    if (tab === "completed" && completedFilter !== "all") return [completedFilter];
    return t.statuses;
  }, [completedFilter, tab]);

  // Push filters to URL (shareable links)
  useEffect(() => {
    setSp((prev) => {
      prev.set("tab", tab);
      prev.set("range", range.id);
      if (kind && kind !== "all") prev.set("kind", kind);
      else prev.delete("kind");
      if (provider) prev.set("provider", provider);
      else prev.delete("provider");
      if (search) prev.set("q", search);
      else prev.delete("q");
      if (tab === "completed") prev.set("completed", completedFilter);
      else prev.delete("completed");
      return prev;
    });
  }, [completedFilter, kind, provider, range.id, search, setSp, tab]);

  const refresh = useCallback(async () => {
    setErr(undefined);
    setLoading(true);
    try {
      const now = Date.now();
      const from = now - range.ms;

      const q: JobsQuery = {
        status: statusesForTab,
        providerId: provider || undefined,
        kind: kind !== "all" ? kind : undefined,
        search: search || undefined,
        from,
        to: now,
        limit: pageSize,
        offset: page * pageSize,
      };

      // Flexible adapters: prefer listJobs(options), fallback to getJobs(provider, options)
      const list =
        (await (AICFService as any).listJobs?.(q)) ??
        (provider
          ? await (AICFService as any).getJobs?.(provider, q)
          : await (AICFService as any).getJobs?.(undefined, q)) ??
        [];

      const rows: Job[] = Array.isArray(list) ? list.map(normalizeJob) : [];

      // Derive lightweight counts (best-effort)
      const c = rows.reduce(
        (acc, j) => {
          const s = (j.status || "").toLowerCase();
          if (s === "queued") acc.queued++;
          else if (s === "running") acc.running++;
          else if (s === "success") acc.success++;
          else if (s === "failed") acc.failed++;
          return acc;
        },
        { queued: 0, running: 0, success: 0, failed: 0 }
      );

      setCounts(c);
      setJobs(rows);
    } catch (e: any) {
      setErr(e?.message || String(e));
      setJobs([]);
    } finally {
      setLoading(false);
    }
  }, [kind, page, pageSize, provider, range.ms, search, statusesForTab]);

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, range, kind, provider, search, page, completedFilter]);

  useEffect(() => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(refresh, REFRESH_MS_DEFAULT);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [refresh]);

  const totalInPage = jobs.length;

  return (
    <section className="page">
      <header className="page-header">
        <div className="row wrap gap-12 items-end">
          <h1>AICF Jobs</h1>
          <div className="row gap-8">
            <Tab id="queued" active={tab === "queued"} count={counts.queued} onClick={() => setTab("queued")}>
              Queued
            </Tab>
            <Tab id="running" active={tab === "running"} count={counts.running} onClick={() => setTab("running")}>
              Running
            </Tab>
            <Tab
              id="completed"
              active={tab === "completed"}
              count={counts.success + counts.failed}
              onClick={() => setTab("completed")}
            >
              Completed
            </Tab>
          </div>
        </div>

        <div className="row wrap gap-10 mt-3">
          <RangeSelector value={range.id} onChange={(id) => setRange(RANGES.find((r) => r.id === id) || RANGES[0])} />

          <div className="row gap-6">
            <select
              className="input"
              value={kind}
              onChange={(e) => {
                setPage(0);
                setKind(e.target.value);
              }}
              title="Kind"
            >
              <option value="all">All kinds</option>
              <option value="AI">AI</option>
              <option value="Quantum">Quantum</option>
            </select>

            {tab === "completed" ? (
              <select
                className="input"
                value={completedFilter}
                onChange={(e) => {
                  setPage(0);
                  setCompletedFilter(e.target.value as any);
                }}
                title="Status"
              >
                <option value="all">All results</option>
                <option value="success">Success only</option>
                <option value="failed">Failed only</option>
              </select>
            ) : null}
          </div>

          <input
            className="input"
            placeholder="Filter by provider (addr or name)"
            value={provider}
            onChange={(e) => {
              setPage(0);
              setProvider(e.target.value);
            }}
            spellCheck={false}
          />
          <input
            className="input"
            placeholder="Search id / model"
            value={search}
            onChange={(e) => {
              setPage(0);
              setSearch(e.target.value);
            }}
            spellCheck={false}
          />

          <button className="btn" onClick={() => refresh()} disabled={loading}>
            Refresh
          </button>
        </div>

        {err ? <div className="alert warn mt-2">{err}</div> : null}
      </header>

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Provider</th>
              <th>Kind</th>
              <th>Model</th>
              <th>Submitted</th>
              <th>Started</th>
              <th>Finished</th>
              <th className="right">Duration</th>
              <th>Status</th>
              <th className="right">Units</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => {
              const dur = calcDurationMs(j);
              const prov = j.providerName || j.providerId || "";
              return (
                <tr key={j.id}>
                  <td className="mono">{shortHash(j.id || "", 10)}</td>
                  <td className="mono" title={j.providerId || undefined}>
                    {prov ? shortHash(prov, 8) : "—"}
                  </td>
                  <td>{j.kind || "—"}</td>
                  <td className="mono">{j.model || "—"}</td>
                  <td title={j.submittedAt ? new Date(j.submittedAt).toISOString() : undefined}>
                    {j.submittedAt ? ago(j.submittedAt) : "—"}
                  </td>
                  <td title={j.startedAt ? new Date(j.startedAt).toISOString() : undefined}>
                    {j.startedAt ? ago(j.startedAt) : "—"}
                  </td>
                  <td title={j.finishedAt ? new Date(j.finishedAt).toISOString() : undefined}>
                    {j.finishedAt ? ago(j.finishedAt) : "—"}
                  </td>
                  <td className="right">{fmtMs(dur)}</td>
                  <td>
                    <StatusBadge status={(j.status || "").toLowerCase()} />
                  </td>
                  <td className="right">{Number.isFinite(j.units || NaN) ? fmtInt(j.units) : "—"}</td>
                </tr>
              );
            })}
            {!jobs.length && !loading ? (
              <tr>
                <td className="center dim" colSpan={10}>
                  No jobs found for the selected filters.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <div className="row gap-8 items-center mt-3">
        <button className="btn small" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}>
          Prev
        </button>
        <span className="dim small">
          Page {page + 1} • showing {totalInPage}
        </span>
        <button className="btn small" onClick={() => setPage((p) => p + 1)} disabled={jobs.length < pageSize}>
          Next
        </button>
        {loading ? <span className="dim small">Loading…</span> : null}
      </div>
    </section>
  );
}

// -------------------------------------------------------------------------------------
// UI bits
// -------------------------------------------------------------------------------------
function Tab({
  id,
  active,
  count,
  onClick,
  children,
}: {
  id: string;
  active: boolean;
  count?: number;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button className={classNames("btn", "tiny", active && "primary")} onClick={onClick} data-tab={id}>
      {children}
      {Number.isFinite(count || NaN) ? <span className="tag muted ml-2">{count}</span> : null}
    </button>
  );
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

function StatusBadge({ status }: { status: "success" | "failed" | "running" | "queued" | string }) {
  const s = (status || "").toLowerCase();
  const tone =
    s === "success" ? "ok" : s === "failed" ? "bad" : s === "running" ? "warn" : s === "queued" ? "muted" : "muted";
  return <span className={classNames("tag", tone)}>{status || "—"}</span>;
}
