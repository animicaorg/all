import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNetwork } from "../../state/network";

type JsonRpcRequest = {
  jsonrpc: "2.0";
  id: number | string;
  method: string;
  params?: any[];
};

const RPC_ID = "studio-web";

// --- RPC helper ---
async function rpcCall<T>(rpcUrl: string, method: string, params?: any[]): Promise<T> {
  const body: JsonRpcRequest = { jsonrpc: "2.0", id: RPC_ID, method, params };
  const res = await fetch(rpcUrl, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`RPC HTTP ${res.status}`);
  const payload = await res.json();
  if (payload.error) throw new Error(payload.error.message || "RPC error");
  return payload.result as T;
}

// --- Types (loose, to tolerate node variations) ---
type TxView = {
  hash: string;
  from?: string;
  to?: string | null;
  value?: string | number;       // decimal or hex
  nonce?: number;
  gas?: number | string;
  gasPrice?: string | number;
  input?: string;                // hex data (EVM-like naming)
  data?: string;                 // alt name
  chainId?: number;
  blockHash?: string | null;
  blockNumber?: number | null;
  status?: string;               // sometimes included
};

type LogView = {
  address: string;
  topics: string[];
  data: string;
  index?: number;
};

type ReceiptView = {
  transactionHash: string;
  blockHash?: string;
  blockNumber?: number;
  status?: number | string;      // 1|0 or "SUCCESS"/"REVERT"/"OOG"
  gasUsed?: string | number;
  logs?: LogView[];
};

// --- Utils ---
function shortHash(h?: string, size = 12): string {
  if (!h) return "—";
  if (h.length <= size + 2) return h;
  return `${h.slice(0, Math.ceil(size / 2))}…${h.slice(-Math.floor(size / 2))}`;
}

function isHexHash(s: string): boolean {
  return /^0x[0-9a-fA-F]{64}$/.test(s.trim());
}

function isBech32Addr(s: string): boolean {
  // lightweight check for anim1… addresses
  return /^anim1[023456789acdefghjklmnpqrstuvwxyz]{10,}$/i.test(s.trim());
}

function hexToBigIntLike(v?: string | number): bigint | null {
  if (v === undefined || v === null) return null;
  try {
    if (typeof v === "number") return BigInt(v);
    const s = String(v);
    if (s.startsWith("0x")) return BigInt(s);
    return BigInt(s);
  } catch {
    return null;
  }
}

function formatAmount(v?: string | number, unit = "ANIM"): string {
  const bi = hexToBigIntLike(v);
  if (bi === null) return "—";
  // We don't know decimals for this chain; show raw units with separators
  const s = bi.toString();
  return `${s.replace(/\B(?=(\d{3})+(?!\d))/g, ",")} ${unit}`;
}

function bytesLen(hex?: string): number {
  if (!hex || typeof hex !== "string" || !hex.startsWith("0x")) return 0;
  return Math.max(0, (hex.length - 2) / 2);
}

function statusBadge(status?: number | string) {
  const norm =
    typeof status === "number"
      ? status === 1
        ? "SUCCESS"
        : status === 0
        ? "REVERT"
        : String(status)
      : typeof status === "string"
      ? status.toUpperCase()
      : "PENDING";
  let bg = "#e5e7eb",
    fg = "#111827";
  if (norm.includes("SUCCESS")) {
    bg = "#dcfce7";
    fg = "#065f46";
  } else if (norm.includes("REVERT") || norm.includes("FAIL") || norm.includes("OOG")) {
    bg = "#fee2e2";
    fg = "#991b1b";
  } else if (norm.includes("PENDING")) {
    bg = "#fef3c7";
    fg = "#92400e";
  }
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 600,
        background: bg,
        color: fg,
      }}
    >
      {norm}
    </span>
  );
}

// --- Main component ---
export const TxPage: React.FC = () => {
  const { rpcUrl } = useNetwork();
  const [query, setQuery] = useState("");
  const [tx, setTx] = useState<TxView | null>(null);
  const [rcpt, setRcpt] = useState<ReceiptView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Read ?q= from URL on mount
  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    const q = sp.get("q") || sp.get("hash") || "";
    if (q) setQuery(q);
  }, []);

  const doSearch = useCallback(
    async (q?: string) => {
      if (!rpcUrl) return;
      const needle = (q ?? query).trim();
      if (!needle) return;
      setLoading(true);
      setError(null);
      setTx(null);
      setRcpt(null);
      try {
        // For now we support direct tx hash lookups. If the user pasted an address,
        // we could add an address-lookup over recent blocks later.
        if (!isHexHash(needle)) {
          if (isBech32Addr(needle)) {
            throw new Error("Address lookups are not supported here yet. Paste a transaction hash.");
          }
          throw new Error("Please paste a full 32-byte transaction hash (0x…64 hex chars).");
        }
        const t = await rpcCall<TxView | null>(rpcUrl, "tx.getTransactionByHash", [needle]);
        if (!t) throw new Error("Transaction not found.");
        setTx(t);
        const r = await rpcCall<ReceiptView | null>(rpcUrl, "tx.getTransactionReceipt", [needle]);
        if (r) setRcpt(r);
      } catch (e: any) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    },
    [rpcUrl, query]
  );

  // auto search if query came from URL
  useEffect(() => {
    if (query && isHexHash(query)) {
      void doSearch(query);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, rpcUrl]);

  const txDataHex = tx?.data ?? tx?.input ?? "0x";
  const dataBytes = bytesLen(txDataHex);

  return (
    <div style={{ padding: 16, display: "grid", gap: 16 }}>
      <div style={{ display: "grid", gap: 8 }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>Transaction</h1>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input
            placeholder="Paste tx hash (0x…)"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") doSearch();
            }}
            style={{
              minWidth: 360,
              flex: "1 1 360px",
              padding: "10px 12px",
              borderRadius: 8,
              border: "1px solid var(--border, #e5e7eb)",
              fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            }}
          />
          <button
            onClick={() => doSearch()}
            disabled={loading || !query.trim()}
            style={{
              padding: "10px 14px",
              borderRadius: 8,
              border: "1px solid var(--border, #e5e7eb)",
              background: "var(--btn-bg, #fff)",
              cursor: loading ? "default" : "pointer",
              fontWeight: 600,
            }}
          >
            {loading ? "Searching…" : "Search"}
          </button>
        </div>
        {error && (
          <div
            style={{
              padding: 12,
              borderRadius: 8,
              border: "1px solid #fecaca",
              background: "#fef2f2",
              color: "#991b1b",
              fontSize: 14,
            }}
          >
            {error}
          </div>
        )}
      </div>

      {!tx && !loading && !error && (
        <div style={{ color: "var(--muted-fg, #667085)", fontSize: 14 }}>Enter a transaction hash to view details.</div>
      )}

      {tx && (
        <div style={{ display: "grid", gap: 16 }}>
          <section
            style={{
              border: "1px solid var(--card-border, #e5e7eb)",
              borderRadius: 12,
              overflow: "hidden",
              background: "var(--card-bg, #fff)",
            }}
          >
            <header
              style={{
                padding: "12px 14px",
                borderBottom: "1px solid var(--card-border, #e5e7eb)",
                background: "var(--card-head, #f9fafb)",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 8,
                flexWrap: "wrap",
              }}
            >
              <div style={{ fontWeight: 700, fontSize: 14 }}>Overview</div>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                {rcpt ? statusBadge(rcpt.status) : statusBadge("PENDING")}
              </div>
            </header>
            <div style={{ padding: 14, display: "grid", gap: 10 }}>
              <Row k="Hash" v={<code>{tx.hash}</code>} />
              <Row
                k="Block"
                v={
                  tx.blockNumber != null ? (
                    <span>
                      #{tx.blockNumber} · <code>{shortHash(tx.blockHash || undefined)}</code>
                    </span>
                  ) : (
                    <em>Pending</em>
                  )
                }
              />
              <Row k="From" v={tx.from ? <code>{tx.from}</code> : "—"} />
              <Row k="To" v={tx.to ? <code>{tx.to}</code> : <em>Contract creation</em>} />
              <Row k="Value" v={formatAmount(tx.value)} />
              <Row k="Nonce" v={tx.nonce ?? "—"} />
              <Row k="Gas limit" v={tx.gas ?? "—"} />
              <Row k="Gas price" v={formatAmount(tx.gasPrice, "gwei?")} />
              <Row
                k="Data"
                v={
                  dataBytes > 0 ? (
                    <div style={{ display: "grid", gap: 6 }}>
                      <code style={{ wordBreak: "break-all" }}>{txDataHex.slice(0, 66)}{txDataHex.length > 66 ? "…" : ""}</code>
                      <span style={{ color: "var(--muted-fg, #667085)", fontSize: 12 }}>
                        {dataBytes} bytes {txDataHex.length > 66 ? " (truncated)" : ""}
                      </span>
                    </div>
                  ) : (
                    "—"
                  )
                }
              />
            </div>
          </section>

          <section
            style={{
              border: "1px solid var(--card-border, #e5e7eb)",
              borderRadius: 12,
              overflow: "hidden",
              background: "var(--card-bg, #fff)",
            }}
          >
            <header
              style={{
                padding: "12px 14px",
                borderBottom: "1px solid var(--card-border, #e5e7eb)",
                background: "var(--card-head, #f9fafb)",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <div style={{ fontWeight: 700, fontSize: 14 }}>Receipt</div>
              <div style={{ color: "var(--muted-fg, #667085)", fontSize: 12 }}>
                {rcpt ? "Finalized" : "Not available yet"}
              </div>
            </header>
            {rcpt ? (
              <div style={{ padding: 14, display: "grid", gap: 10 }}>
                <Row k="Block" v={rcpt.blockNumber != null ? `#${rcpt.blockNumber}` : "—"} />
                <Row k="Gas used" v={rcpt.gasUsed ?? "—"} />
                <Row k="Logs" v={rcpt.logs ? rcpt.logs.length : 0} />
              </div>
            ) : (
              <div style={{ padding: 14, color: "var(--muted-fg, #667085)" }}>
                No receipt yet. If this tx is pending, check back shortly.
              </div>
            )}
          </section>

          {rcpt?.logs && rcpt.logs.length > 0 && (
            <section
              style={{
                border: "1px solid var(--card-border, #e5e7eb)",
                borderRadius: 12,
                overflow: "hidden",
                background: "var(--card-bg, #fff)",
              }}
            >
              <header
                style={{
                  padding: "12px 14px",
                  borderBottom: "1px solid var(--card-border, #e5e7eb)",
                  background: "var(--card-head, #f9fafb)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <div style={{ fontWeight: 700, fontSize: 14 }}>Logs</div>
                <div style={{ color: "var(--muted-fg, #667085)", fontSize: 12 }}>{rcpt.logs.length} events</div>
              </header>
              <div style={{ display: "grid" }}>
                {rcpt.logs.map((log, i) => (
                  <div
                    key={`${log.address}-${i}-${log.index ?? i}`}
                    style={{
                      padding: 14,
                      borderTop: i === 0 ? "none" : "1px solid var(--card-border, #e5e7eb)",
                      display: "grid",
                      gap: 8,
                    }}
                  >
                    <Row k="Index" v={log.index ?? i} />
                    <Row k="Address" v={<code>{log.address}</code>} />
                    <Row
                      k="Topics"
                      v={
                        <div style={{ display: "grid", gap: 4 }}>
                          {log.topics.map((t, j) => (
                            <code key={`${t}-${j}`} style={{ wordBreak: "break-all" }}>
                              {t}
                            </code>
                          ))}
                          {log.topics.length === 0 && <span>—</span>}
                        </div>
                      }
                    />
                    <Row
                      k="Data"
                      v={
                        log.data && log.data !== "0x" ? (
                          <div style={{ display: "grid", gap: 4 }}>
                            <code style={{ wordBreak: "break-all" }}>
                              {log.data.length > 84 ? `${log.data.slice(0, 84)}…` : log.data}
                            </code>
                            <span style={{ color: "var(--muted-fg, #667085)", fontSize: 12 }}>
                              {bytesLen(log.data)} bytes
                            </span>
                          </div>
                        ) : (
                          "—"
                        )
                      }
                    />
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
};

const Row: React.FC<{ k: React.ReactNode; v: React.ReactNode }> = ({ k, v }) => (
  <div
    style={{
      display: "grid",
      gridTemplateColumns: "180px 1fr",
      gap: 12,
      alignItems: "baseline",
    }}
  >
    <div style={{ color: "var(--muted-fg, #667085)", fontSize: 12, fontWeight: 600 }}>{k}</div>
    <div style={{ fontSize: 14 }}>{v}</div>
  </div>
);

export default TxPage;
