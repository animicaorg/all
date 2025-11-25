import React from "react";
import cn from "../../utils/classnames";
import { shortHash } from "../../utils/hash";
import { formatNumber } from "../../utils/format";
import Copy from "../Copy";
import Tag from "../Tag";

export type SettlementStatus = "pending" | "settled" | "failed" | "reverted";
export type SettlementKind = "job" | "refund" | "penalty" | "rebate";

export type SettlementRow = {
  id: string;
  kind?: SettlementKind;

  // Links to the job/work
  jobId?: string;

  // Economic amounts
  amount?:
    | number
    | string
    | {
        amount: number | string;
        currency?: string; // e.g., ANM
        unit?: string; // e.g., "job", "token"
      };
  fee?: number | string; // network / service fee
  subsidy?: number | string; // protocol rebate, if any

  // Parties
  requester?: string;
  providerId?: string;
  providerName?: string;

  // Chain refs
  status: SettlementStatus;
  txHash?: string;
  blockHeight?: number;
  gasUsed?: number;

  // Timing
  createdAt?: string | number;
  settledAt?: string | number;

  // Notes
  note?: string;
};

export interface SettlementsTableProps {
  settlements: SettlementRow[];
  className?: string;
  loading?: boolean;
  hasMore?: boolean;
  onLoadMore?: () => void;

  // Column toggles
  showKind?: boolean;
  showJob?: boolean;
  showParties?: boolean; // requester + provider
  showEconomics?: boolean; // amount/fee/subsidy
  showGas?: boolean;
  showTx?: boolean; // tx hash + block
  showTimes?: boolean; // created/settled

  // Row interactions/links
  onRowClick?: (s: SettlementRow) => void;
  settlementHref?: (s: SettlementRow) => string;
  jobHref?: (jobId: string) => string;
  providerHref?: (providerId: string) => string;
  addressHref?: (addr: string) => string;
  txHref?: (txHash: string) => string;

  ariaLabel?: string;
}

/**
 * SettlementsTable — shows AICF settlements with economic breakdown & chain refs.
 */
export default function SettlementsTable({
  settlements,
  className,
  loading = false,
  hasMore = false,
  onLoadMore,

  showKind = true,
  showJob = true,
  showParties = true,
  showEconomics = true,
  showGas = true,
  showTx = true,
  showTimes = true,

  onRowClick,
  settlementHref,
  jobHref,
  providerHref,
  addressHref,
  txHref,

  ariaLabel = "AICF settlements table",
}: SettlementsTableProps) {
  const cols =
    2 + // id + status
    Number(showKind) +
    Number(showJob) +
    Number(showParties) * 2 +
    Number(showEconomics) * 3 +
    Number(showGas) +
    Number(showTx) * 2 +
    Number(showTimes) * 2;

  return (
    <div className={cn("ow-card", className)}>
      <div className="ow-table-wrap" role="region" aria-label={ariaLabel}>
        <table className="ow-table ow-table--settlements" data-testid="settlements-table">
          <thead>
            <tr>
              <Th>Settlement</Th>
              {showKind && <Th>Kind</Th>}
              {showJob && <Th>Job</Th>}
              {showParties && <Th>Requester</Th>}
              {showParties && <Th>Provider</Th>}
              <Th>Status</Th>
              {showEconomics && <Th align="right">Amount</Th>}
              {showEconomics && <Th align="right">Fee</Th>}
              {showEconomics && <Th align="right">Subsidy</Th>}
              {showGas && <Th align="right">Gas</Th>}
              {showTx && <Th>Tx</Th>}
              {showTx && <Th align="right">Block</Th>}
              {showTimes && <Th>Created</Th>}
              {showTimes && <Th>Settled</Th>}
            </tr>
          </thead>

          <tbody>
            {loading && settlements.length === 0 ? (
              <SkeletonRows cols={cols} />
            ) : settlements.length === 0 ? (
              <tr>
                <td className="ow-empty" colSpan={cols}>
                  No settlements found.
                </td>
              </tr>
            ) : (
              settlements.map((s) => {
                const sHref = settlementHref ? settlementHref(s) : `/settlements/${s.id}`;
                const jHref = s.jobId && jobHref ? jobHref(s.jobId) : s.jobId ? `/jobs/${s.jobId}` : null;
                const pHref =
                  s.providerId && providerHref ? providerHref(s.providerId) : s.providerId ? `/providers/${s.providerId}` : null;
                const rHref =
                  s.requester && addressHref ? addressHref(s.requester) : s.requester ? `/address/${s.requester}` : null;
                const tHref = s.txHash && txHref ? txHref(s.txHash) : s.txHash ? `/tx/${s.txHash}` : null;

                return (
                  <tr
                    key={s.id}
                    className={cn("ow-row", onRowClick && "ow-row--click")}
                    onClick={onRowClick ? () => onRowClick(s) : undefined}
                    tabIndex={onRowClick ? 0 : -1}
                    onKeyDown={(e) => {
                      if (!onRowClick) return;
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onRowClick(s);
                      }
                    }}
                  >
                    {/* Settlement */}
                    <td data-label="Settlement">
                      <a className="ow-link" href={sHref}>
                        <code className="ow-mono">{shortHash(s.id, 8)}</code>
                      </a>
                      <Copy text={s.id} className="ow-copy" />
                      {s.note && (
                        <span className="ow-muted" style={{ marginLeft: 8 }}>
                          {s.note}
                        </span>
                      )}
                    </td>

                    {/* Kind */}
                    {showKind && (
                      <td data-label="Kind">
                        <span className="ow-badge" title={s.kind || "job"}>{(s.kind || "job").toUpperCase()}</span>
                      </td>
                    )}

                    {/* Job */}
                    {showJob && (
                      <td data-label="Job">
                        {s.jobId ? (
                          <>
                            <a className="ow-link" href={jHref ?? "#"}>
                              <code className="ow-mono">{shortHash(s.jobId, 8)}</code>
                            </a>
                            <Copy text={s.jobId} className="ow-copy" />
                          </>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Requester */}
                    {showParties && (
                      <td data-label="Requester">
                        {s.requester ? (
                          <>
                            <a className="ow-link" href={rHref ?? "#"}>
                              <code className="ow-mono">{shortHash(s.requester, 6)}</code>
                            </a>
                            <Copy text={s.requester} className="ow-copy" />
                          </>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Provider */}
                    {showParties && (
                      <td data-label="Provider">
                        {s.providerId ? (
                          <>
                            <a className="ow-link" href={pHref ?? "#"}>
                              {s.providerName || <code className="ow-mono">{shortHash(s.providerId, 6)}</code>}
                            </a>
                            <Copy text={s.providerId} className="ow-copy" />
                          </>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Status */}
                    <td data-label="Status">
                      <StatusPill status={s.status} />
                    </td>

                    {/* Amount */}
                    {showEconomics && (
                      <td data-label="Amount" className="ow-num">
                        {renderAmount(s.amount)}
                      </td>
                    )}

                    {/* Fee */}
                    {showEconomics && (
                      <td data-label="Fee" className="ow-num">
                        {renderAmount(s.fee)}
                      </td>
                    )}

                    {/* Subsidy */}
                    {showEconomics && (
                      <td data-label="Subsidy" className="ow-num">
                        {renderAmount(s.subsidy)}
                      </td>
                    )}

                    {/* Gas */}
                    {showGas && (
                      <td data-label="Gas" className="ow-num">
                        {s.gasUsed != null ? formatNumber(toNum(s.gasUsed)) : "—"}
                      </td>
                    )}

                    {/* Tx */}
                    {showTx && (
                      <td data-label="Tx">
                        {s.txHash ? (
                          <>
                            <a className="ow-link" href={tHref ?? "#"}>
                              <code className="ow-mono">{shortHash(s.txHash, 10)}</code>
                            </a>
                            <Copy text={s.txHash} className="ow-copy" />
                          </>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Block */}
                    {showTx && (
                      <td data-label="Block" className="ow-num">
                        {s.blockHeight != null ? formatNumber(s.blockHeight) : "—"}
                      </td>
                    )}

                    {/* Created */}
                    {showTimes && (
                      <td data-label="Created">
                        {s.createdAt ? (
                          <time dateTime={toIso(s.createdAt)} title={toFull(s.createdAt)} className="ow-muted">
                            {ago(s.createdAt)}
                          </time>
                        ) : (
                          "—"
                        )}
                      </td>
                    )}

                    {/* Settled */}
                    {showTimes && (
                      <td data-label="Settled">
                        {s.settledAt ? (
                          <time dateTime={toIso(s.settledAt)} title={toFull(s.settledAt)} className="ow-muted">
                            {ago(s.settledAt)}
                          </time>
                        ) : s.status === "pending" ? (
                          <span className="ow-muted">pending…</span>
                        ) : (
                          "—"
                        )}
                      </td>
                    )}
                  </tr>
                );
              })
            )}
          </tbody>

          {hasMore && (
            <tfoot>
              <tr>
                <td colSpan={cols}>
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

function StatusPill({ status }: { status: SettlementStatus }) {
  const cls =
    status === "settled"
      ? "ow-pill--ok"
      : status === "failed" || status === "reverted"
      ? "ow-pill--bad"
      : "ow-pill--warn";
  const label =
    status === "pending"
      ? "Pending"
      : status === "settled"
      ? "Settled"
      : status === "failed"
      ? "Failed"
      : "Reverted";
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

function renderAmount(v?: SettlementRow["amount"]): string {
  if (v == null) return "—";
  if (typeof v === "object" && !Array.isArray(v)) {
    const n = toNum(v.amount);
    const amt = Number.isFinite(n) ? formatNumber(n) : String(v.amount);
    const cur = v.currency ? ` ${v.currency}` : "";
    const unit = v.unit ? `/${v.unit}` : "";
    return `${amt}${cur}${unit}`;
  }
  const n = toNum(v);
  return Number.isFinite(n) ? formatNumber(n) : String(v);
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
