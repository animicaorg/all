import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNetwork } from "../../state/network";

// ---------------- RPC helpers ----------------
type JsonRpcRequest = {
  jsonrpc: "2.0";
  id: number | string;
  method: string;
  params?: any[];
};

const RPC_ID = "studio-web";

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

// ---------------- Types (tolerant, to work across node variants) ----------------
type BalanceLike = string | number | { free?: string | number; locked?: string | number; [k: string]: any };

type TxView = {
  hash: string;
  from?: string;
  to?: string | null;
  value?: string | number;
  nonce?: number;
  gas?: number | string;
  gasPrice?: string | number;
  input?: string;
  data?: string;
  chainId?: number;
  blockHash?: string | null;
  blockNumber?: number | null;
  status?: string | number;
  contractAddress?: string | null; // sometimes part of receipt
};

type TxListResponse =
  | { items: TxView[]; next?: string | null }
  | { transactions: TxView[]; cursor?: string | null }
  | TxView[];

type ReceiptView = {
  transactionHash: string;
  status?: number | string;
  contractAddress?: string | null;
};

type ContractsByOwnerResponse =
  | { items: string[]; next?: string | null }
  | { contracts: string[]; cursor?: string | null }
  | string[];

// ---------------- Utils ----------------
function shortHash(h?: string, size = 12): string {
  if (!h) return "—";
  if (h.length <= size + 2) return h;
  return `${h.slice(0, Math.ceil(size / 2))}…${h.slice(-Math.floor(size / 2))}`;
}

function isHexHash(s: string): boolean {
  return /^0x[0-9a-fA-F]+$/.test(s.trim());
}

function isBech32Addr(s: string): boolean {
  // lightweight Animica-style bech32 check (hrp 'anim', but let envs customize)
  return /^[a-z0-9]{1,10}1[023456789acdefghjklmnpqrstuvwxyz]{10,}$/i.test(s.trim());
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
      : "—";
  let bg = "#e5e7eb",
    fg = "#111827";
  if (norm.includes("SUCCESS")) {
    bg = "#dcfce7";
    fg = "#065f46";
  } else if (norm.includes("REVERT") || norm.includes("FAIL") || norm.includes("OOG")) {
    bg = "#fee2e2";
    fg = "#991b1b";
  } else if (norm === "—") {
    bg = "#f3f4f6";
    fg = "#374151";
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

// Normalize various API shapes into arrays/cursors
function normalizeTxList(r: TxListResponse): { items: TxView[]; next?: string | null } {
  if (Array.isArray(r)) return { items: r, next: null };
  if ("items" in r) return { items: r.items, next: r.next ?? null };
  if ("transactions" in r) return { items: r.transactions, next: (r as any).cursor ?? null };
  return { items: [], next: null };
}
function normalizeContracts(r: ContractsByOwnerResponse): { items: string[]; next?: string | null } {
  if (Array.isArray(r)) return { items: r, next: null };
  if ("items" in r) return { items: r.items, next: r.next ?? null };
  if ("contracts" in r) return { items: r.contracts, next: (r as any).cursor ?? null };
  return { items: [], next: null };
}

// ---------------- Component ----------------
export const AddressPage: React.FC = () => {
  const { rpcUrl } = useNetwork();
  const [addr, setAddr] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [balance, setBalance] = useState<BalanceLike | null>(null);
  const [isContract, setIsContract] = useState<boolean | null>(null);

  const [txs, setTxs] = useState<TxView[]>([]);
  const [txCursor, setTxCursor] = useState<string | null>(null);
  const [txExhausted, setTxExhausted] = useState(false);

  const [contracts, setContracts] = useState<string[]>([]);
  const [contractsCursor, setContractsCursor] = useState<string | null>(null);
  const [contractsExhausted, setContractsExhausted] = useState(false);

  // Prefill from URL (?a= or ?addr=)
  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    const q = sp.get("a") || sp.get("addr") || sp.get("address") || "";
    if (q) setAddr(q);
  }, []);

  const addrValid = useMemo(() => isBech32Addr(addr) || isHexHash(addr), [addr]);

  const fetchBalance = useCallback(
    async (address: string): Promise<{ value: BalanceLike; codeNonEmpty: boolean | null }> => {
      if (!rpcUrl) throw new Error("No RPC URL configured");
      // Try Animica-style first
      try {
        const v = await rpcCall<BalanceLike>(rpcUrl, "account.getBalance", [address]);
        // Try detect contract by code
        let codeNonEmpty: boolean | null = null;
        try {
          const code = await rpcCall<string>(rpcUrl, "account.getCode", [address]);
          codeNonEmpty = !!(code && code !== "0x");
        } catch {
          // fallback to eth_getCode for EVM-like nodes
          try {
            const code = await rpcCall<string>(rpcUrl, "eth_getCode", [address, "latest"]);
            codeNonEmpty = !!(code && code !== "0x");
          } catch {
            codeNonEmpty = null;
          }
        }
        return { value: v, codeNonEmpty };
      } catch {
        // Fallback to eth_getBalance
        const v = await rpcCall<string>(rpcUrl, "eth_getBalance", [address, "latest"]);
        let codeNonEmpty: boolean | null = null;
        try {
          const code = await rpcCall<string>(rpcUrl, "eth_getCode", [address, "latest"]);
          codeNonEmpty = !!(code && code !== "0x");
        } catch {
          codeNonEmpty = null;
        }
        return { value: v, codeNonEmpty };
      }
    },
    [rpcUrl]
  );

  const fetchTxs = useCallback(
    async (address: string, cursor?: string | null): Promise<{ items: TxView[]; next?: string | null }> => {
      if (!rpcUrl) throw new Error("No RPC URL configured");
      // Try canonical
      try {
        const res = await rpcCall<TxListResponse>(rpcUrl, "tx.getTransactionsByAddress", [
          { address, limit: 20, cursor: cursor ?? null },
        ]);
        return normalizeTxList(res);
      } catch {
        // Widely-used alternates
        try {
          const res = await rpcCall<TxListResponse>(rpcUrl, "index.getTxsByAddress", [address, 20, cursor ?? null]);
          return normalizeTxList(res);
        } catch {
          // Last-resort: empty
          return { items: [], next: null };
        }
      }
    },
    [rpcUrl]
  );

  const fetchContractsByOwner = useCallback(
    async (address: string, cursor?: string | null): Promise<{ items: string[]; next?: string | null }> => {
      if (!rpcUrl) throw new Error("No RPC URL configured");
      try {
        const res = await rpcCall<ContractsByOwnerResponse>(rpcUrl, "contracts.listByOwner", [
          { owner: address, limit: 20, cursor: cursor ?? null },
        ]);
        return normalizeContracts(res);
      } catch {
        try {
          const res = await rpcCall<ContractsByOwnerResponse>(rpcUrl, "account.getContracts", [
            address,
            20,
            cursor ?? null,
          ]);
          return normalizeContracts(res);
        } catch {
          return { items: [], next: null };
        }
      }
    },
    [rpcUrl]
  );

  const doSearch = useCallback(
    async (q?: string) => {
      const address = (q ?? addr).trim();
      if (!rpcUrl) return;
      if (!address) return;
      if (!isBech32Addr(address) && !isHexHash(address)) {
        setError("Please enter a valid address (bech32 or 0x-hex).");
        return;
      }
      setLoading(true);
      setError(null);
      setBalance(null);
      setIsContract(null);
      setTxs([]);
      setTxCursor(null);
      setTxExhausted(false);
      setContracts([]);
      setContractsCursor(null);
      setContractsExhausted(false);

      try {
        const [{ value, codeNonEmpty }, txList, cList] = await Promise.all([
          fetchBalance(address),
          fetchTxs(address, null),
          fetchContractsByOwner(address, null),
        ]);
        setBalance(value);
        setIsContract(codeNonEmpty);
        setTxs(txList.items);
        setTxCursor(txList.next ?? null);
        setTxExhausted(!txList.next || txList.items.length === 0);
        setContracts(cList.items);
        setContractsCursor(cList.next ?? null);
        setContractsExhausted(!cList.next || cList.items.length === 0);
        // reflect in URL
        const sp = new URLSearchParams(window.location.search);
        sp.set("address", address);
        const url = `${window.location.pathname}?${sp.toString()}`;
        window.history.replaceState({}, "", url);
      } catch (e: any) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    },
    [addr, rpcUrl, fetchBalance, fetchTxs, fetchContractsByOwner]
  );

  // auto search if prefilled
  useEffect(() => {
    if (addr && (isBech32Addr(addr) || isHexHash(addr))) {
      void doSearch(addr);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rpcUrl]);

  const loadMoreTxs = useCallback(async () => {
    if (txExhausted || !txCursor || !rpcUrl || !addr) return;
    setLoading(true);
    try {
      const page = await fetchTxs(addr.trim(), txCursor);
      setTxs((prev) => [...prev, ...page.items]);
      setTxCursor(page.next ?? null);
      setTxExhausted(!page.next || page.items.length === 0);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [addr, rpcUrl, fetchTxs, txCursor, txExhausted]);

  const loadMoreContracts = useCallback(async () => {
    if (contractsExhausted || !contractsCursor || !rpcUrl || !addr) return;
    setLoading(true);
    try {
      const page = await fetchContractsByOwner(addr.trim(), contractsCursor);
      setContracts((prev) => [...prev, ...page.items]);
      setContractsCursor(page.next ?? null);
      setContractsExhausted(!page.next || page.items.length === 0);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [addr, rpcUrl, fetchContractsByOwner, contractsCursor, contractsExhausted]);

  const balanceDisplay = useMemo(() => {
    if (!balance) return "—";
    if (typeof balance === "object" && balance !== null) {
      const free = "free" in balance ? balance.free : undefined;
      const locked = "locked" in balance ? balance.locked : undefined;
      return (
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <span title="Spendable">{formatAmount(free)}</span>
          {locked !== undefined && (
            <span style={{ color: "var(--muted-fg,#667085)" }} title="Locked">
              (+ {formatAmount(locked)} locked)
            </span>
          )}
        </div>
      );
    }
    return formatAmount(balance as any);
  }, [balance]);

  return (
    <div style={{ padding: 16, display: "grid", gap: 16 }}>
      <div style={{ display: "grid", gap: 8 }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>Address</h1>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input
            placeholder="Paste address (anim1… or 0x…)"
            value={addr}
            onChange={(e) => setAddr(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") doSearch();
            }}
            style={{
              minWidth: 420,
              flex: "1 1 420px",
              padding: "10px 12px",
              borderRadius: 8,
              border: "1px solid var(--border, #e5e7eb)",
              fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            }}
          />
          <button
            onClick={() => doSearch()}
            disabled={loading || !addrValid}
            style={{
              padding: "10px 14px",
              borderRadius: 8,
              border: "1px solid var(--border, #e5e7eb)",
              background: "var(--btn-bg, #fff)",
              cursor: loading || !addrValid ? "default" : "pointer",
              fontWeight: 600,
            }}
            title={addrValid ? "Search" : "Enter a valid address"}
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

      {!addr && (
        <div style={{ color: "var(--muted-fg, #667085)", fontSize: 14 }}>
          Enter an address to view balance, transactions, and contracts.
        </div>
      )}

      {addr && (
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
            }}
          >
            <div style={{ fontWeight: 700, fontSize: 14 }}>Overview</div>
          </header>
          <div style={{ padding: 14, display: "grid", gap: 10 }}>
            <Row k="Address" v={<code style={{ wordBreak: "break-all" }}>{addr}</code>} />
            <Row
              k="Type"
              v={
                isContract === null ? (
                  <span style={{ color: "var(--muted-fg,#667085)" }}>Unknown</span>
                ) : isContract ? (
                  <strong>Contract</strong>
                ) : (
                  <span>Externally Owned</span>
                )
              }
            />
            <Row k="Balance" v={balanceDisplay} />
          </div>
        </section>
      )}

      {addr && (
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
            <div style={{ fontWeight: 700, fontSize: 14 }}>Recent Transactions</div>
            <div style={{ color: "var(--muted-fg,#667085)", fontSize: 12 }}>{txs.length} shown</div>
          </header>
          {txs.length === 0 ? (
            <div style={{ padding: 14, color: "var(--muted-fg, #667085)" }}>
              No transactions found for this address.
            </div>
          ) : (
            <div style={{ width: "100%", overflowX: "auto" }}>
              <table
                style={{
                  width: "100%",
                  borderCollapse: "collapse",
                  fontSize: 13,
                }}
              >
                <thead>
                  <tr style={{ textAlign: "left", background: "var(--table-head,#fafafa)" }}>
                    <Th>Hash</Th>
                    <Th>Block</Th>
                    <Th>From</Th>
                    <Th>To</Th>
                    <Th>Value</Th>
                    <Th>Data</Th>
                    <Th>Status</Th>
                  </tr>
                </thead>
                <tbody>
                  {txs.map((t, i) => {
                    const href = `/explorer/tx?q=${encodeURIComponent(t.hash)}`;
                    const dataHex = t.data ?? t.input ?? "0x";
                    const dlen = bytesLen(dataHex);
                    return (
                      <tr key={`${t.hash}-${i}`} style={{ borderTop: "1px solid var(--row,#eee)" }}>
                        <Td>
                          <a href={href} style={{ textDecoration: "none" }}>
                            <code>{shortHash(t.hash, 16)}</code>
                          </a>
                        </Td>
                        <Td>{t.blockNumber != null ? `#${t.blockNumber}` : <em>Pending</em>}</Td>
                        <Td>
                          {t.from ? <code title={t.from}>{shortHash(t.from, 14)}</code> : "—"}
                        </Td>
                        <Td>
                          {t.to ? (
                            <code title={t.to}>{shortHash(t.to, 14)}</code>
                          ) : (
                            <em>Contract create</em>
                          )}
                        </Td>
                        <Td>{formatAmount(t.value)}</Td>
                        <Td>{dlen ? `${dlen} B` : "—"}</Td>
                        <Td>{statusBadge(t.status)}</Td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <div style={{ padding: 12, display: "flex", justifyContent: "center" }}>
            <button
              onClick={loadMoreTxs}
              disabled={loading || txExhausted || !txCursor}
              style={{
                padding: "8px 12px",
                borderRadius: 8,
                border: "1px solid var(--border,#e5e7eb)",
                background: "var(--btn-bg,#fff)",
                cursor: loading || txExhausted || !txCursor ? "default" : "pointer",
                fontWeight: 600,
                fontSize: 13,
              }}
            >
              {txExhausted || !txCursor ? "No more" : loading ? "Loading…" : "Load more"}
            </button>
          </div>
        </section>
      )}

      {addr && (
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
            <div style={{ fontWeight: 700, fontSize: 14 }}>Contracts</div>
            <div style={{ color: "var(--muted-fg,#667085)", fontSize: 12 }}>{contracts.length} shown</div>
          </header>
          {contracts.length === 0 ? (
            <div style={{ padding: 14, color: "var(--muted-fg, #667085)" }}>
              No contracts found for this owner (or the node does not index this relation).
            </div>
          ) : (
            <div style={{ padding: 8, display: "grid" }}>
              {contracts.map((c, i) => (
                <div
                  key={`${c}-${i}`}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "10px 8px",
                    borderTop: i === 0 ? "none" : "1px solid var(--row,#eee)",
                  }}
                >
                  <code style={{ wordBreak: "break-all" }}>{c}</code>
                  <a
                    href={`/explorer/address?address=${encodeURIComponent(c)}`}
                    style={{ fontSize: 12, textDecoration: "none" }}
                  >
                    Open
                  </a>
                </div>
              ))}
            </div>
          )}
          <div style={{ padding: 12, display: "flex", justifyContent: "center" }}>
            <button
              onClick={loadMoreContracts}
              disabled={loading || contractsExhausted || !contractsCursor}
              style={{
                padding: "8px 12px",
                borderRadius: 8,
                border: "1px solid var(--border,#e5e7eb)",
                background: "var(--btn-bg,#fff)",
                cursor: loading || contractsExhausted || !contractsCursor ? "default" : "pointer",
                fontWeight: 600,
                fontSize: 13,
              }}
            >
              {contractsExhausted || !contractsCursor ? "No more" : loading ? "Loading…" : "Load more"}
            </button>
          </div>
        </section>
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

const Th: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <th style={{ padding: "10px 8px", fontSize: 12, color: "var(--muted-fg,#667085)", fontWeight: 700 }}>{children}</th>
);
const Td: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <td style={{ padding: "10px 8px", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>{children}</td>
);

export default AddressPage;
