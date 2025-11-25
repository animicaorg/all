import React from "react";
import cn from "../../utils/classnames";
import Copy from "../Copy";
import { shortHash } from "../../utils/hash";
import { formatNumber } from "../../utils/format";

export type BlockRow = {
  height: number;
  hash: string;
  /** ISO string or unix seconds/ms */
  time?: string | number;
  /** Some backends expose `timestamp` instead of `time` */
  timestamp?: string | number;
  txs?: number;
  txCount?: number;
  gasUsed?: number;
  gas?: number;
  proposer?: string;
};

export interface BlocksTableProps {
  blocks: BlockRow[];
  className?: string;
  loading?: boolean;
  /** Show an extra column for proposer address */
  showProposer?: boolean;
  /** If true, renders a footer "Load more" control */
  hasMore?: boolean;
  onLoadMore?: () => void;
  /** Called when a row is clicked; default navigates via anchor links */
  onRowClick?: (b: BlockRow) => void;
  /** If provided, overrides the href used for each block link */
  blockHref?: (b: BlockRow) => string;
  /** Optional aria-label for the table */
  ariaLabel?: string;
}

/**
 * BlocksTable — Responsive, accessible table for recent blocks.
 * - Handles differing API field names (time/timestamp, txs/txCount, gas/gasUsed).
 * - Integrates with Copy and formatting utilities.
 * - Supports "Load more" pagination footer.
 */
export default function BlocksTable({
  blocks,
  className,
  loading = false,
  showProposer = true,
  hasMore = false,
  onLoadMore,
  onRowClick,
  blockHref,
  ariaLabel = "Blocks table",
}: BlocksTableProps) {
  return (
    <div className={cn("ow-card", className)}>
      <div className="ow-table-wrap" role="region" aria-label={ariaLabel}>
        <table className="ow-table ow-table--block" data-testid="blocks-table">
          <thead>
            <tr>
              <Th>Height</Th>
              <Th>Hash</Th>
              <Th>Time</Th>
              <Th align="right">Txs</Th>
              <Th align="right">Gas Used</Th>
              {showProposer && <Th>Proposer</Th>}
            </tr>
          </thead>
          <tbody>
            {loading && blocks.length === 0 ? (
              <SkeletonRows showProposer={showProposer} />
            ) : blocks.length === 0 ? (
              <tr>
                <td className="ow-empty" colSpan={showProposer ? 6 : 5}>
                  No blocks to display.
                </td>
              </tr>
            ) : (
              blocks.map((b) => {
                const href = blockHref ? blockHref(b) : `/block/${b.height ?? b.hash}`;
                const time = b.time ?? b.timestamp;
                const txCount = (b.txs ?? b.txCount) ?? 0;
                const gas = (b.gasUsed ?? b.gas) ?? undefined;

                const row = (
                  <tr
                    key={b.hash}
                    className={cn("ow-row", onRowClick && "ow-row--click")}
                    onClick={onRowClick ? () => onRowClick(b) : undefined}
                    tabIndex={onRowClick ? 0 : -1}
                    onKeyDown={(e) => {
                      if (!onRowClick) return;
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onRowClick(b);
                      }
                    }}
                  >
                    <td data-label="Height">
                      <a href={href} className="ow-link">
                        {formatNumber(b.height)}
                      </a>
                    </td>
                    <td data-label="Hash">
                      <code className="ow-mono">{shortHash(b.hash, 6)}</code>
                      <Copy text={b.hash} className="ow-copy" />
                    </td>
                    <td data-label="Time">
                      <time
                        dateTime={toIso(time)}
                        title={toFull(time)}
                        className="ow-muted"
                      >
                        {ago(time)}
                      </time>
                    </td>
                    <td data-label="Txs" className="ow-num">
                      {formatNumber(txCount)}
                    </td>
                    <td data-label="Gas Used" className="ow-num">
                      {gas != null ? formatNumber(gas) : "—"}
                    </td>
                    {showProposer && (
                      <td data-label="Proposer">
                        {b.proposer ? (
                          <a href={`/address/${b.proposer}`} className="ow-link">
                            {shortHash(b.proposer, 6)}
                          </a>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}
                  </tr>
                );

                // If we don't have a row click handler, keep the link focusable within row
                return row;
              })
            )}
          </tbody>
          {hasMore && (
            <tfoot>
              <tr>
                <td colSpan={showProposer ? 6 : 5}>
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

function SkeletonRows({ showProposer }: { showProposer: boolean }) {
  const cols = showProposer ? 6 : 5;
  return (
    <>
      {Array.from({ length: 6 }).map((_, i) => (
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

/* ------------------------------ time utils ------------------------------ */

function toIso(t?: string | number): string {
  if (t == null) return new Date(0).toISOString();
  if (typeof t === "string") {
    // already ISO? try to parse either way
    const d = new Date(t);
    return isNaN(d.getTime()) ? new Date(Number(t)).toISOString() : d.toISOString();
    }
  // If seconds, convert to ms
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

.ow-table-wrap {
  overflow-x: auto;
}

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

.ow-row--click {
  cursor: pointer;
}
.ow-row--click:hover {
  background: rgba(255,255,255,.03);
}

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

.ow-muted {
  color: var(--muted, #9aa4b2);
}

.ow-copy {
  margin-left: 8px;
  vertical-align: middle;
}

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
.ow-button--ghost:hover {
  background: rgba(255,255,255,.05);
}
.ow-button[disabled] {
  opacity: .6;
  cursor: default;
}
`;
