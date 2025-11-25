import React from "react";
import cn from "../../utils/classnames";
import { shortHash } from "../../utils/hash";
import { formatNumber } from "../../utils/format";
import { ago, toIso } from "../../utils/time";
import Copy from "../Copy";
import Tag from "../Tag";

export type VerificationStatus = "verified" | "unverified" | "partial";

export type ContractRow = {
  address: string;
  name?: string;
  codeHash?: string;
  verified?: boolean;                 // quick flag
  verificationStatus?: VerificationStatus;
  version?: string;                   // contract/package version (semver)
  compiler?: string;                  // toolchain/compiler id
  tags?: string[];                    // arbitrary labels (e.g., "example", "erc20")
  artifactIds?: string[];             // ids in artifact store
  deployTxHash?: string;              // creation tx
  createdHeight?: number;
  createdAt?: string | number;        // ISO | epoch(ms|s)
  lastSeenHeight?: number;
  lastSeenAt?: string | number;
};

export interface ContractsTableProps {
  items: ContractRow[];
  loading?: boolean;
  hasMore?: boolean;
  onLoadMore?: () => void;
  className?: string;

  // Column toggles
  showName?: boolean;
  showCodeHash?: boolean;
  showCompiler?: boolean;
  showVersion?: boolean;
  showTags?: boolean;
  showArtifacts?: boolean;
  showDeployTx?: boolean;
  showCreated?: boolean;
  showLastSeen?: boolean;

  // Interactions / links
  onRowClick?: (c: ContractRow) => void;
  addressHref?: (address: string) => string;
  txHref?: (txHash: string) => string;
  artifactHref?: (artifactId: string, row: ContractRow) => string;

  ariaLabel?: string;
}

/**
 * ContractsTable — lists discovered/verified contracts with code + verification metadata.
 */
export default function ContractsTable({
  items,
  loading = false,
  hasMore = false,
  onLoadMore,
  className,

  showName = true,
  showCodeHash = true,
  showCompiler = true,
  showVersion = true,
  showTags = true,
  showArtifacts = true,
  showDeployTx = true,
  showCreated = true,
  showLastSeen = true,

  onRowClick,
  addressHref,
  txHref,
  artifactHref,

  ariaLabel = "Contracts table",
}: ContractsTableProps) {
  const cols =
    1 + // address + verification
    Number(showName) +
    Number(showCodeHash) +
    Number(showCompiler) +
    Number(showVersion) +
    Number(showTags) +
    Number(showArtifacts) +
    Number(showDeployTx) +
    Number(showCreated) * 2 + // height + time
    Number(showLastSeen) * 2; // height + time

  return (
    <div className={cn("ow-card", className)}>
      <div className="ow-table-wrap" role="region" aria-label={ariaLabel}>
        <table className="ow-table ow-table--contracts" data-testid="contracts-table">
          <thead>
            <tr>
              <Th>Contract</Th>
              {showName && <Th>Name</Th>}
              {showCodeHash && <Th>Code Hash</Th>}
              {showCompiler && <Th>Compiler</Th>}
              {showVersion && <Th>Version</Th>}
              {showTags && <Th>Tags</Th>}
              {showArtifacts && <Th align="right">Artifacts</Th>}
              {showDeployTx && <Th>Deploy Tx</Th>}
              {showCreated && <Th align="right">Created H</Th>}
              {showCreated && <Th>Created</Th>}
              {showLastSeen && <Th align="right">Last H</Th>}
              {showLastSeen && <Th>Last Seen</Th>}
            </tr>
          </thead>

          <tbody>
            {loading && items.length === 0 ? (
              <SkeletonRows cols={cols} />
            ) : items.length === 0 ? (
              <tr>
                <td className="ow-empty" colSpan={cols}>
                  No contracts found.
                </td>
              </tr>
            ) : (
              items.map((c) => {
                const status = deriveStatus(c);
                const addrHref = addressHref ? addressHref(c.address) : `#/address/${c.address}`;
                const deployHref =
                  c.deployTxHash && txHref ? txHref(c.deployTxHash) : c.deployTxHash ? `#/tx/${c.deployTxHash}` : undefined;

                return (
                  <tr
                    key={c.address}
                    className={cn("ow-row", onRowClick && "ow-row--click")}
                    onClick={onRowClick ? () => onRowClick(c) : undefined}
                    tabIndex={onRowClick ? 0 : -1}
                    onKeyDown={(e) => {
                      if (!onRowClick) return;
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onRowClick(c);
                      }
                    }}
                  >
                    {/* Contract */}
                    <td data-label="Contract">
                      <a className="ow-link" href={addrHref}>
                        <code className="ow-mono">{shortHash(c.address, 10)}</code>
                      </a>
                      <Copy text={c.address} className="ow-copy" />
                      <span className="ow-gap" />
                      <VerifyPill status={status} />
                    </td>

                    {/* Name */}
                    {showName && (
                      <td data-label="Name">
                        {c.name ? <span>{c.name}</span> : <span className="ow-muted">—</span>}
                      </td>
                    )}

                    {/* Code Hash */}
                    {showCodeHash && (
                      <td data-label="Code Hash">
                        {c.codeHash ? (
                          <>
                            <code className="ow-mono">{shortHash(c.codeHash, 10)}</code>
                            <Copy text={c.codeHash} className="ow-copy" />
                          </>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Compiler */}
                    {showCompiler && (
                      <td data-label="Compiler">
                        {c.compiler ? <span className="ow-muted">{c.compiler}</span> : <span className="ow-muted">—</span>}
                      </td>
                    )}

                    {/* Version */}
                    {showVersion && (
                      <td data-label="Version">
                        {c.version ? <Tag size="sm" label={c.version} /> : <span className="ow-muted">—</span>}
                      </td>
                    )}

                    {/* Tags */}
                    {showTags && (
                      <td data-label="Tags">
                        {c.tags?.length ? (
                          <span className="ow-tags">
                            {c.tags.map((t) => (
                              <Tag key={t} size="sm" label={t} />
                            ))}
                          </span>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Artifacts */}
                    {showArtifacts && (
                      <td data-label="Artifacts" className="ow-num">
                        {c.artifactIds?.length ? (
                          <span className="ow-artifacts">
                            <span className="ow-count">{c.artifactIds.length}</span>
                            <span className="ow-gap" />
                            <span className="ow-list">
                              {c.artifactIds.slice(0, 3).map((id) => {
                                const aHref = artifactHref ? artifactHref(id, c) : `#/artifacts/${id}`;
                                return (
                                  <a key={id} className="ow-link" href={aHref} title={id}>
                                    <code className="ow-mono">{shortHash(id, 6)}</code>
                                  </a>
                                );
                              })}
                              {c.artifactIds.length > 3 && (
                                <span className="ow-muted"> +{c.artifactIds.length - 3} more</span>
                              )}
                            </span>
                          </span>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Deploy Tx */}
                    {showDeployTx && (
                      <td data-label="Deploy Tx">
                        {c.deployTxHash ? (
                          <>
                            {deployHref ? (
                              <a className="ow-link" href={deployHref}>
                                <code className="ow-mono">{shortHash(c.deployTxHash, 10)}</code>
                              </a>
                            ) : (
                              <code className="ow-mono">{shortHash(c.deployTxHash, 10)}</code>
                            )}
                            <Copy text={c.deployTxHash} className="ow-copy" />
                          </>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Created (height/time) */}
                    {showCreated && (
                      <td data-label="Created H" className="ow-num">
                        {c.createdHeight != null ? formatNumber(c.createdHeight) : "—"}
                      </td>
                    )}
                    {showCreated && (
                      <td data-label="Created">
                        {c.createdAt ? <span title={toIso(c.createdAt)}>{ago(c.createdAt)} ago</span> : <span className="ow-muted">—</span>}
                      </td>
                    )}

                    {/* Last Seen (height/time) */}
                    {showLastSeen && (
                      <td data-label="Last H" className="ow-num">
                        {c.lastSeenHeight != null ? formatNumber(c.lastSeenHeight) : "—"}
                      </td>
                    )}
                    {showLastSeen && (
                      <td data-label="Last Seen">
                        {c.lastSeenAt ? <span title={toIso(c.lastSeenAt)}>{ago(c.lastSeenAt)} ago</span> : <span className="ow-muted">—</span>}
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

function deriveStatus(c: ContractRow): VerificationStatus {
  if (c.verificationStatus) return c.verificationStatus;
  if (c.verified) return "verified";
  if (c.artifactIds && c.artifactIds.length > 0) return "partial";
  return "unverified";
}

function VerifyPill({ status }: { status: VerificationStatus }) {
  const label = status === "verified" ? "Verified" : status === "partial" ? "Partial" : "Unverified";
  const cls =
    status === "verified"
      ? "ow-pill--ok"
      : status === "partial"
      ? "ow-pill--warn"
      : "ow-pill--muted";
  return <span className={cn("ow-pill", cls)}>{label}</span>;
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

.ow-tags > * { margin-right: 6px; }
.ow-list > * { margin-right: 6px; }

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
