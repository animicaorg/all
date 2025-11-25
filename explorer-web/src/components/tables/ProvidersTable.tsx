import React from "react";
import cn from "../../utils/classnames";
import { shortHash } from "../../utils/hash";
import { formatNumber } from "../../utils/format";
import Copy from "../Copy";
import Tag from "../Tag";

export type ProviderRow = {
  id: string;
  name?: string;
  addr?: string;
  endpoint?: string;
  /** Capabilities advertised by the provider, e.g. ["ai","quantum"] */
  caps?: string[];
  /** Region string like "us-east-1" */
  region?: string;

  /** Unit price in native token (number | string | hex). If object, render as "<amount> / <unit>" */
  price?:
    | number
    | string
    | {
        amount: number | string;
        unit: string; // e.g. "sec", "request", "token"
        currency?: string; // e.g. "ANM"
      };
  currency?: string; // fallback currency label if price is number

  stake?: number | string | bigint;

  /** Success ratio (0-1 or 0-100). */
  successRate?: number;
  /** Uptime/availability ratio (0-1 or 0-100). */
  availability?: number;

  /** Latency in milliseconds (p50) */
  latencyP50?: number;

  /** Completed jobs total */
  jobsCompleted?: number;

  /** Online heartbeat (boolean) */
  online?: boolean;

  /** Last-seen timestamp ISO / unix (s|ms) */
  lastSeen?: string | number;
};

export interface ProvidersTableProps {
  providers: ProviderRow[];
  className?: string;
  loading?: boolean;
  hasMore?: boolean;
  onLoadMore?: () => void;

  /** Column toggles */
  showRegion?: boolean;
  showPrice?: boolean;
  showStake?: boolean;
  showSla?: boolean; // success + availability
  showLatency?: boolean;
  showJobs?: boolean;
  showLastSeen?: boolean;

  /** Row interactions / links */
  onRowClick?: (p: ProviderRow) => void;
  providerHref?: (p: ProviderRow) => string;
  addressHref?: (addr: string) => string;

  ariaLabel?: string;
}

/**
 * ProvidersTable — lists AICF providers with health/SLA/pricing summary.
 */
export default function ProvidersTable({
  providers,
  className,
  loading = false,
  hasMore = false,
  onLoadMore,

  showRegion = true,
  showPrice = true,
  showStake = true,
  showSla = true,
  showLatency = true,
  showJobs = true,
  showLastSeen = true,

  onRowClick,
  providerHref,
  addressHref,
  ariaLabel = "AICF providers table",
}: ProvidersTableProps) {
  return (
    <div className={cn("ow-card", className)}>
      <div className="ow-table-wrap" role="region" aria-label={ariaLabel}>
        <table className="ow-table ow-table--providers" data-testid="providers-table">
          <thead>
            <tr>
              <Th>Name</Th>
              <Th>Provider ID</Th>
              <Th>Address</Th>
              {showRegion && <Th>Region</Th>}
              <Th>Status</Th>
              {showPrice && <Th align="right">Price</Th>}
              {showStake && <Th align="right">Stake</Th>}
              {showSla && <Th align="right">SLA</Th>}
              {showLatency && <Th align="right">p50 Latency</Th>}
              {showJobs && <Th align="right">Jobs</Th>}
              {showLastSeen && <Th>Last seen</Th>}
            </tr>
          </thead>
          <tbody>
            {loading && providers.length === 0 ? (
              <SkeletonRows
                cols={
                  6 +
                  Number(showRegion) +
                  Number(showPrice) +
                  Number(showStake) +
                  Number(showSla) +
                  Number(showLatency) +
                  Number(showJobs) +
                  Number(showLastSeen)
                }
              />
            ) : providers.length === 0 ? (
              <tr>
                <td className="ow-empty" colSpan={12}>
                  No providers found.
                </td>
              </tr>
            ) : (
              providers.map((p) => {
                const href = providerHref ? providerHref(p) : `/providers/${p.id}`;
                const addrHref =
                  p.addr && addressHref ? addressHref(p.addr) : p.addr ? `/address/${p.addr}` : null;

                const price = renderPrice(p.price, p.currency);
                const stake = renderMaybeNumber(p.stake);
                const sla = renderSLA(p.successRate, p.availability);
                const lat = p.latencyP50 != null ? `${formatNumber(p.latencyP50)} ms` : "—";
                const jobs = p.jobsCompleted != null ? formatNumber(p.jobsCompleted) : "—";
                const last = p.lastSeen ? (
                  <time dateTime={toIso(p.lastSeen)} title={toFull(p.lastSeen)} className="ow-muted">
                    {ago(p.lastSeen)}
                  </time>
                ) : (
                  "—"
                );

                return (
                  <tr
                    key={p.id}
                    className={cn("ow-row", onRowClick && "ow-row--click")}
                    onClick={onRowClick ? () => onRowClick(p) : undefined}
                    tabIndex={onRowClick ? 0 : -1}
                    onKeyDown={(e) => {
                      if (!onRowClick) return;
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onRowClick(p);
                      }
                    }}
                  >
                    {/* Name + caps */}
                    <td data-label="Name">
                      <div className="ow-name">
                        <a href={href} className="ow-link">
                          {p.name || "Unnamed provider"}
                        </a>
                        <div className="ow-caps">
                          {(p.caps || []).map((c) => (
                            <Tag key={c}>{c}</Tag>
                          ))}
                        </div>
                      </div>
                    </td>

                    {/* Provider ID */}
                    <td data-label="Provider ID">
                      <a href={href} className="ow-link">
                        <code className="ow-mono">{shortHash(p.id, 6)}</code>
                      </a>
                      <Copy text={p.id} className="ow-copy" />
                    </td>

                    {/* Address */}
                    <td data-label="Address">
                      {p.addr ? (
                        <>
                          <a href={addrHref ?? "#"} className="ow-link">
                            <code className="ow-mono">{shortHash(p.addr, 6)}</code>
                          </a>
                          <Copy text={p.addr} className="ow-copy" />
                        </>
                      ) : (
                        <span className="ow-muted">—</span>
                      )}
                    </td>

                    {/* Region */}
                    {showRegion && (
                      <td data-label="Region">
                        {p.region ? <span className="ow-muted">{p.region}</span> : "—"}
                      </td>
                    )}

                    {/* Status */}
                    <td data-label="Status">
                      <StatusPill online={!!p.online} />
                    </td>

                    {/* Price */}
                    {showPrice && (
                      <td data-label="Price" className="ow-num">
                        {price}
                      </td>
                    )}

                    {/* Stake */}
                    {showStake && (
                      <td data-label="Stake" className="ow-num">
                        {stake}
                      </td>
                    )}

                    {/* SLA */}
                    {showSla && (
                      <td data-label="SLA" className="ow-num">
                        {sla}
                      </td>
                    )}

                    {/* Latency */}
                    {showLatency && (
                      <td data-label="Latency" className="ow-num">
                        {lat}
                      </td>
                    )}

                    {/* Jobs */}
                    {showJobs && (
                      <td data-label="Jobs" className="ow-num">
                        {jobs}
                      </td>
                    )}

                    {/* Last seen */}
                    {showLastSeen && <td data-label="Last seen">{last}</td>}
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

function StatusPill({ online }: { online: boolean }) {
  return (
    <span className={cn("ow-pill", online ? "ow-pill--ok" : "ow-pill--bad")}>
      {online ? "Online" : "Offline"}
    </span>
  );
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

function percent(v?: number, digits = 0): string {
  if (v == null || Number.isNaN(v)) return "—";
  const n = v > 1 ? v : v * 100;
  return `${n.toFixed(digits)}%`;
}

function renderSLA(successRate?: number, availability?: number): string {
  if (successRate == null && availability == null) return "—";
  const succ = successRate != null ? percent(successRate, 0) : "—";
  const avail = availability != null ? percent(availability, 0) : "—";
  return `${succ} / ${avail}`;
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

function renderMaybeNumber(v?: unknown): string {
  const n = toNum(v as any);
  return Number.isFinite(n) ? formatNumber(n) : "—";
}

function renderPrice(
  price: ProviderRow["price"],
  currency?: string
): string {
  if (price == null) return "—";
  if (typeof price === "object" && !Array.isArray(price)) {
    const n = toNum(price.amount);
    const amt = Number.isFinite(n) ? formatNumber(n) : String(price.amount);
    const cur = price.currency || currency || "";
    return cur ? `${amt} ${cur}/${price.unit}` : `${amt}/${price.unit}`;
  }
  const n = toNum(price);
  const amt = Number.isFinite(n) ? formatNumber(n) : String(price);
  return currency ? `${amt} ${currency}` : amt;
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

.ow-name { display: flex; align-items: center; gap: 8px; }
.ow-caps { display: inline-flex; flex-wrap: wrap; gap: 6px; }

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
