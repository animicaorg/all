import React, { useEffect, useMemo } from "react";
import { Link } from "react-router-dom";
import { useExplorerStore } from "../../state/store";
import { shortHash } from "../../utils/format";
import { ago } from "../../utils/time";

type TxRow = {
  hash: string;
  from?: string;
  to?: string | null;
  method?: string | null;
  status?: "success" | "failed" | "pending";
  timestamp?: number; // ms epoch
  time?: number;      // fallback: some backends use 'time'
  value?: string | number | bigint | null;
};

const MAX_ROWS = 10;

export default function RecentTxs(): JSX.Element {
  // Select state with graceful fallbacks — keeps this component resilient
  const txs: TxRow[] =
    useExplorerStore((s) => (s.txs?.recent ?? s.txs?.items ?? [])) || [];
  const loading = useExplorerStore((s) => s.txs?.loading ?? false);
  const start = useExplorerStore((s) => s.txs?.start ?? null);
  const refresh = useExplorerStore((s) => s.txs?.refresh ?? null);

  // Start live feed if available; otherwise, do a one-shot refresh.
  useEffect(() => {
    if (typeof start === "function") {
      start();
    } else if (typeof refresh === "function" && txs.length === 0) {
      refresh();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const rows = useMemo(() => {
    const norm = (txs || []).map((t) => ({
      hash: t.hash,
      from: t.from,
      to: t.to ?? null,
      method: t.method ?? null,
      status: t.status ?? "success",
      // Prefer 'timestamp' (ms) then 'time' (ms), else undefined
      ts: typeof t.timestamp === "number" ? t.timestamp : (typeof t.time === "number" ? t.time : undefined),
      value: t.value ?? null,
    }));
    // Most-recent first if timestamps exist; otherwise keep incoming order.
    norm.sort((a, b) => (b.ts ?? 0) - (a.ts ?? 0));
    return norm.slice(0, MAX_ROWS);
  }, [txs]);

  return (
    <section aria-labelledby="recent-txs-title" className="card">
      <div className="card-header">
        <h2 id="recent-txs-title" className="card-title">Recent Transactions</h2>
        <div className="card-actions">
          <Link to="/tx" className="link">See all</Link>
        </div>
      </div>

      <div className="card-body">
        {loading && rows.length === 0 ? (
          <div className="skeleton-list" role="status" aria-live="polite">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="skeleton-row" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <div className="empty">
            <p>No recent transactions.</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: "28%" }}>Hash</th>
                  <th style={{ width: "28%" }}>From → To</th>
                  <th style={{ width: "24%" }}>Method</th>
                  <th style={{ width: "10%" }}>Age</th>
                  <th style={{ width: "10%" }} className="text-right">Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((tx) => (
                  <tr key={tx.hash}>
                    <td className="mono">
                      <Link to={`/tx/${tx.hash}`} className="link">
                        {shortHash(tx.hash)}
                      </Link>
                    </td>
                    <td className="mono">
                      <span title={tx.from || ""}>{shortHash(tx.from || "")}</span>
                      <span className="dim"> → </span>
                      {tx.to ? (
                        <span title={tx.to}>{shortHash(tx.to)}</span>
                      ) : (
                        <span className="dim">—</span>
                      )}
                    </td>
                    <td>
                      {tx.method ? <code className="badge">{tx.method}</code> : <span className="dim">—</span>}
                    </td>
                    <td title={tx.ts ? new Date(tx.ts).toLocaleString() : undefined}>
                      {tx.ts ? ago(tx.ts) : "—"}
                    </td>
                    <td className="text-right">
                      <StatusPill status={tx.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}

function StatusPill({ status }: { status?: TxRow["status"] }) {
  const s = status ?? "success";
  const label =
    s === "pending" ? "Pending" : s === "failed" ? "Failed" : "Success";
  return (
    <span
      className={
        "pill " +
        (s === "pending"
          ? "pill-warn"
          : s === "failed"
          ? "pill-danger"
          : "pill-ok")
      }
      aria-label={`status: ${label}`}
      title={label}
    >
      {label}
    </span>
  );
}
