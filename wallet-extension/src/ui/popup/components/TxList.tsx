import React, { useEffect, useMemo, useState } from "react";

/**
 * Minimal Tx types to render the list.
 * Kept compatible with src/background/tx/types.ts (fields we actually read).
 */
export type TxStatus = "pending" | "confirmed" | "failed" | "dropped";
export type TxKind = "transfer" | "call" | "deploy" | "unknown";

export type TxSummary = {
  hash: string;
  kind?: TxKind;
  from?: string;
  to?: string | null;
  value?: string | number; // string (wei-like) or number (human units)
  fee?: string | number;
  createdAt?: number; // ms epoch
  status: TxStatus;
  error?: string;
  method?: string; // for calls: contract method name
  preview?: string; // short human hint, if background provides it
};

type BgListResp =
  | { ok: true; items: TxSummary[] }
  | { ok: false; error: string };

type Props = {
  /** If set, only show transactions involving this address */
  address?: string;
  /** Limit number of txs to show (default 10) */
  limit?: number;
  /** Called when a row is clicked */
  onSelect?: (tx: TxSummary) => void;
  /** Optional explorer base URL; if provided, a "View" link is shown */
  explorerBaseUrl?: string;
  /** Compact mode (denser rows) */
  compact?: boolean;
  /** If provided, skip background fetch and render these items */
  items?: TxSummary[];
};

/** MV3 background message helper */
async function bgListRecent(limit: number, address?: string): Promise<BgListResp | null> {
  const chromeAny = (globalThis as any).chrome;
  if (!chromeAny?.runtime?.id || !chromeAny?.runtime?.sendMessage) return null;

  // Try preferred route; fall back to a legacy name if needed.
  const payloads = [
    { kind: "tx:listRecent", limit, address },
    { kind: "tx:list", limit, address },
  ];

  for (const payload of payloads) {
    try {
      // eslint-disable-next-line no-await-in-loop
      const resp = await new Promise<BgListResp>((resolve) => {
        chromeAny.runtime.sendMessage(payload, (r: BgListResp) =>
          resolve(r ?? { ok: false, error: "no response" })
        );
      });
      if (resp?.ok || payload === payloads[payloads.length - 1]) return resp;
    } catch {
      // continue to next payload
    }
  }
  return null;
}

/** Listen for background updates that should prompt a refresh. */
function useBgRefreshSignal(onSignal: () => void) {
  useEffect(() => {
    const chromeAny = (globalThis as any).chrome;
    if (!chromeAny?.runtime?.onMessage) return;

    const handler = (msg: any) => {
      // Background may emit these notifications:
      // - { kind: "tx:update", hash, status }
      // - { kind: "head:new", height }
      // - { kind: "ws:pendingTx" }
      if (!msg || typeof msg !== "object") return;
      const k = msg.kind;
      if (k === "tx:update" || k === "head:new" || k === "ws:pendingTx") {
        onSignal();
      }
    };
    chromeAny.runtime.onMessage.addListener(handler);
    return () => chromeAny.runtime.onMessage.removeListener(handler);
  }, [onSignal]);
}

export default function TxList({
  address,
  limit = 10,
  onSelect,
  explorerBaseUrl,
  compact,
  items,
}: Props) {
  const [loading, setLoading] = useState<boolean>(!items);
  const [err, setErr] = useState<string | null>(null);
  const [data, setData] = useState<TxSummary[]>(items ?? []);

  // Pull from background (unless items prop is provided)
  const fetchList = React.useCallback(() => {
    if (items) return; // controlled externally
    setLoading(true);
    setErr(null);
    void bgListRecent(limit, address)
      .then((resp) => {
        if (!resp) return setErr("Background not available");
        if (!resp.ok) return setErr(resp.error || "Failed to load transactions");
        setData(resp.items ?? []);
      })
      .catch(() => setErr("Failed to load transactions"))
      .finally(() => setLoading(false));
  }, [items, limit, address]);

  useEffect(() => {
    fetchList();
  }, [fetchList]);

  useBgRefreshSignal(fetchList);

  useEffect(() => {
    if (items) setData(items);
  }, [items]);

  const rows = useMemo(() => data.slice(0, limit), [data, limit]);

  return (
    <div className={["ami-txlist", compact ? "ami-compact" : ""].join(" ").trim()}>
      <div className="ami-txlist-head">
        <div className="ami-title">Recent activity</div>
        {loading ? <span className="ami-spinner" aria-label="Loading" /> : null}
      </div>

      {err ? (
        <div className="ami-error" role="alert">{err}</div>
      ) : rows.length === 0 && !loading ? (
        <div className="ami-empty">No recent transactions.</div>
      ) : (
        <ul className="ami-items" role="list">
          {rows.map((tx) => (
            <li
              key={tx.hash}
              className={["ami-item", `st-${tx.status}`].join(" ")}
              onClick={() => onSelect?.(tx)}
              role={onSelect ? "button" : "listitem"}
              tabIndex={onSelect ? 0 : -1}
              onKeyDown={(e) => {
                if (onSelect && (e.key === "Enter" || e.key === " ")) onSelect(tx);
              }}
            >
              <div className="ami-kind">
                <KindBadge kind={tx.kind} />
              </div>
              <div className="ami-main">
                <div className="ami-line1">
                  <span className="ami-hash" title={tx.hash}>
                    {shortHash(tx.hash)}
                  </span>
                  <StatusBadge status={tx.status} />
                </div>
                <div className="ami-line2">
                  {renderPreview(tx)}
                </div>
              </div>
              <div className="ami-meta">
                <span className="ami-ago" title={tx.createdAt ? new Date(tx.createdAt).toString() : ""}>
                  {tx.createdAt ? timeAgo(tx.createdAt) : ""}
                </span>
                {explorerBaseUrl ? (
                  <a
                    className="ami-view"
                    href={`${trimSlash(explorerBaseUrl)}/tx/${tx.hash}`}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(e) => e.stopPropagation()}
                  >
                    View
                  </a>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}

      <style>{styles}</style>
    </div>
  );
}

function KindBadge({ kind }: { kind?: TxKind }) {
  const k = (kind ?? "unknown").toLowerCase() as TxKind;
  const label =
    k === "transfer" ? "Transfer" :
    k === "call" ? "Call" :
    k === "deploy" ? "Deploy" : "Tx";
  return <span className={`ami-badge kind-${k}`}>{label}</span>;
}

function StatusBadge({ status }: { status: TxStatus }) {
  const s = status.toLowerCase() as TxStatus;
  const label =
    s === "pending" ? "Pending" :
    s === "confirmed" ? "Confirmed" :
    s === "failed" ? "Failed" : "Dropped";
  return <span className={`ami-badge st-${s}`}>{label}</span>;
}

function renderPreview(tx: TxSummary) {
  if (tx.preview) return <span className="ami-dim">{tx.preview}</span>;

  const from = shortAddr(tx.from || "");
  const to = tx.to ? shortAddr(tx.to) : "(deploy)";
  const val = tx.value != null ? formatAmount(tx.value) : "";
  switch (tx.kind) {
    case "transfer":
      return (
        <span className="ami-dim">
          {from} → {to} {val ? <em className="ami-val">{val}</em> : null}
        </span>
      );
    case "call":
      return (
        <span className="ami-dim">
          {from} → {to} {tx.method ? <em className="ami-val">{tx.method}()</em> : null}
        </span>
      );
    case "deploy":
      return <span className="ami-dim">{from} deployed a contract</span>;
    default:
      return <span className="ami-dim">{shortHash(tx.hash)}</span>;
  }
}

function shortHash(h: string, left = 8, right = 6) {
  if (!h) return "";
  return h.length > left + right + 2 ? `${h.slice(0, left)}…${h.slice(-right)}` : h;
}

function shortAddr(a: string, left = 7, right = 5) {
  if (!a) return "";
  return a.length > left + right + 2 ? `${a.slice(0, left)}…${a.slice(-right)}` : a;
}

function formatAmount(v: string | number) {
  if (typeof v === "number") return `${v}`;
  // If it's a large integer string (e.g., wei), present a short form.
  if (/^\d+$/.test(v)) {
    // naive: show in 'units' assuming 6 decimals (adjust per chain config in future)
    const dec = 6;
    if (v.length <= dec) return `0.${v.padStart(dec, "0")}`;
    const whole = v.slice(0, -dec);
    const frac = v.slice(-dec, -dec + 2).replace(/0+$/, ""); // 2 dp
    return frac ? `${whole}.${frac}` : whole;
  }
  return v;
}

function timeAgo(ts: number) {
  const s = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  return `${d}d`;
}

function trimSlash(u: string) {
  return u.replace(/\/+$/, "");
}

const styles = `
.ami-txlist { width: 100%; }
.ami-txlist-head { display:flex; align-items:center; gap:.5rem; margin-bottom:.35rem; }
.ami-title { font-weight:600; font-size:.95rem; }
.ami-spinner {
  width: 14px; height: 14px; border-radius: 50%;
  border: 2px solid color-mix(in srgb, var(--ami-primary, #4b7cff) 30%, transparent);
  border-top-color: var(--ami-primary, #4b7cff);
  animation: ami-spin 1s linear infinite;
}
@keyframes ami-spin { to { transform: rotate(360deg); } }

.ami-error {
  color: #b00020; background: color-mix(in srgb, #b00020 8%, transparent);
  border: 1px solid color-mix(in srgb, #b00020 30%, transparent);
  border-radius: 8px; padding: .5rem .6rem; font-size: .9rem;
}
.ami-empty {
  padding:.6rem; border:1px dashed var(--ami-border, rgba(0,0,0,.15));
  border-radius:8px; text-align:center; opacity:.8; font-size:.9rem;
}

.ami-items { list-style:none; padding:0; margin:0; display:flex; flex-direction:column; gap:.35rem; }
.ami-item {
  display:grid;
  grid-template-columns: auto 1fr auto;
  gap:.6rem; align-items:center;
  border:1px solid var(--ami-border, rgba(0,0,0,.1));
  border-radius:10px; padding:.5rem .6rem; background: var(--ami-surface, #fff);
  cursor: default;
}
.ami-item[role="button"] { cursor: pointer; }
.ami-item:hover { border-color: var(--ami-primary, #4b7cff); }
.ami-compact .ami-item { padding:.4rem .5rem; }

.ami-kind { min-width: 82px; display:flex; }
.ami-badge {
  font-size: .75rem; padding: .15rem .4rem; border-radius:999px;
  border:1px solid var(--ami-border, rgba(0,0,0,.15)); background: color-mix(in srgb, var(--ami-surface, #fff) 85%, transparent);
}
.kind-transfer { color:#2459e0; border-color: color-mix(in srgb, #2459e0 30%, transparent); }
.kind-call { color:#8552d7; border-color: color-mix(in srgb, #8552d7 30%, transparent); }
.kind-deploy { color:#2f9d6a; border-color: color-mix(in srgb, #2f9d6a 30%, transparent); }

.st-pending { }
.st-confirmed { }
.st-failed { }
.st-dropped { }

.ami-main { min-width:0; }
.ami-line1 { display:flex; align-items:center; gap:.4rem; }
.ami-hash { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; font-size:.82rem; }
.ami-line2 { font-size:.85rem; opacity:.8; }

.ami-meta { display:flex; align-items:center; gap:.5rem; }
.ami-ago { font-size:.8rem; opacity:.7; }
.ami-view {
  font-size:.8rem; border:1px solid var(--ami-border, rgba(0,0,0,.15));
  border-radius:6px; padding:.2rem .45rem; text-decoration:none;
}
.ami-view:hover { border-color: var(--ami-primary, #4b7cff); }

.ami-dim { opacity:.8; }
.ami-val { margin-left:.25rem; font-style: normal; opacity:.9; }
`;

