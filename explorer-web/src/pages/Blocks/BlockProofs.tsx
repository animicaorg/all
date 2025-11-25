import React, { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useExplorerStore } from "../../state/store";
import Code from "../../components/Code";
import { shortHash } from "../../utils/format";

// Optional shared types (fallback to 'any' if not present during build)
type ProofSummary = {
  id?: string;                 // canonical proof id/hash
  type?: string;               // "hashshare" | "ai" | "quantum" | "storage" | "vdf" | ...
  weight?: number;             // contribution weight
  sizeBytes?: number;          // blob size (if any)
  provider?: string | string[];// prover/provider id(s)
  verified?: boolean;          // has been verified
  // Common cross-links / hints
  jobId?: string;              // AI/Quantum job id
  commitment?: string;         // DA commitment root
  nmtRoot?: string;            // namespaced Merkle tree root (DA)
  vdf?: { tau?: number; y?: string } | null; // VDF params/result
  shares?: number;             // HashShare count
  meta?: Record<string, any>;  // backend-specific extras
  [k: string]: any;
};

const TYPE_LABEL: Record<string, string> = {
  hashshare: "HashShare",
  ai: "AI",
  quantum: "Quantum",
  storage: "Storage",
  vdf: "VDF",
};

function normalizeType(p: ProofSummary): string {
  const t = (p.type || "").toLowerCase();
  if (t) return t;
  if (p.shares !== undefined || p.meta?.shares) return "hashshare";
  if (p.vdf || p.meta?.vdf) return "vdf";
  if (p.nmtRoot || p.commitment || p.meta?.da) return "storage";
  if (p.meta?.quantum || p.circuit || p.trapdoor) return "quantum";
  if (p.meta?.ai || p.model || p.jobId) return "ai";
  return "hashshare";
}

function bytesHuman(x?: number): string {
  if (!x && x !== 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = x;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

export default function BlockProofs(): JSX.Element {
  const { id } = useParams<{ id: string }>();

  // From store (if wired)
  const explorerApi = useExplorerStore((s) => (s as any).services?.explorerApi);
  const rpc = useExplorerStore((s) => (s as any).services?.rpc || (s as any).network?.rpc);
  const fetchOneBlock = useExplorerStore((s) => (s as any).blocks?.fetchOne);
  const chainId = useExplorerStore((s) => (s as any).network?.chainId);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | undefined>(undefined);
  const [proofs, setProofs] = useState<ProofSummary[]>([]);
  const [blockHash, setBlockHash] = useState<string | undefined>(undefined);
  const [blockHeight, setBlockHeight] = useState<number | undefined>(undefined);

  // Resolve param into hash/height
  const query = useMemo(() => {
    if (!id) return {};
    if (/^0x/i.test(id)) return { hash: id };
    const asNum = Number(id);
    return Number.isFinite(asNum) ? { height: asNum } : { hash: id };
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(undefined);
      try {
        // 1) Determine block hash/height if needed
        let hash = (query as any).hash as string | undefined;
        let height = (query as any).height as number | undefined;

        if (!hash || height === undefined) {
          // Try store first
          let blk: any = undefined;
          if (fetchOneBlock) {
            blk = await fetchOneBlock(query as any);
          }
          // Fallback to RPC if necessary
          if (!blk && rpc) {
            if (hash) {
              blk = await rpc.call("omni_getBlockByHash", [hash]);
            } else if (height !== undefined) {
              blk = await rpc.call("omni_getBlockByHeight", [height]);
            }
          }
          if (blk) {
            hash = blk.hash || hash;
            height = blk.height ?? height;
          }
        }

        // 2) Fetch proofs via best-available adapter
        let list: ProofSummary[] | undefined;
        if (explorerApi?.getBlockProofs) {
          list = await explorerApi.getBlockProofs(hash || height);
        }
        if (!list && rpc) {
          // Try dedicated RPC if node exposes it
          try {
            list = await rpc.call("omni_getBlockProofs", [hash || height]);
          } catch {
            // Some nodes include proofs on block itself
            try {
              const blk =
                hash
                  ? await rpc.call("omni_getBlockByHash", [hash])
                  : await rpc.call("omni_getBlockByHeight", [height]);
              list = blk?.proofs || [];
            } catch {
              /* ignore */
            }
          }
        }

        if (!cancelled) {
          setBlockHash(hash);
          setBlockHeight(height);
          setProofs(Array.isArray(list) ? list : []);
        }
      } catch (e: any) {
        if (!cancelled) setError(e?.message || String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [explorerApi, rpc, fetchOneBlock, query]);

  // Group proofs by normalized type
  const grouped = useMemo(() => {
    const g: Record<string, ProofSummary[]> = {};
    for (const p of proofs) {
      const t = normalizeType(p);
      if (!g[t]) g[t] = [];
      g[t].push(p);
    }
    // Stable order
    const order = ["hashshare", "ai", "quantum", "storage", "vdf"];
    const entries: Array<[string, ProofSummary[]]> = [];
    for (const k of order) if (g[k]?.length) entries.push([k, g[k]]);
    for (const k of Object.keys(g)) if (!order.includes(k)) entries.push([k, g[k]]);
    return entries;
  }, [proofs]);

  return (
    <div className="page page-block-proofs">
      <header className="page-header">
        <div>
          <h1>Block Proofs</h1>
          <p className="dim">
            {blockHeight !== undefined ? <>Block #{blockHeight.toLocaleString()} · </> : null}
            {blockHash ? <> {shortHash(blockHash)}</> : null}
            {chainId ? <> · Chain {chainId}</> : null}
          </p>
        </div>
        <div className="actions">
          {blockHash ? (
            <Link className="btn" to={`/block/${blockHash}`}>Back to Block</Link>
          ) : null}
        </div>
      </header>

      {loading ? (
        <section className="card">
          <div className="card-body"><p>Loading proofs…</p></div>
        </section>
      ) : error ? (
        <section className="card error">
          <div className="card-body">
            <p>Failed to load proofs: {error}</p>
          </div>
        </section>
      ) : proofs.length === 0 ? (
        <section className="card">
          <div className="card-body">
            <p className="dim">No proofs found for this block.</p>
          </div>
        </section>
      ) : (
        grouped.map(([type, items]) => (
          <ProofGroup key={type} type={type} items={items} />
        ))
      )}
    </div>
  );
}

function ProofGroup(props: { type: string; items: ProofSummary[] }) {
  const { type, items } = props;
  const label = TYPE_LABEL[type] || type.toUpperCase();
  const totalWeight = items.reduce((a, b) => a + (Number(b.weight) || 0), 0);
  const totalSize = items.reduce((a, b) => a + (Number(b.sizeBytes) || 0), 0);

  return (
    <section className="card mt-3" aria-labelledby={`group-${type}`}>
      <div className="card-header">
        <h2 id={`group-${type}`} className="card-title">
          {label} <span className="dim">({items.length})</span>
        </h2>
        <div className="card-actions">
          <span className="chip">Σ weight {totalWeight || 0}</span>
          <span className="chip">Σ size {bytesHuman(totalSize)}</span>
        </div>
      </div>
      <div className="card-body">
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Provider(s)</th>
                <th>Weight</th>
                <th>Size</th>
                {type === "storage" ? <th>Commitment</th> : null}
                {type === "ai" || type === "quantum" ? <th>Job</th> : null}
                {type === "vdf" ? <th>VDF</th> : null}
                {type === "hashshare" ? <th>Shares</th> : null}
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {items.map((p, i) => (
                <tr key={p.id || i}>
                  <td className="mono">
                    {p.id ? <Code value={p.id} compact allowCopy /> : "—"}
                  </td>
                  <td className="mono">
                    {Array.isArray(p.provider)
                      ? p.provider.map((q, qi) => (
                          <span key={qi} className="badge">{shortHash(q)}</span>
                        ))
                      : p.provider
                      ? shortHash(String(p.provider))
                      : "—"}
                  </td>
                  <td>{p.weight ?? "—"}</td>
                  <td>{bytesHuman(p.sizeBytes)}</td>

                  {type === "storage" ? (
                    <td className="mono">
                      {p.commitment || p.nmtRoot ? (
                        <Code value={p.commitment || p.nmtRoot} compact allowCopy />
                      ) : (
                        "—"
                      )}
                    </td>
                  ) : null}

                  {type === "ai" || type === "quantum" ? (
                    <td className="mono">
                      {p.jobId ? (
                        <Link to={`/${type === "ai" ? "ai" : "quantum"}/job/${p.jobId}`}>
                          {shortHash(p.jobId)}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </td>
                  ) : null}

                  {type === "vdf" ? (
                    <td className="mono">
                      {p.vdf ? (
                        <>
                          τ={p.vdf.tau ?? "?"} · {p.vdf.y ? shortHash(p.vdf.y) : "—"}
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                  ) : null}

                  {type === "hashshare" ? (
                    <td>{p.shares ?? p.meta?.shares ?? "—"}</td>
                  ) : null}

                  <td>
                    {p.verified === true ? (
                      <span className="chip chip-success">Verified</span>
                    ) : p.verified === false ? (
                      <span className="chip chip-warn">Unverified</span>
                    ) : (
                      <span className="chip">Unknown</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {items.some((p) => p.meta && Object.keys(p.meta).length) ? (
          <details className="mt-3">
            <summary className="dim">Show raw entries (debug)</summary>
            <pre className="mono small">
{JSON.stringify(items, null, 2)}
            </pre>
          </details>
        ) : null}
      </div>
    </section>
  );
}
