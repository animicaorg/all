import React from "react";
import cn from "../../utils/classnames";
import { shortHash } from "../../utils/hash";
import { formatNumber } from "../../utils/format";
import Copy from "../Copy";
import Tag from "../Tag";

export type BlobStatus = "pending" | "pinned" | "included";

export type BlobRow = {
  id: string;                 // content id / blob id (content-addressed)
  commitment: string;         // 0x… commitment (e.g., NMT root or hash)
  size: number;               // bytes
  namespace?: string;         // hex or text namespace
  provider?: string;          // e.g., "celestia", "eigen", "local"
  status?: BlobStatus;        // pending|pinned|included
  height?: number;            // inclusion height (if included)
  txHash?: string;            // tx that referenced blob (if any)
  includedAt?: string | number; // timestamp (ms or ISO)
  proof?: {
    available: boolean;
    type?: string;            // e.g., "NMT", "Light"
    id?: string;              // proof id/reference
  } | null;
};

export interface BlobsTableProps {
  blobs: BlobRow[];
  loading?: boolean;
  hasMore?: boolean;
  onLoadMore?: () => void;
  className?: string;

  // Column toggles
  showNamespace?: boolean;
  showProvider?: boolean;
  showCommitment?: boolean;
  showSize?: boolean;
  showTx?: boolean;
  showProof?: boolean;
  showIncluded?: boolean;

  // Row interactions / links
  onRowClick?: (b: BlobRow) => void;
  blobHref?: (b: BlobRow) => string;
  commitmentHref?: (commitment: string) => string;
  txHref?: (txHash: string) => string;
  proofHref?: (proofId: string, b: BlobRow) => string;

  ariaLabel?: string;
}

/**
 * BlobsTable — lists DA blobs/commitments with inclusion/size/provider info.
 */
export default function BlobsTable({
  blobs,
  loading = false,
  hasMore = false,
  onLoadMore,
  className,

  showNamespace = true,
  showProvider = true,
  showCommitment = true,
  showSize = true,
  showTx = true,
  showProof = true,
  showIncluded = true,

  onRowClick,
  blobHref,
  commitmentHref,
  txHref,
  proofHref,

  ariaLabel = "Blobs table",
}: BlobsTableProps) {
  const cols =
    1 + // id + status
    Number(showNamespace) +
    Number(showProvider) +
    Number(showCommitment) +
    Number(showSize) +
    Number(showIncluded) * 2 + // height + time
    Number(showTx) +
    Number(showProof);

  return (
    <div className={cn("ow-card", className)}>
      <div className="ow-table-wrap" role="region" aria-label={ariaLabel}>
        <table className="ow-table ow-table--blobs" data-testid="blobs-table">
          <thead>
            <tr>
              <Th>Blob</Th>
              {showNamespace && <Th>Namespace</Th>}
              {showProvider && <Th>Provider</Th>}
              {showCommitment && <Th>Commitment</Th>}
              {showSize && <Th align="right">Size</Th>}
              {showIncluded && <Th align="right">Height</Th>}
              {showIncluded && <Th>Included</Th>}
              {showTx && <Th>Tx</Th>}
              {showProof && <Th>Proof</Th>}
            </tr>
          </thead>

          <tbody>
            {loading && blobs.length === 0 ? (
              <SkeletonRows cols={cols} />
            ) : blobs.length === 0 ? (
              <tr>
                <td className="ow-empty" colSpan={cols}>
                  No blobs found.
                </td>
              </tr>
            ) : (
              blobs.map((b) => {
                const stat = deriveStatus(b);
                const bHref = blobHref ? blobHref(b) : `#/da/${b.id}`;
                const cHref = commitmentHref ? commitmentHref(b.commitment) : undefined;
                const tHref = b.txHash && txHref ? txHref(b.txHash) : b.txHash ? `#/tx/${b.txHash}` : undefined;
                const prHref = b.proof?.id && proofHref ? proofHref(b.proof.id, b) : undefined;

                return (
                  <tr
                    key={b.id}
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
                    {/* Blob */}
                    <td data-label="Blob">
                      <a className="ow-link" href={bHref}>
                        <code className="ow-mono">{shortHash(b.id, 10)}</code>
                      </a>
                      <Copy text={b.id} className="ow-copy" />
                      <span className="ow-gap" />
                      <StatusPill status={stat} />
                    </td>

                    {/* Namespace */}
                    {showNamespace && (
                      <td data-label="Namespace">
                        {b.namespace ? (
                          <>
                            <span className="ow-muted">{formatNamespace(b.namespace)}</span>
                            <Copy text={b.namespace} className="ow-copy" />
                          </>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Provider */}
                    {showProvider && (
                      <td data-label="Provider">
                        {b.provider ? <Tag size="sm" label={b.provider} /> : <span className="ow-muted">—</span>}
                      </td>
                    )}

                    {/* Commitment */}
                    {showCommitment && (
                      <td data-label="Commitment">
                        {b.commitment ? (
                          <>
                            {cHref ? (
                              <a className="ow-link" href={cHref}>
                                <code className="ow-mono">{shortHash(b.commitment, 10)}</code>
                              </a>
                            ) : (
                              <code className="ow-mono">{shortHash(b.commitment, 10)}</code>
                            )}
                            <Copy text={b.commitment} className="ow-copy" />
                          </>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Size */}
                    {showSize && (
                      <td data-label="Size" className="ow-num">
                        {formatBytes(b.size)}
                      </td>
                    )}

                    {/* Height */}
                    {showIncluded && (
                      <td data-label="Height" className="ow-num">
                        {b.height != null ? formatNumber(b.height) : "—"}
                      </td>
                    )}

                    {/* Included */}
                    {showIncluded && (
                      <td data-label="Included">
                        {b.includedAt ? (
                          <span title={toIso(b.includedAt)}>{ago(b.includedAt)} ago</span>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Tx */}
                    {showTx && (
                      <td data-label="Tx">
                        {b.txHash ? (
                          <>
                            {tHref ? (
                              <a className="ow-link" href={tHref}>
                                <code className="ow-mono">{shortHash(b.txHash, 10)}</code>
                              </a>
                            ) : (
                              <code className="ow-mono">{shortHash(b.txHash, 10)}</code>
                            )}
                            <Copy text={b.txHash} className="ow-copy" />
                          </>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Proof */}
                    {showProof && (
                      <td data-label="Proof">
                        {b.proof?.available ? (
                          <>
                            <span className="ow-badge">#{b.proof.type || "Proof"}</span>
                            {b.proof.id && (
                              <>
                                <span className="ow-gap" />
                                {prHref ? (
                                  <a className="ow-link" href={prHref}>
                                    <code className="ow-mono">{shortHash(b.proof.id, 8)}</code>
                                  </a>
                                ) : (
                                  <code className="ow-mono">{shortHash(b.proof.id, 8)}</code>
                                )}
                                <Copy text={b.proof.id} className="ow-copy" />
                              </>
                            )}
                          </>
                        ) : (
                          <span className="ow-muted">—</span>
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

function StatusPill({ status }: { status: BlobStatus }) {
  const cls =
    status === "included"
      ? "ow-pill--ok"
      : status === "pinned"
      ? "ow-pill--warn"
      : "ow-pill--muted";
  const label =
    status === "included" ? "Included" : status === "pinned" ? "Pinned" : "Pending";
  return <span className={cn("ow-pill", cls)}>{label}</span>;
}

/* --------------------------------- helpers ---------------------------------- */

function deriveStatus(b: BlobRow): BlobStatus {
  if (b.height != null) return "included";
  if (b.status) return b.status;
  if (b.provider) return "pinned";
  return "pending";
}

function toIso(t?: string | number): string {
  if (t == null) return new Date(0).toISOString();
  if (typeof t === "string") {
    const d = new Date(t);
    return isNaN(d.getTime()) ? new Date(Number(t)).toISOString() : d.toISOString();
  }
  const ms = t < 1e12 ? t * 1000 : t;
  return new Date(ms).toISOString();
}

function ago(t?: string | number): string {
  if (t == null) return "";
  const now = Date.now();
  const then = new Date(toIso(t)).getTime();
  const diff = Math.max(0, now - then);

  const s = Math.floor(diff / 1000);
  if (s < 45) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 45) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 36) return `${h}h`;
  const d = Math.floor(h / 24);
  if (d < 14) return `${d}d`;
  const w = Math.floor(d / 7);
  if (w < 8) return `${w}w`;
  const mo = Math.floor(d / 30);
  if (mo < 18) return `${mo}mo`;
  const y = Math.floor(d / 365);
  return `${y}y`;
}

function formatNamespace(ns: string): string {
  if (ns.startsWith("0x") && ns.length > 18) {
    return `${ns.slice(0, 10)}…${ns.slice(-6)}`;
  }
  if (ns.length > 24) return `${ns.slice(0, 12)}…${ns.slice(-8)}`;
  return ns;
}

function formatBytes(n: number): string {
  if (!isFinite(n) || n < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  const prec = v < 10 && i > 0 ? 2 : v < 100 && i > 0 ? 1 : 0;
  return `${v.toFixed(prec)} ${units[i]}`;
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

.ow-link { color: var(--accent, #8ab4ff); text-decoration: none; }
.ow-link:hover { text-decoration: underline; }
.ow-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
}
.ow-muted { color: var(--muted, #9aa4b2); }
.ow-copy { margin-left: 8px; vertical-align: middle; }
.ow-gap { display:inline-block; width:8px; }

/* Pills */
.ow-pill {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 18px;
  border: 1px solid transparent;
}
.ow-pill--ok    { background: rgba(16,185,129,.15); color: #34d399; border-color: rgba(16,185,129,.35); }
.ow-pill--warn  { background: rgba(245,158,11,.15); color: #fbbf24; border-color: rgba(245,158,11,.35); }
.ow-pill--muted { background: rgba(255,255,255,.08); color: #cbd5e1; border-color: rgba(255,255,255,.18); }

/* Badge (proof type) */
.ow-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 12px;
  background: rgba(255,255,255,.06);
  border: 1px solid rgba(255,255,255,.12);
  margin-right: 6px;
}

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
