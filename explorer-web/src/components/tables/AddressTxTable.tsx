import React from "react";
import cn from "../../utils/classnames";
import Copy from "../Copy";
import { shortHash } from "../../utils/hash";
import { formatNumber } from "../../utils/format";

export type AddressTxRow = {
  hash: string;
  from: string;
  to?: string | null;
  /** optional method / call label */
  method?: string;
  /** block height (some APIs use `block` or `height`) */
  block?: number;
  height?: number;
  /** ISO string or unix seconds/ms */
  time?: string | number;
  timestamp?: string | number;
  /** value and fee types are flexible (hex/number/bigint/string) */
  value?: string | number | bigint;
  amount?: string | number | bigint;
  fee?: string | number | bigint;
  gasUsed?: number;
  status?: "success" | "failed" | "pending" | boolean | number;
  /** optional tx index within block */
  index?: number;
};

export interface AddressTxTableProps {
  /** The perspective address (used to compute IN/OUT/SELF/OTHER) */
  address: string;

  txs: AddressTxRow[];
  className?: string;
  loading?: boolean;
  hasMore?: boolean;
  onLoadMore?: () => void;

  /** Column visibility toggles */
  showMethod?: boolean;
  showFee?: boolean;
  showGas?: boolean;
  showBlock?: boolean;
  showTime?: boolean;
  showStatus?: boolean;

  /** Link builders / row click */
  onRowClick?: (t: AddressTxRow) => void;
  txHref?: (t: AddressTxRow) => string;
  addrHref?: (addr: string) => string;
  blockHref?: (height: number) => string;

  /** Optional aria-label for the table */
  ariaLabel?: string;
}

/**
 * AddressTxTable — A transaction table rendered from the perspective of a single address.
 * Shows direction (IN / OUT / SELF / OTHER) and the counterparty column.
 */
export default function AddressTxTable({
  address,
  txs,
  className,
  loading = false,
  hasMore = false,
  onLoadMore,
  showMethod = true,
  showFee = true,
  showGas = false,
  showBlock = true,
  showTime = true,
  showStatus = true,
  onRowClick,
  txHref,
  addrHref,
  blockHref,
  ariaLabel = "Address transactions table",
}: AddressTxTableProps) {
  const who = normAddr(address);

  return (
    <div className={cn("ow-card", className)}>
      <div className="ow-table-wrap" role="region" aria-label={ariaLabel}>
        <table className="ow-table ow-table--tx" data-testid="address-tx-table">
          <thead>
            <tr>
              <Th>Tx</Th>
              <Th>Direction</Th>
              <Th>Counterparty</Th>
              {showMethod && <Th>Method</Th>}
              <Th align="right">Value</Th>
              {showFee && <Th align="right">Fee</Th>}
              {showGas && <Th align="right">Gas Used</Th>}
              {showBlock && <Th align="right">Block</Th>}
              {showTime && <Th>Time</Th>}
              {showStatus && <Th>Status</Th>}
            </tr>
          </thead>
          <tbody>
            {loading && txs.length === 0 ? (
              <SkeletonRows
                cols={
                  6 +
                  Number(showMethod) +
                  Number(showFee) +
                  Number(showGas) +
                  Number(showBlock) +
                  Number(showTime) +
                  Number(showStatus)
                }
              />
            ) : txs.length === 0 ? (
              <tr>
                <td className="ow-empty" colSpan={10}>
                  No transactions for this address.
                </td>
              </tr>
            ) : (
              txs.map((t) => {
                const dir = directionFor(who, t.from, t.to);
                const cp = counterpartyFor(who, t.from, t.to);
                const href = txHref ? txHref(t) : `/tx/${t.hash}`;
                const cpHref =
                  cp && isAddressLike(cp) ? (addrHref ? addrHref(cp) : `/address/${cp}`) : null;

                const block = t.height ?? t.block;
                const time = t.time ?? t.timestamp;
                const method = t.method ?? inferMethod(t);
                const value = firstDefined(t.value, t.amount);
                const gas = t.gasUsed;

                return (
                  <tr
                    key={t.hash}
                    className={cn("ow-row", onRowClick && "ow-row--click")}
                    onClick={onRowClick ? () => onRowClick(t) : undefined}
                    tabIndex={onRowClick ? 0 : -1}
                    onKeyDown={(e) => {
                      if (!onRowClick) return;
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onRowClick(t);
                      }
                    }}
                  >
                    <td data-label="Tx">
                      <a href={href} className="ow-link">
                        <code className="ow-mono">{shortHash(t.hash, 6)}</code>
                      </a>
                      <Copy text={t.hash} className="ow-copy" />
                    </td>

                    <td data-label="Direction">
                      <DirectionPill dir={dir} />
                    </td>

                    <td data-label="Counterparty">
                      {cp ? (
                        isAddressLike(cp) ? (
                          <>
                            <a href={cpHref!} className="ow-link">
                              {shortHash(cp, 6)}
                            </a>
                            <Copy text={cp} className="ow-copy" />
                          </>
                        ) : (
                          <span className="ow-muted">{cp}</span>
                        )
                      ) : (
                        <span className="ow-muted">—</span>
                      )}
                    </td>

                    {showMethod && (
                      <td data-label="Method">
                        {method ? (
                          <span className="ow-muted">{method}</span>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    <td data-label="Value" className="ow-num">
                      {formatMaybeNumber(value)}
                    </td>

                    {showFee && (
                      <td data-label="Fee" className="ow-num">
                        {formatMaybeNumber(t.fee)}
                      </td>
                    )}

                    {showGas && (
                      <td data-label="Gas Used" className="ow-num">
                        {gas != null ? formatNumber(gas) : "—"}
                      </td>
                    )}

                    {showBlock && (
                      <td data-label="Block" className="ow-num">
                        {block != null ? (
                          <a
                            href={blockHref ? blockHref(block) : `/block/${block}`}
                            className="ow-link"
                          >
                            {formatNumber(block)}
                          </a>
                        ) : (
                          "—"
                        )}
                      </td>
                    )}

                    {showTime && (
                      <td data-label="Time">
                        <time dateTime={toIso(time)} title={toFull(time)} className="ow-muted">
                          {ago(time)}
                        </time>
                      </td>
                    )}

                    {showStatus && (
                      <td data-label="Status">
                        <StatusPill status={t.status} />
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
                <td colSpan={10}>
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

/* ------------------------------ helpers & UI bits ------------------------------ */

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

type Direction = "IN" | "OUT" | "SELF" | "OTHER" | "DEPLOY";

function normAddr(a: string): string {
  return String(a || "").toLowerCase();
}

function isAddressLike(v: string): boolean {
  // simple heuristic: 0x-prefixed or bech32-ish (contains '1' separator)
  return /^0x[0-9a-fA-F]{6,}$/.test(v) || /^[a-z0-9]{1,83}1[ac-hj-np-z02-9]{6,}$/i.test(v);
}

function directionFor(me: string, from: string, to?: string | null): Direction {
  const f = normAddr(from);
  const t = to ? normAddr(to) : null;

  if (f === me && t === me) return "SELF";
  if (f === me && !t) return "DEPLOY";
  if (f === me) return "OUT";
  if (t === me) return "IN";
  return "OTHER";
}

function counterpartyFor(me: string, from: string, to?: string | null): string | null {
  const f = normAddr(from);
  const t = to ? normAddr(to) : null;

  if (f === me && t === me) return "Self";
  if (f === me && !t) return "Contract Creation";
  if (f === me) return to ?? null;
  if (t === me) return from;
  return to ?? from ?? null;
}

function inferMethod(t: AddressTxRow): string | undefined {
  const to = t.to ?? null;
  const val = firstDefined(t.value, t.amount);
  if (!to) return "deploy";
  const n = toNum(val);
  if (!isNaN(n) && n > 0) return "transfer";
  return undefined;
}

function firstDefined<T>(...vals: (T | undefined | null)[]): T | undefined {
  for (const v of vals) if (v !== undefined && v !== null) return v as T;
  return undefined;
}

function toNum(v: AddressTxRow["value"]): number {
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

function formatMaybeNumber(v: AddressTxRow["value"]): string {
  const n = toNum(v);
  return Number.isFinite(n) ? formatNumber(n) : "—";
}

function StatusPill({ status }: { status: AddressTxRow["status"] }) {
  const { label, cls } = normStatus(status);
  return <span className={cn("ow-pill", cls)}>{label}</span>;
}

function DirectionPill({ dir }: { dir: Direction }) {
  const map: Record<Direction, { label: string; cls: string }> = {
    IN: { label: "IN", cls: "ow-pill--ok" },
    OUT: { label: "OUT", cls: "ow-pill--warn" },
    SELF: { label: "SELF", cls: "ow-pill--muted" },
    OTHER: { label: "OTHER", cls: "ow-pill--muted" },
    DEPLOY: { label: "DEPLOY", cls: "ow-pill--info" },
  };
  const { label, cls } = map[dir];
  return <span className={cn("ow-pill", cls)}>{label}</span>;
}

function normStatus(
  s: AddressTxRow["status"]
): { label: string; cls: string } {
  if (s === "success" || s === true || s === 1)
    return { label: "Success", cls: "ow-pill--ok" };
  if (s === "failed" || s === false || s === 0)
    return { label: "Failed", cls: "ow-pill--bad" };
  return { label: "Pending", cls: "ow-pill--warn" };
}

/* ------------------------------ time utils ------------------------------ */

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

/* ------------------------------ local styles ------------------------------ */

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
.ow-pill--info { background: rgba(59,130,246,.15); color: #93c5fd; border-color: rgba(59,130,246,.35); }
.ow-pill--muted { background: rgba(148,163,184,.15); color: #cbd5e1; border-color: rgba(148,163,184,.35); }

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
