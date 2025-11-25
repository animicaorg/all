import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { classNames } from "../../utils/classnames";
import { shortHash } from "../../utils/format";
import { ago } from "../../utils/time";
import * as DAService from "../../services/da";

// -------------------------------------------------------------------------------------
// Types (kept loose to tolerate different backends)
// -------------------------------------------------------------------------------------
type BlobItem = {
  id?: string;
  commitment?: string; // hex or CID-like
  namespace?: string;
  owner?: string;
  height?: number;
  timestamp?: number; // ms since epoch
  size?: number;
  mime?: string;
};

type BlobsQuery = {
  namespace?: string;
  owner?: string;
  from?: number; // ms
  to?: number; // ms
  limit?: number;
  offset?: number;
};

// -------------------------------------------------------------------------------------
// Constants
// -------------------------------------------------------------------------------------
const REFRESH_MS_DEFAULT = 30_000;

const RANGES = [
  { id: "24h", label: "24h", ms: 24 * 60 * 60 * 1000 },
  { id: "7d", label: "7d", ms: 7 * 24 * 60 * 60 * 1000 },
  { id: "30d", label: "30d", ms: 30 * 24 * 60 * 60 * 1000 },
] as const;

// -------------------------------------------------------------------------------------
// Utils
// -------------------------------------------------------------------------------------
function toNumber(x: any): number | undefined {
  if (x === null || x === undefined) return undefined;
  if (typeof x === "number") return Number.isFinite(x) ? x : undefined;
  if (typeof x === "string") {
    if (/^0x[0-9a-fA-F]+$/.test(x)) {
      try {
        return Number(BigInt(x));
      } catch {
        return undefined;
      }
    }
    const n = Number(x);
    return Number.isFinite(n) ? n : undefined;
  }
  if (typeof x === "bigint") return Number(x);
  return undefined;
}

function normalizeBlob(x: any): BlobItem {
  const commitment = x?.commitment ?? x?.cid ?? x?.hash ?? x?.id;
  return {
    id: x?.id ?? commitment,
    commitment,
    namespace: x?.namespace ?? x?.ns,
    owner: x?.owner ?? x?.poster ?? x?.address ?? x?.from,
    height: toNumber(x?.height ?? x?.blockHeight),
    timestamp: toNumber(x?.timestamp ?? x?.time ?? x?.postedAt),
    size: toNumber(x?.size ?? x?.length ?? x?.bytes),
    mime: x?.mime ?? x?.contentType,
  };
}

function formatBytes(n?: number): string {
  if (!Number.isFinite(n || NaN)) return "—";
  const v = n as number;
  if (v < 1024) return `${v} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let i = -1;
  let s = v;
  do {
    s /= 1024;
    i++;
  } while (s >= 1024 && i < units.length - 1);
  return `${s.toFixed(s >= 100 ? 0 : s >= 10 ? 1 : 2)} ${units[i]}`;
}

// -------------------------------------------------------------------------------------
// Page
// -------------------------------------------------------------------------------------
export default function DAPage() {
  const [range, setRange] = useState<(typeof RANGES)[number]>(RANGES[1]); // default 7d
  const [namespace, setNamespace] = useState("");
  const [owner, setOwner] = useState("");
  const [page, setPage] = useState(0);
  const pageSize = 50;

  const [rows, setRows] = useState<BlobItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | undefined>();

  const [proofView, setProofView] = useState<{ commitment: string; json: any } | null>(null);

  const timer = useRef<number | undefined>(undefined);

  const refresh = useCallback(async () => {
    setErr(undefined);
    setLoading(true);
    try {
      const now = Date.now();
      const from = now - range.ms;
      const q: BlobsQuery = {
        namespace: namespace || undefined,
        owner: owner || undefined,
        from,
        to: now,
        limit: pageSize,
        offset: page * pageSize,
      };

      // Flexible adapters:
      // - prefer listBlobs(options)
      // - fallback to getBlobs(options) or fetchBlobs(options)
      let resp: any =
        (await (DAService as any).listBlobs?.(q)) ??
        (await (DAService as any).getBlobs?.(q)) ??
        (await (DAService as any).fetchBlobs?.(q)) ??
        [];

      // Some APIs return { items, total }
      if (resp && Array.isArray(resp.items)) {
        resp = resp.items;
      }

      const items: BlobItem[] = (Array.isArray(resp) ? resp : []).map(normalizeBlob);
      setRows(items);
    } catch (e: any) {
      setErr(e?.message || String(e));
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [namespace, owner, page, pageSize, range.ms]);

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range, namespace, owner, page]);

  useEffect(() => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(refresh, REFRESH_MS_DEFAULT);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [refresh]);

  // Aggregates
  const totals = useMemo(() => {
    const count = rows.length;
    const bytes = rows.reduce((a, b) => a + (b.size || 0), 0);
    const uniqueNamespaces = new Set(rows.map((r) => r.namespace || "")).size;
    const maxHeight = rows.reduce((m, r) => Math.max(m, r.height || 0), 0);
    return { count, bytes, uniqueNamespaces, maxHeight };
  }, [rows]);

  // Actions
  const openProof = useCallback(async (commitment: string) => {
    try {
      const proof =
        (await (DAService as any).getProof?.(commitment)) ??
        (await (DAService as any).fetchProof?.(commitment)) ??
        (await (DAService as any).proofForCommitment?.(commitment));
      setProofView({ commitment, json: proof ?? { error: "No proof returned" } });
    } catch (e: any) {
      setProofView({ commitment, json: { error: e?.message || String(e) } });
    }
  }, []);

  const downloadBlob = useCallback(async (item: BlobItem) => {
    const c = item.commitment!;
    const url =
      (DAService as any).blobUrl?.(c) ??
      (DAService as any).getBlobUrl?.(c) ??
      (DAService as any).makeBlobUrl?.(c);

    if (typeof url === "string") {
      window.open(url, "_blank", "noopener,noreferrer");
      return;
    }

    // Try to fetch bytes and download
    try {
      const res: ArrayBuffer | Uint8Array | undefined =
        (await (DAService as any).getBlob?.(c)) ??
        (await (DAService as any).fetchBlob?.(c));
      if (!res) throw new Error("No blob bytes returned");
      const bytes = res instanceof ArrayBuffer ? new Uint8Array(res) : res;
      const blob = new Blob([bytes], { type: item.mime || "application/octet-stream" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${c.slice(0, 10)}.bin`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4_000);
    } catch (e: any) {
      setErr(e?.message || String(e));
    }
  }, []);

  return (
    <section className="page">
      <header className="page-header">
        <div className="row wrap gap-12 items-end">
          <h1>Data Availability</h1>
          <div className="row gap-8">
            <RangeSelector value={range.id} onChange={(id) => setRange(RANGES.find((r) => r.id === id) || RANGES[0])} />
          </div>
        </div>

        <div className="row wrap gap-10 mt-3 items-end">
          <input
            className="input"
            placeholder="Namespace (optional)"
            value={namespace}
            onChange={(e) => {
              setPage(0);
              setNamespace(e.target.value.trim());
            }}
            spellCheck={false}
          />
          <input
            className="input"
            placeholder="Owner / Poster (optional)"
            value={owner}
            onChange={(e) => {
              setPage(0);
              setOwner(e.target.value.trim());
            }}
            spellCheck={false}
          />
          <button className="btn" onClick={() => refresh()} disabled={loading}>
            Refresh
          </button>
          {err ? <div className="alert warn">{err}</div> : null}
        </div>

        <section className="grid cols-4 gap-12 mt-3">
          <StatCard label="Blobs" value={fmtInt(totals.count)} />
          <StatCard label="Total size" value={formatBytes(totals.bytes)} />
          <StatCard label="Namespaces" value={fmtInt(totals.uniqueNamespaces)} />
          <StatCard label="Latest height" value={fmtInt(totals.maxHeight)} />
        </section>
      </header>

      <div className="table-wrap mt-3">
        <table className="table">
          <thead>
            <tr>
              <th>Commitment</th>
              <th>Namespace</th>
              <th>Owner</th>
              <th className="right">Size</th>
              <th>Height</th>
              <th>Posted</th>
              <th className="right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.id}`}>
                <td className="mono" title={r.commitment}>
                  {r.commitment ? shortHash(r.commitment, 12) : "—"}
                </td>
                <td className="mono" title={r.namespace || undefined}>{r.namespace || "—"}</td>
                <td className="mono" title={r.owner || undefined}>{r.owner ? shortHash(r.owner, 10) : "—"}</td>
                <td className="right mono">{formatBytes(r.size)}</td>
                <td className="mono">{Number.isFinite(r.height || NaN) ? r.height : "—"}</td>
                <td title={r.timestamp ? new Date(r.timestamp).toISOString() : undefined}>
                  {r.timestamp ? ago(r.timestamp) : "—"}
                </td>
                <td className="right">
                  <div className="row gap-6 justify-end">
                    {r.commitment ? (
                      <>
                        <button className="btn tiny" onClick={() => downloadBlob(r)}>Get</button>
                        <button className="btn tiny" onClick={() => openProof(r.commitment!)}>Proof</button>
                        <button
                          className="btn tiny"
                          onClick={() => navigator.clipboard?.writeText(r.commitment!)}
                          title="Copy commitment"
                        >
                          Copy
                        </button>
                      </>
                    ) : (
                      <span className="dim">—</span>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {!rows.length && !loading ? (
              <tr>
                <td className="center dim" colSpan={7}>
                  No blobs found for the selected filters.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <Pagination page={page} setPage={setPage} canNext={rows.length >= pageSize} loading={loading} />

      <ProofModal view={proofView} onClose={() => setProofView(null)} />
    </section>
  );
}

// -------------------------------------------------------------------------------------
// UI bits
// -------------------------------------------------------------------------------------
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

function StatCard({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="panel">
      <div className="dim small">{label}</div>
      <div className="h2 mt-1">{value}</div>
    </div>
  );
}

function Pagination({
  page,
  setPage,
  canNext,
  loading,
}: {
  page: number;
  setPage: (f: (p: number) => number) => void;
  canNext: boolean;
  loading: boolean;
}) {
  return (
    <div className="row gap-8 items-center mt-3">
      <button className="btn small" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}>
        Prev
      </button>
      <span className="dim small">Page {page + 1}</span>
      <button className="btn small" onClick={() => setPage((p) => p + 1)} disabled={!canNext}>
        Next
      </button>
      {loading ? <span className="dim small">Loading…</span> : null}
    </div>
  );
}

function ProofModal({
  view,
  onClose,
}: {
  view: { commitment: string; json: any } | null;
  onClose: () => void;
}) {
  if (!view) return null;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="row space-between items-center">
          <h3 className="m-0">DA Proof</h3>
          <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <div className="mt-2 small dim">Commitment: <span className="mono">{view.commitment}</span></div>
        <pre className="code mt-2" style={{ maxHeight: 400, overflow: "auto" }}>
{JSON.stringify(view.json, null, 2)}
        </pre>
        <footer className="row justify-end mt-2">
          <button className="btn" onClick={onClose}>Close</button>
        </footer>
      </div>
    </div>
  );
}

function fmtInt(n?: number): string {
  if (!Number.isFinite(n || NaN)) return "—";
  const v = n as number;
  if (Math.abs(v) >= 1_000_000_000) return (v / 1_000_000_000).toFixed(2) + "B";
  if (Math.abs(v) >= 1_000_000) return (v / 1_000_000).toFixed(2) + "M";
  if (Math.abs(v) >= 10_000) return Math.round(v / 1_000) + "k";
  return String(Math.round(v));
}
