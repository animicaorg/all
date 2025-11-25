import React from "react";
import cn from "../../utils/classnames";
import { shortHash } from "../../utils/hash";
import { formatNumber } from "../../utils/format";
import Copy from "../Copy";
import Tag from "../Tag";

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export type JobKind = "ai" | "quantum";

export type JobRow = {
  id: string;
  kind: JobKind;
  status: JobStatus;

  // Who requested the job (address)
  requester?: string;

  // Provider info
  providerId?: string;
  providerName?: string;
  region?: string;

  // Workload details
  model?: string; // AI model (e.g., "gpt-4o", "llama3.1-70b")
  promptTokens?: number;
  outputTokens?: number;
  circuitSize?: number; // number of gates/qubits summary for quantum
  caps?: string[]; // tags/capabilities (["ai","quantum","image"])

  // Economics
  cost?:
    | number
    | string
    | {
        amount: number | string;
        currency?: string; // e.g., ANM
        unit?: string; // e.g., "job", "token", "sec"
      };

  // Timing
  submittedAt?: string | number; // ISO or unix (s|ms)
  startedAt?: string | number;
  finishedAt?: string | number;

  // Runtime metrics
  latencyMs?: number; // end-to-end
  error?: string;

  // Result reference
  resultId?: string;
  resultSize?: number; // bytes
};

export interface JobsTableProps {
  jobs: JobRow[];
  className?: string;
  loading?: boolean;
  hasMore?: boolean;
  onLoadMore?: () => void;

  // Column toggles
  showRequester?: boolean;
  showProvider?: boolean;
  showRegion?: boolean;
  showWorkload?: boolean; // model/circuit
  showEconomics?: boolean; // cost/tokens
  showTimes?: boolean; // submitted/finished
  showDuration?: boolean; // derived duration
  showResult?: boolean; // result id/size

  // Row interactions/links
  onRowClick?: (j: JobRow) => void;
  jobHref?: (j: JobRow) => string;
  providerHref?: (providerId: string) => string;
  addressHref?: (addr: string) => string;
  resultHref?: (resultId: string) => string;

  ariaLabel?: string;
}

/**
 * JobsTable — lists AICF/Quantum jobs with status, timing, and economics.
 */
export default function JobsTable({
  jobs,
  className,
  loading = false,
  hasMore = false,
  onLoadMore,

  showRequester = true,
  showProvider = true,
  showRegion = true,
  showWorkload = true,
  showEconomics = true,
  showTimes = true,
  showDuration = true,
  showResult = true,

  onRowClick,
  jobHref,
  providerHref,
  addressHref,
  resultHref,
  ariaLabel = "AICF jobs table",
}: JobsTableProps) {
  const cols =
    4 + // Job / Kind / Status / Region or Workload depending on toggles; we compute flexibly below
    Number(showRequester) +
    Number(showProvider) +
    Number(showRegion) +
    Number(showWorkload) +
    Number(showEconomics) +
    Number(showTimes) * 2 +
    Number(showDuration) +
    Number(showResult);

  return (
    <div className={cn("ow-card", className)}>
      <div className="ow-table-wrap" role="region" aria-label={ariaLabel}>
        <table className="ow-table ow-table--jobs" data-testid="jobs-table">
          <thead>
            <tr>
              <Th>Job</Th>
              <Th>Kind</Th>
              {showRequester && <Th>Requester</Th>}
              {showProvider && <Th>Provider</Th>}
              {showRegion && <Th>Region</Th>}
              <Th>Status</Th>
              {showWorkload && <Th>Workload</Th>}
              {showEconomics && <Th align="right">Cost</Th>}
              {showDuration && <Th align="right">Duration</Th>}
              {showTimes && <Th>Submitted</Th>}
              {showTimes && <Th>Finished</Th>}
              {showResult && <Th>Result</Th>}
            </tr>
          </thead>

          <tbody>
            {loading && jobs.length === 0 ? (
              <SkeletonRows cols={cols} />
            ) : jobs.length === 0 ? (
              <tr>
                <td className="ow-empty" colSpan={12}>
                  No jobs found.
                </td>
              </tr>
            ) : (
              jobs.map((j) => {
                const jHref = jobHref ? jobHref(j) : `/jobs/${j.id}`;
                const pHref = j.providerId && providerHref ? providerHref(j.providerId) : j.providerId ? `/providers/${j.providerId}` : null;
                const rHref = j.resultId && resultHref ? resultHref(j.resultId) : j.resultId ? `/results/${j.resultId}` : null;
                const aHref = j.requester && addressHref ? addressHref(j.requester) : j.requester ? `/address/${j.requester}` : null;

                const workload = renderWorkload(j);
                const cost = renderCost(j.cost);
                const duration = renderDuration(j);
                const submitted = j.submittedAt ? (
                  <time dateTime={toIso(j.submittedAt)} title={toFull(j.submittedAt)} className="ow-muted">
                    {ago(j.submittedAt)}
                  </time>
                ) : (
                  "—"
                );
                const finished = j.finishedAt ? (
                  <time dateTime={toIso(j.finishedAt)} title={toFull(j.finishedAt)} className="ow-muted">
                    {ago(j.finishedAt)}
                  </time>
                ) : (
                  j.status === "running" ? <span className="ow-muted">in progress…</span> : "—"
                );
                const result = j.resultId ? (
                  <>
                    <a className="ow-link" href={rHref ?? "#"}>
                      <code className="ow-mono">{shortHash(j.resultId, 6)}</code>
                    </a>
                    <Copy text={j.resultId} className="ow-copy" />
                    {j.resultSize != null && (
                      <span className="ow-muted" style={{ marginLeft: 8 }}>
                        ({formatBytes(j.resultSize)})
                      </span>
                    )}
                  </>
                ) : (
                  <span className="ow-muted">—</span>
                );

                return (
                  <tr
                    key={j.id}
                    className={cn("ow-row", onRowClick && "ow-row--click")}
                    onClick={onRowClick ? () => onRowClick(j) : undefined}
                    tabIndex={onRowClick ? 0 : -1}
                    onKeyDown={(e) => {
                      if (!onRowClick) return;
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onRowClick(j);
                      }
                    }}
                  >
                    {/* Job */}
                    <td data-label="Job">
                      <a className="ow-link" href={jHref}>
                        <code className="ow-mono">{shortHash(j.id, 8)}</code>
                      </a>
                      <Copy text={j.id} className="ow-copy" />
                      <div className="ow-tags">
                        {(j.caps || []).map((c) => (
                          <Tag key={c}>{c}</Tag>
                        ))}
                      </div>
                    </td>

                    {/* Kind */}
                    <td data-label="Kind">
                      <span className="ow-badge">{j.kind === "ai" ? "AI" : "Quantum"}</span>
                    </td>

                    {/* Requester */}
                    {showRequester && (
                      <td data-label="Requester">
                        {j.requester ? (
                          <>
                            <a className="ow-link" href={aHref ?? "#"}>
                              <code className="ow-mono">{shortHash(j.requester, 6)}</code>
                            </a>
                            <Copy text={j.requester} className="ow-copy" />
                          </>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Provider */}
                    {showProvider && (
                      <td data-label="Provider">
                        {j.providerId ? (
                          <>
                            <a className="ow-link" href={pHref ?? "#"}>
                              {j.providerName || <code className="ow-mono">{shortHash(j.providerId, 6)}</code>}
                            </a>
                            <Copy text={j.providerId} className="ow-copy" />
                          </>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Region */}
                    {showRegion && (
                      <td data-label="Region">{j.region ? <span className="ow-muted">{j.region}</span> : "—"}</td>
                    )}

                    {/* Status */}
                    <td data-label="Status">
                      <StatusPill status={j.status} />
                    </td>

                    {/* Workload */}
                    {showWorkload && <td data-label="Workload">{workload}</td>}

                    {/* Cost */}
                    {showEconomics && (
                      <td data-label="Cost" className="ow-num">
                        {cost}
                      </td>
                    )}

                    {/* Duration */}
                    {showDuration && (
                      <td data-label="Duration" className="ow-num">
                        {duration}
                      </td>
                    )}

                    {/* Submitted */}
                    {showTimes && <td data-label="Submitted">{submitted}</td>}

                    {/* Finished */}
                    {showTimes && <td data-label="Finished">{finished}</td>}

                    {/* Result */}
                    {showResult && <td data-label="Result">{result}</td>}
                  </tr>
                );
              })
            )}
          </tbody>

          {hasMore && (
            <tfoot>
              <tr>
                <td colSpan={12}>
                  <button
                    type="button"
                    className="ow-button ow-button--ghost"
                    onClick={onLoadMore}
                    disabled={loading}
                  >
                    {loading ? "Loading…" : "Load more"}
                  </button>
                </td>
              </tr>
            </tfoot>
          )}
        </table>
      </div>

      <style>{styles}</style>
    </div>
  );
}

/* --------------------------------- UI bits ---------------------------------- */

function Th({
  children,
  align,
}: {
  children: React.ReactNode;
  align?: "left" | "right" | "center";
}) {
  return (
    <th style={align ? { textAlign: align } : undefined}>
      <span>{children}</span>
    </th>
  );
}

function SkeletonRows({ cols }: { cols: number }) {
  return (
    <>
      {Array.from({ length: 8 }).map((_, i) => (
        <tr key={i} className="ow-skel-row">
          {Array.from({ length: cols }).map((__, j) => (
            <td key={j}>
              <div className="ow-skel" />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

function StatusPill({ status }: { status: JobStatus }) {
  const cls =
    status === "succeeded"
      ? "ow-pill--ok"
      : status === "failed" || status === "cancelled"
      ? "ow-pill--bad"
      : "ow-pill--warn";
  const label =
    status === "queued"
      ? "Queued"
      : status === "running"
      ? "Running"
      : status === "succeeded"
      ? "Succeeded"
      : status === "failed"
      ? "Failed"
      : "Cancelled";
  return <span className={cn("ow-pill", cls)}>{label}</span>;
}

/* --------------------------------- helpers ---------------------------------- */

function toIso(t?: string | number): string {
  if (t == null) return new Date(0).toISOString();
  if (typeof t === "string") {
    const d = new Date(t);
    return isNaN(d.getTime()) ? new Date(Number(t)).toISOString() : d.toISOString();
  }
  const ms = t < 1e12 ? t * 1000 : t;
  return new Date(ms).toISOString();
}

function toFull(t?: string | number): string {
  const iso = toIso(t);
  const d = new Date(iso);
  return d.toLocaleString();
}

function ago(t?: string | number): string {
  if (t == null) return "—";
  const now = Date.now();
  const then = new Date(toIso(t)).getTime();
  const diff = Math.max(0, now - then);

  const s = Math.floor(diff / 1000);
  if (s < 45) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 45) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 36) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 14) return `${d}d ago`;
  const w = Math.floor(d / 7);
  if (w < 8) return `${w}w ago`;
  const mo = Math.floor(d / 30);
  if (mo < 18) return `${mo}mo ago`;
  const y = Math.floor(d / 365);
  return `${y}y ago`;
}

function renderWorkload(j: JobRow): React.ReactNode {
  if (j.kind === "ai") {
    const tokens =
      j.promptTokens != null || j.outputTokens != null ? (
        <span className="ow-muted" style={{ marginLeft: 6 }}>
          ({(j.promptTokens ?? 0) + (j.outputTokens ?? 0)} tok)
        </span>
      ) : null;
    return (
      <>
        <span>{j.model || "—"}</span>
        {tokens}
      </>
    );
  }
  // quantum
  return (
    <>
      <span>Circuit</span>
      {j.circuitSize != null && (
        <span className="ow-muted" style={{ marginLeft: 6 }}>
          ({formatNumber(j.circuitSize)} ops)
        </span>
      )}
    </>
  );
}

function toNum(v: unknown): number {
  if (v == null) return NaN;
  if (typeof v === "number") return v;
  if (typeof v === "bigint") {
    const n = Number(v);
    return Number.isFinite(n) ? n : NaN;
  }
  const s = String(v);
  if (/^0x[0-9a-fA-F]+$/.test(s)) {
    const n = parseInt(s, 16);
    return Number.isFinite(n) ? n : NaN;
  }
  const n = Number(s);
  return Number.isFinite(n) ? n : NaN;
}

function renderCost(cost: JobRow["cost"]): string {
  if (cost == null) return "—";
  if (typeof cost === "object" && !Array.isArray(cost)) {
    const n = toNum(cost.amount);
    const amt = Number.isFinite(n) ? formatNumber(n) : String(cost.amount);
    const cur = cost.currency ? ` ${cost.currency}` : "";
    const unit = cost.unit ? `/${cost.unit}` : "";
    return `${amt}${cur}${unit}`;
  }
  const n = toNum(cost);
  return Number.isFinite(n) ? formatNumber(n) : String(cost);
}

function msToHuman(ms?: number): string {
  if (ms == null || !isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 2 : 1)} s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return `${m}m ${r}s`;
}

function renderDuration(j: JobRow): string {
  // Prefer finished - started; else started - now; else submitted - now.
  const start = ts(j.startedAt);
  const end = ts(j.finishedAt);
  if (end && start) return msToHuman(end - start);
  if (start) return msToHuman(Date.now() - start);
  const sub = ts(j.submittedAt);
  if (sub) return msToHuman((ts(j.finishedAt) ?? Date.now()) - sub);
  return "—";
}

function ts(t?: string | number): number | null {
  if (t == null) return null;
  const d = new Date(toIso(t));
  const n = d.getTime();
  return isNaN(n) ? null : n;
}

function formatBytes(bytes?: number): string {
  if (bytes == null || !isFinite(bytes) || bytes < 0) return "—";
  const u = ["B", "kB", "MB", "GB", "TB"];
  let i = 0;
  let n = bytes;
  while (n >= 1024 && i < u.length - 1) {
    n /= 1024;
    i++;
  }
  const digits = n < 10 && i > 0 ? 2 : 1;
  return `${n.toFixed(digits)} ${u[i]}`;
}

/* --------------------------------- styles ----------------------------------- */

const styles = `
.ow-card {
  border: 1px solid var(--border, rgba(0,0,0,.1));
  border-radius: 12px;
  background: var(--panel, #0b1020);
}

.ow-table-wrap { overflow-x: auto; }

.ow-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 14px;
}

.ow-table thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--panel, #0b1020);
  text-align: left;
  font-weight: 600;
  color: var(--muted, #9aa4b2);
  padding: 10px 12px;
  border-bottom: 1px solid var(--border, rgba(255,255,255,.08));
  white-space: nowrap;
}

.ow-table tbody td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border, rgba(255,255,255,.06));
  vertical-align: middle;
}

.ow-table tfoot td {
  padding: 10px 12px;
  text-align: center;
}

.ow-row--click { cursor: pointer; }
.ow-row--click:hover { background: rgba(255,255,255,.03); }

.ow-empty {
  text-align: center;
  padding: 18px;
  color: var(--muted, #9aa4b2);
}

.ow-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.ow-link {
  color: var(--accent, #8ab4ff);
  text-decoration: none;
}
.ow-link:hover { text-decoration: underline; }

.ow-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
}

.ow-muted { color: var(--muted, #9aa4b2); }
.ow-copy { margin-left: 8px; vertical-align: middle; }

.ow-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 12px;
  background: rgba(255,255,255,.06);
  border: 1px solid rgba(255,255,255,.12);
}

.ow-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }

/* Pills */
.ow-pill {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 18px;
  border: 1px solid transparent;
}
.ow-pill--ok   { background: rgba(16,185,129,.15); color: #34d399; border-color: rgba(16,185,129,.35); }
.ow-pill--bad  { background: rgba(239,68,68,.15);  color: #f87171; border-color: rgba(239,68,68,.35); }
.ow-pill--warn { background: rgba(245,158,11,.15); color: #fbbf24; border-color: rgba(245,158,11,.35); }

/* Skeletons */
.ow-skel-row .ow-skel {
  height: 12px;
  width: 100%;
  background: linear-gradient(
    90deg,
    rgba(255,255,255,0.06) 0%,
    rgba(255,255,255,0.12) 50%,
    rgba(255,255,255,0.06) 100%
  );
  border-radius: 6px;
  animation: ow-shine 1200ms ease-in-out infinite;
}
@keyframes ow-shine {
  0% { background-position: 0% 0; }
  100% { background-position: 200% 0; }
}

/* Buttons */
.ow-button {
  appearance: none;
  border: 1px solid var(--border, rgba(255,255,255,.12));
  background: transparent;
  color: var(--fg, #e5e7eb);
  border-radius: 8px;
  padding: 8px 12px;
  cursor: pointer;
}
.ow-button--ghost:hover { background: rgba(255,255,255,.05); }
.ow-button[disabled] { opacity: .6; cursor: default; }
`;
