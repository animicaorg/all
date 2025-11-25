import React, { useEffect, useMemo, useState } from "react";
// If your app exposes the network store, use it. Fallback to env when absent.
import { useNetwork } from "../../state/network";

type JsonRpcRequest = {
  jsonrpc: "2.0";
  id: number | string;
  method: string;
  params?: any[];
};

type Head = { number?: number; height?: number; hash?: string; timestamp?: number };

type BlockHeader = {
  number?: number;
  height?: number;
  hash: string;
  timestamp?: number;
};

type ProofKind = "hash" | "ai" | "quantum" | "storage" | "vdf" | string;

type ProofAny = {
  type_id?: number | string;
  kind?: ProofKind;
  psi?: number;          // µ-nats or units; we only use for relative mix
  weight?: number;       // fallback field name
};

type BlockLike = {
  header: BlockHeader;
  txs?: any[];           // some nodes return "txs"
  transactions?: any[];  // others may return "transactions"
  proofs?: ProofAny[];   // optional list of attached proofs for PoIES mix
  poies?: {
    breakdown?: Record<string, number>; // if server already computed a mix
  };
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

function shortHash(h?: string, size = 10): string {
  if (!h) return "—";
  if (h.length <= size + 2) return h;
  return `${h.slice(0, Math.ceil(size / 2))}…${h.slice(-Math.floor(size / 2))}`;
}

function asHeight(h?: number): number {
  return typeof h === "number" && Number.isFinite(h) ? h : 0;
}

function toPct(n: number, total: number): number {
  if (total <= 0) return 0;
  return Math.max(0, Math.min(100, (n / total) * 100));
}

function proofKindNormalize(p: ProofAny): ProofKind {
  if (p.kind) return p.kind;
  const t = typeof p.type_id === "string" ? p.type_id.toLowerCase() : p.type_id;
  if (t === 0 || t === "hash" || t === "hashshare") return "hash";
  if (t === 1 || t === "ai" || t === "ai_proof") return "ai";
  if (t === 2 || t === "quantum" || t === "quantum_proof") return "quantum";
  if (t === 3 || t === "storage" || t === "storage_heartbeat") return "storage";
  if (t === 4 || t === "vdf" || t === "vdf_proof") return "vdf";
  return String(t || "unknown");
}

type Mix = { hash: number; ai: number; quantum: number; storage: number; vdf: number; total: number };

function computePoiesMix(block: BlockLike): Mix {
  // If the server sent a ready-made breakdown, use it
  const b = block.poies?.breakdown;
  if (b) {
    const mix: Mix = {
      hash: b.hash || b.hashshare || 0,
      ai: b.ai || 0,
      quantum: b.quantum || 0,
      storage: b.storage || 0,
      vdf: b.vdf || 0,
      total: 0,
    };
    mix.total = mix.hash + mix.ai + mix.quantum + mix.storage + mix.vdf;
    return mix;
  }
  // Otherwise, aggregate from proofs if present.
  const out: Mix = { hash: 0, ai: 0, quantum: 0, storage: 0, vdf: 0, total: 0 };
  const proofs = block.proofs || [];
  for (const p of proofs) {
    const kind = proofKindNormalize(p);
    const val = typeof p.psi === "number" ? p.psi : typeof p.weight === "number" ? p.weight : 1;
    if (kind === "hash") out.hash += val;
    else if (kind === "ai") out.ai += val;
    else if (kind === "quantum") out.quantum += val;
    else if (kind === "storage") out.storage += val;
    else if (kind === "vdf") out.vdf += val;
    out.total += val;
  }
  return out;
}

const Legend: React.FC = () => {
  const item = (color: string, label: string) => (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }} key={label}>
      <span style={{ width: 12, height: 12, borderRadius: 2, background: color, display: "inline-block" }} />
      <span style={{ fontSize: 12, color: "var(--muted-fg, #667085)" }}>{label}</span>
    </div>
  );
  return (
    <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
      {item("var(--mix-hash, #94a3b8)", "Hash")}
      {item("var(--mix-ai, #60a5fa)", "AI")}
      {item("var(--mix-quantum, #a78bfa)", "Quantum")}
      {item("var(--mix-storage, #34d399)", "Storage")}
      {item("var(--mix-vdf, #f59e0b)", "VDF")}
    </div>
  );
};

const MixBar: React.FC<{ mix: Mix }> = ({ mix }) => {
  const total = mix.total || mix.hash + mix.ai + mix.quantum + mix.storage + mix.vdf;
  const seg = (value: number, color: string, title: string) => {
    const w = toPct(value, total);
    if (w <= 0) return null;
    return (
      <div
        key={title}
        title={`${title}: ${value.toFixed(2)} (${w.toFixed(1)}%)`}
        style={{ width: `${w}%`, height: 8, background: color }}
      />
    );
  };
  return (
    <div style={{ display: "flex", width: "100%", height: 8, overflow: "hidden", borderRadius: 4, background: "var(--bar-bg, #e5e7eb)" }}>
      {seg(mix.hash, "var(--mix-hash, #94a3b8)", "Hash")}
      {seg(mix.ai, "var(--mix-ai, #60a5fa)", "AI")}
      {seg(mix.quantum, "var(--mix-quantum, #a78bfa)", "Quantum")}
      {seg(mix.storage, "var(--mix-storage, #34d399)", "Storage")}
      {seg(mix.vdf, "var(--mix-vdf, #f59e0b)", "VDF")}
    </div>
  );
};

const PAGE_SIZE = 20;

export const BlocksPage: React.FC = () => {
  const { rpcUrl } = useNetwork();
  const [head, setHead] = useState<Head | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [blocks, setBlocks] = useState<BlockLike[]>([]);

  const headHeight = useMemo(() => (head?.number ?? head?.height ?? 0), [head]);

  const refresh = async () => {
    if (!rpcUrl) return;
    setError(null);
    setLoading(true);
    try {
      const h = await rpcCall<Head>(rpcUrl, "chain.getHead", []);
      setHead(h);
      const height = asHeight(h.number ?? h.height);
      const from = Math.max(0, height - PAGE_SIZE + 1);
      const nums = Array.from({ length: Math.min(PAGE_SIZE, height + 1) }, (_, i) => height - i);
      const calls = nums.map((n) =>
        rpcCall<BlockLike>(rpcUrl, "chain.getBlockByNumber", [n, { includeTxs: false, includeReceipts: false }]).catch(() => null)
      );
      const results = await Promise.all(calls);
      const filtered = results.filter((x): x is BlockLike => !!x);
      setBlocks(filtered);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    // light polling; WS subscription (newHeads) will upgrade this later
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rpcUrl]);

  return (
    <div style={{ padding: 16, display: "grid", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <div style={{ display: "grid", gap: 4 }}>
          <h1 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>Blocks</h1>
          <div style={{ color: "var(--muted-fg, #667085)", fontSize: 13 }}>
            Head: <strong>{headHeight}</strong> {head?.hash ? `· ${shortHash(head.hash)}` : ""}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Legend />
          <button
            onClick={refresh}
            disabled={loading}
            style={{
              padding: "8px 12px",
              borderRadius: 8,
              border: "1px solid var(--border, #e5e7eb)",
              background: "var(--btn-bg, #fff)",
              cursor: "pointer",
            }}
          >
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
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

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "separate", borderSpacing: 0 }}>
          <thead>
            <tr>
              {["Height", "Hash", "Txs", "PoIES Mix", "Time"].map((h, i) => (
                <th
                  key={h}
                  style={{
                    textAlign: i === 2 ? "right" : "left",
                    fontWeight: 600,
                    fontSize: 12,
                    color: "var(--muted-fg, #667085)",
                    padding: "8px 12px",
                    borderBottom: "1px solid var(--table-border, #e5e7eb)",
                    background: "var(--table-head, #f9fafb)",
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {blocks.map((b) => {
              const h = b.header;
              const height = asHeight(h.number ?? h.height);
              const txCount = Array.isArray(b.txs) ? b.txs.length : Array.isArray(b.transactions) ? b.transactions.length : 0;
              const mix = computePoiesMix(b);
              const ts = typeof h.timestamp === "number" ? new Date(h.timestamp * 1000) : null;
              return (
                <tr key={h.hash} style={{ borderBottom: "1px solid var(--table-border, #e5e7eb)" }}>
                  <td style={{ padding: "10px 12px", whiteSpace: "nowrap" }}>{height}</td>
                  <td style={{ padding: "10px 12px", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>
                    {shortHash(h.hash, 14)}
                  </td>
                  <td style={{ padding: "10px 12px", textAlign: "right" }}>{txCount}</td>
                  <td style={{ padding: "10px 12px", minWidth: 220 }}>
                    {mix.total > 0 ? <MixBar mix={mix} /> : <span style={{ color: "#9ca3af" }}>n/a</span>}
                  </td>
                  <td style={{ padding: "10px 12px", whiteSpace: "nowrap", color: "var(--muted-fg, #667085)" }}>
                    {ts ? ts.toLocaleString() : "—"}
                  </td>
                </tr>
              );
            })}
            {blocks.length === 0 && !loading && (
              <tr>
                <td colSpan={5} style={{ padding: 16, color: "var(--muted-fg, #667085)" }}>
                  No blocks yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <style>{`
        /* Optional CSS variables so theming can override segment colors */
        :root {
          --mix-hash: #94a3b8;
          --mix-ai: #60a5fa;
          --mix-quantum: #a78bfa;
          --mix-storage: #34d399;
          --mix-vdf: #f59e0b;
        }
      `}</style>
    </div>
  );
};

export default BlocksPage;
