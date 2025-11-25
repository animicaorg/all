import React from "react";
import cn from "../../utils/classnames";
import { shortHash } from "../../utils/hash";
import { formatNumber } from "../../utils/format";
import Copy from "../Copy";
import Tag from "../Tag";

export type PeerDirection = "in" | "out" | "mixed";
export type PeerStatus = "online" | "slow" | "offline";

export type PeerRow = {
  id: string;                  // libp2p/node id or peer id
  address?: string;            // /ip4/.. or host:port
  client?: string;             // e.g. "animica-node"
  version?: string;            // e.g. "1.2.3"
  direction?: PeerDirection;   // in/out/mixed
  latencyMs?: number;          // rolling RTT
  height?: number;             // peer's reported tip height
  headHash?: string;           // peer's head hash (optional)
  country?: string;            // ISO 3166-1 alpha-2 (e.g., "US")
  city?: string;
  asn?: string;                // "AS13335"
  score?: number;              // 0..100; health/rep score
  connectedAt?: string | number;
  lastSeenAt?: string | number; // heartbeat/ping at
  services?: string[];         // ["rpc","ws","aicf"]
};

export interface PeersTableProps {
  peers: PeerRow[];
  loading?: boolean;
  hasMore?: boolean;
  onLoadMore?: () => void;

  className?: string;

  // Column toggles
  showAddr?: boolean;       // address / multiaddr
  showClient?: boolean;     // client name + version
  showConn?: boolean;       // direction + since
  showGeo?: boolean;        // country/city/asn
  showLatency?: boolean;    // rtt
  showSync?: boolean;       // height + head
  showScore?: boolean;      // health score
  showServices?: boolean;   // service tags

  // Row interactions/links
  onRowClick?: (p: PeerRow) => void;
  peerHref?: (p: PeerRow) => string;
  addressHref?: (addr: string) => string;
  headHref?: (hash: string) => string;

  ariaLabel?: string;
}

/**
 * PeersTable — lists connected/known peers with health, sync and geo hints.
 */
export default function PeersTable({
  peers,
  loading = false,
  hasMore = false,
  onLoadMore,

  className,

  showAddr = true,
  showClient = true,
  showConn = true,
  showGeo = true,
  showLatency = true,
  showSync = true,
  showScore = true,
  showServices = true,

  onRowClick,
  peerHref,
  addressHref,
  headHref,

  ariaLabel = "Peers table",
}: PeersTableProps) {
  const cols =
    1 + // peer id
    Number(showAddr) +
    Number(showClient) +
    Number(showConn) +
    Number(showGeo) +
    Number(showLatency) +
    Number(showSync) * 2 +
    Number(showScore) +
    Number(showServices);

  return (
    <div className={cn("ow-card", className)}>
      <div className="ow-table-wrap" role="region" aria-label={ariaLabel}>
        <table className="ow-table ow-table--peers" data-testid="peers-table">
          <thead>
            <tr>
              <Th>Peer</Th>
              {showAddr && <Th>Address</Th>}
              {showClient && <Th>Client</Th>}
              {showConn && <Th>Connection</Th>}
              {showGeo && <Th>Geo/ASN</Th>}
              {showLatency && <Th align="right">Latency</Th>}
              {showSync && <Th align="right">Height</Th>}
              {showSync && <Th>Head</Th>}
              {showScore && <Th align="right">Score</Th>}
              {showServices && <Th>Services</Th>}
            </tr>
          </thead>

          <tbody>
            {loading && peers.length === 0 ? (
              <SkeletonRows cols={cols} />
            ) : peers.length === 0 ? (
              <tr>
                <td className="ow-empty" colSpan={cols}>
                  No peers found.
                </td>
              </tr>
            ) : (
              peers.map((p) => {
                const status = deriveStatus(p);
                const pHref = peerHref ? peerHref(p) : `#/peers/${p.id}`;
                const aHref = p.address && addressHref ? addressHref(p.address) : undefined;
                const hHref = p.headHash && headHref ? headHref(p.headHash) : p.headHash ? `#/tx/${p.headHash}` : undefined;

                return (
                  <tr
                    key={p.id}
                    className={cn(
                      "ow-row",
                      onRowClick && "ow-row--click",
                      status === "offline" && "ow-row--dim"
                    )}
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
                    {/* Peer */}
                    <td data-label="Peer">
                      <a className="ow-link" href={pHref}>
                        <code className="ow-mono">{shortHash(p.id, 10)}</code>
                      </a>
                      <Copy text={p.id} className="ow-copy" />
                      <span className="ow-gap" />
                      <StatusPill status={status} />
                    </td>

                    {/* Address */}
                    {showAddr && (
                      <td data-label="Address">
                        {p.address ? (
                          <>
                            {aHref ? (
                              <a className="ow-link" href={aHref}>
                                {truncateAddr(p.address)}
                              </a>
                            ) : (
                              <span className="ow-muted">{truncateAddr(p.address)}</span>
                            )}
                            <Copy text={p.address} className="ow-copy" />
                          </>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Client */}
                    {showClient && (
                      <td data-label="Client">
                        {p.client ? (
                          <>
                            <span>{p.client}</span>
                            {p.version && (
                              <span className="ow-muted" style={{ marginLeft: 6 }}>
                                v{p.version}
                              </span>
                            )}
                          </>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Connection */}
                    {showConn && (
                      <td data-label="Connection">
                        <ConnBadge dir={p.direction} />
                        <span className="ow-muted">
                          {p.connectedAt ? ` since ${ago(p.connectedAt)}` : ""}
                        </span>
                      </td>
                    )}

                    {/* Geo/ASN */}
                    {showGeo && (
                      <td data-label="Geo/ASN">
                        {p.country || p.city || p.asn ? (
                          <span>
                            {p.country && <Tag size="sm" label={p.country} />}
                            {p.city && (
                              <span className="ow-muted" style={{ marginLeft: 6 }}>
                                {p.city}
                              </span>
                            )}
                            {p.asn && (
                              <span className="ow-muted" style={{ marginLeft: 6 }}>
                                {p.asn}
                              </span>
                            )}
                          </span>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Latency */}
                    {showLatency && (
                      <td data-label="Latency" className="ow-num">
                        {p.latencyMs != null ? formatLatency(p.latencyMs) : "—"}
                      </td>
                    )}

                    {/* Height */}
                    {showSync && (
                      <td data-label="Height" className="ow-num">
                        {p.height != null ? formatNumber(p.height) : "—"}
                      </td>
                    )}

                    {/* Head */}
                    {showSync && (
                      <td data-label="Head">
                        {p.headHash ? (
                          <>
                            {hHref ? (
                              <a className="ow-link" href={hHref}>
                                <code className="ow-mono">{shortHash(p.headHash, 10)}</code>
                              </a>
                            ) : (
                              <code className="ow-mono">{shortHash(p.headHash, 10)}</code>
                            )}
                            <Copy text={p.headHash} className="ow-copy" />
                          </>
                        ) : (
                          <span className="ow-muted">—</span>
                        )}
                      </td>
                    )}

                    {/* Score */}
                    {showScore && (
                      <td data-label="Score" className="ow-num">
                        {p.score != null ? <ScoreBar value={clamp(p.score, 0, 100)} /> : "—"}
                      </td>
                    )}

                    {/* Services */}
                    {showServices && (
                      <td data-label="Services">
                        {p.services && p.services.length > 0 ? (
                          <span className="ow-services">
                            {p.services.map((s) => (
                              <span key={s} className="ow-svc">{s}</span>
                            ))}
                          </span>
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

function StatusPill({ status }: { status: PeerStatus }) {
  const cls =
    status === "online"
      ? "ow-pill--ok"
      : status === "slow"
      ? "ow-pill--warn"
      : "ow-pill--bad";
  const label =
    status === "online" ? "Online" : status === "slow" ? "Slow" : "Offline";
  return <span className={cn("ow-pill", cls)}>{label}</span>;
}

function ConnBadge({ dir }: { dir?: PeerDirection }) {
  const label = dir ? dir.toUpperCase() : "—";
  const tone =
    dir === "in" ? "ow-badge--in" : dir === "out" ? "ow-badge--out" : dir === "mixed" ? "ow-badge--mix" : "";
  return <span className={cn("ow-badge", tone)}>{label}</span>;
}

function ScoreBar({ value }: { value: number }) {
  const pct = clamp(value, 0, 100);
  const tone = pct >= 70 ? "ok" : pct >= 35 ? "mid" : "low";
  return (
    <div className="ow-score" title={`${pct.toFixed(0)} / 100`}>
      <div className={cn("ow-score__bar", `ow-score__bar--${tone}`)} style={{ width: `${pct}%` }} />
      <span className="ow-score__text">{pct.toFixed(0)}</span>
    </div>
  );
}

/* --------------------------------- helpers ---------------------------------- */

function deriveStatus(p: PeerRow): PeerStatus {
  const now = Date.now();
  const last = p.lastSeenAt ? new Date(toIso(p.lastSeenAt)).getTime() : undefined;
  if (last && now - last > 90_000) return "offline";
  if (p.latencyMs == null) return "slow";
  if (p.latencyMs > 350) return "slow";
  return "online";
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

function formatLatency(ms: number): string {
  if (ms < 1) return `${ms.toFixed(2)} ms`;
  if (ms < 10) return `${ms.toFixed(1)} ms`;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function truncateAddr(a: string): string {
  if (!a) return "";
  if (a.length <= 42) return a;
  return `${a.slice(0, 22)}…${a.slice(-16)}`;
}

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
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
.ow-row--dim { opacity: .7; }

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
.ow-pill--ok   { background: rgba(16,185,129,.15); color: #34d399; border-color: rgba(16,185,129,.35); }
.ow-pill--bad  { background: rgba(239,68,68,.15);  color: #f87171; border-color: rgba(239,68,68,.35); }
.ow-pill--warn { background: rgba(245,158,11,.15); color: #fbbf24; border-color: rgba(245,158,11,.35); }

/* Badge */
.ow-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 12px;
  background: rgba(255,255,255,.06);
  border: 1px solid rgba(255,255,255,.12);
  margin-right: 6px;
}
.ow-badge--in  { background: rgba(59,130,246,.15); border-color: rgba(59,130,246,.35); color:#93c5fd; }
.ow-badge--out { background: rgba(99,102,241,.15); border-color: rgba(99,102,241,.35); color:#a5b4fc; }
.ow-badge--mix { background: rgba(16,185,129,.15); border-color: rgba(16,185,129,.35); color:#6ee7b7; }

/* Score */
.ow-score {
  position: relative;
  height: 18px;
  min-width: 72px;
  border: 1px solid rgba(255,255,255,.12);
  border-radius: 6px;
  overflow: hidden;
  background: rgba(255,255,255,.04);
}
.ow-score__bar {
  height: 100%;
}
.ow-score__bar--ok  { background: rgba(16,185,129,.45); }
.ow-score__bar--mid { background: rgba(245,158,11,.45); }
.ow-score__bar--low { background: rgba(239,68,68,.45); }
.ow-score__text {
  position: absolute;
  top: 0; left: 0; right: 0; bottom: 0;
  text-align: center;
  font-size: 12px;
  line-height: 18px;
  color: #e5e7eb;
  text-shadow: 0 1px 2px rgba(0,0,0,.4);
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

/* Services */
.ow-services { display: inline-flex; gap: 6px; flex-wrap: wrap; }
.ow-svc {
  padding: 2px 6px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,.12);
  background: rgba(255,255,255,.04);
  font-size: 12px;
}
`;
