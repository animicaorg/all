import React, { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useExplorerStore } from "../../state/store";
import { shortHash } from "../../utils/format";
import { ago } from "../../utils/time";
import Code from "../../components/Code";
import PoIESBreakdown from "../../components/poies/PoIESBreakdown";
import ProofMixLegend from "../../components/poies/ProofMixLegend";

// Minimal shape we rely on for rendering; extra fields are accessed safely
type BlockLike = {
  height: number;
  hash: string;
  parentHash?: string;
  producer?: string;
  time?: number; // ms epoch
  txs?: number;
  gasUsed?: number;
  gasLimit?: number;
  // roots may be nested under roots or flat on the block
  stateRoot?: string;
  txRoot?: string;
  receiptsRoot?: string;
  daRoot?: string;
  roots?: {
    state?: string;
    tx?: string;
    receipts?: string;
    da?: string;
  };
  // PoIES/Proofs fields (any shape; we normalize below)
  proofs?: Array<{ type: string; count?: number; weight?: number }>;
  poies?: {
    gamma?: number;                // Γ
    acceptanceS?: number;          // S (acceptance threshold)
    caps?: Record<string, number>; // caps per proof-type
    mix?: Record<string, number>;  // normalized proof mix (0..1)
    fairness?: {
      gini?: number;
      hhi?: number;
    };
  };
  // Some backends may expose already-aggregated mix
  proofMix?: Record<string, number>;
};

export default function BlockDetailPage(): JSX.Element {
  const { id } = useParams<{ id: string }>();

  // Optional store helpers (if present)
  const fetchOne =
    useExplorerStore((s) => s.blocks?.fetchOne) ||
    (async (_q: { height?: number; hash?: string }) => undefined);
  const rpc =
    useExplorerStore((s) => s.network?.rpc) || undefined; // optional direct RPC handle
  const chainId = useExplorerStore((s) => s.network?.chainId);

  const [loading, setLoading] = useState(true);
  const [block, setBlock] = useState<BlockLike | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);

  // Resolve id as height or hash
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
        // Prefer store method if available
        let b: any = await fetchOne(query as any);
        // Fallback to generic RPC client if store not wired for single fetch
        if (!b && rpc) {
          if ((query as any).hash) {
            b = await rpc.call("omni_getBlockByHash", [(query as any).hash]);
          } else if ((query as any).height !== undefined) {
            b = await rpc.call("omni_getBlockByHeight", [(query as any).height]);
          }
        }
        if (!cancelled) {
          setBlock(b || undefined);
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
  }, [fetchOne, rpc, query]);

  // Normalized roots
  const roots = useMemo(() => {
    const r = block?.roots || {};
    return {
      state: block?.stateRoot || (r as any).state || (r as any).stateRoot,
      tx: block?.txRoot || (r as any).tx || (r as any).txRoot,
      receipts:
        block?.receiptsRoot || (r as any).receipts || (r as any).receiptsRoot,
      da: block?.daRoot || (r as any).da || (r as any).daRoot,
    };
  }, [block]);

  // Normalize proof mix from whichever fields are present
  const mix: Record<string, number> = useMemo(() => {
    if (!block) return {};
    if (block.poies?.mix) return block.poies.mix;
    if (block.proofMix) return block.proofMix;

    // Compute from proofs weight/count if present
    if (Array.isArray(block.proofs) && block.proofs.length) {
      let total = 0;
      const tmp: Record<string, number> = {};
      for (const p of block.proofs) {
        const w = typeof p.weight === "number" ? p.weight : (p.count ?? 0);
        tmp[p.type] = (tmp[p.type] || 0) + (w || 0);
        total += w || 0;
      }
      if (total > 0) {
        const norm: Record<string, number> = {};
        Object.entries(tmp).forEach(([k, v]) => (norm[k] = v / total));
        return norm;
      }
    }
    return {};
  }, [block]);

  const gamma = block?.poies?.gamma;
  const acceptanceS = block?.poies?.acceptanceS;
  const caps = block?.poies?.caps || undefined;
  const fairness = block?.poies?.fairness || undefined;

  const gasPct =
    block?.gasUsed !== undefined && block?.gasLimit
      ? Math.min(100, Math.round((block.gasUsed / block.gasLimit) * 100))
      : undefined;

  return (
    <div className="page page-block-detail">
      <header className="page-header">
        <div>
          <h1>
            Block{" "}
            {block?.height !== undefined ? (
              <span>#{block.height.toLocaleString()}</span>
            ) : id ? (
              <span>{shortHash(id)}</span>
            ) : (
              "—"
            )}
          </h1>
          <p className="dim">
            {block?.hash ? (
              <>
                {shortHash(block.hash)} ·{" "}
                {block.time ? (
                  <>
                    {new Date(block.time).toLocaleString()} ({ago(block.time)})
                  </>
                ) : (
                  "time unknown"
                )}
              </>
            ) : loading ? (
              "Loading block…"
            ) : error ? (
              <>Error loading block</>
            ) : (
              "Not found"
            )}
          </p>
        </div>
        {chainId ? (
          <div className="chip">Chain {chainId}</div>
        ) : null}
      </header>

      {error ? (
        <section className="card error">
          <div className="card-body">
            <p>Failed to load block: {error}</p>
          </div>
        </section>
      ) : null}

      <section className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <div className="card">
          <div className="card-header">
            <h2 className="card-title">Overview</h2>
          </div>
          <div className="card-body stats">
            <dl>
              <div className="stat">
                <dt>Producer</dt>
                <dd className="mono">
                  {block?.producer ? (
                    <Link to={`/address/${block.producer}`}>
                      {shortHash(block.producer)}
                    </Link>
                  ) : (
                    "—"
                  )}
                </dd>
              </div>
              <div className="stat">
                <dt>Transactions</dt>
                <dd>{block?.txs ?? "—"}</dd>
              </div>
              <div className="stat">
                <dt>Gas used</dt>
                <dd>
                  {block?.gasUsed !== undefined ? (
                    <>
                      {block.gasUsed.toLocaleString()}
                      {block?.gasLimit ? (
                        <>
                          {" "}
                          / {block.gasLimit.toLocaleString()}
                          {gasPct !== undefined ? (
                            <> ({gasPct}%)
                            </>
                          ) : null}
                        </>
                      ) : null}
                    </>
                  ) : (
                    "—"
                  )}
                </dd>
              </div>
              <div className="stat">
                <dt>Parent</dt>
                <dd className="mono">
                  {block?.parentHash ? (
                    <Link to={`/block/${block.parentHash}`}>
                      {shortHash(block.parentHash)}
                    </Link>
                  ) : (
                    "—"
                  )}
                </dd>
              </div>
            </dl>
          </div>
        </div>

        <div className="card lg:col-span-2">
          <div className="card-header">
            <h2 className="card-title">Roots</h2>
          </div>
          <div className="card-body">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <RootItem label="State Root" value={roots.state} />
              <RootItem label="Tx Root" value={roots.tx} />
              <RootItem label="Receipts Root" value={roots.receipts} />
              <RootItem label="DA Root" value={roots.da} />
            </div>
          </div>
        </div>
      </section>

      <section className="card mt-3" aria-labelledby="proofs-title">
        <div className="card-header">
          <h2 id="proofs-title" className="card-title">Proofs &amp; PoIES</h2>
          <div className="card-actions">
            {gamma !== undefined ? (
              <span className="chip">Γ {Number(gamma).toFixed(3)}</span>
            ) : null}
            {acceptanceS !== undefined ? (
              <span className="chip">S {Number(acceptanceS).toFixed(3)}</span>
            ) : null}
            {fairness?.gini !== undefined ? (
              <span className="chip">Gini {fairness.gini.toFixed(3)}</span>
            ) : null}
            {fairness?.hhi !== undefined ? (
              <span className="chip">HHI {fairness.hhi.toFixed(3)}</span>
            ) : null}
          </div>
        </div>
        <div className="card-body">
          {Object.keys(mix).length === 0 ? (
            <p className="dim">No proof mix information available for this block.</p>
          ) : (
            <>
              <div className="mb-3">
                <ProofMixLegend mix={mix} />
              </div>
              <PoIESBreakdown mix={mix} caps={caps} acceptanceS={acceptanceS} />
            </>
          )}
        </div>
      </section>

      <section className="card mt-3" aria-labelledby="hashes-title">
        <div className="card-header">
          <h2 id="hashes-title" className="card-title">Hashes</h2>
        </div>
        <div className="card-body">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <KV label="Hash" value={block?.hash} copy />
            <KV label="Parent Hash" value={block?.parentHash} copy linkTo={block?.parentHash ? `/block/${block.parentHash}` : undefined} />
          </div>
        </div>
      </section>
    </div>
  );
}

function RootItem(props: { label: string; value?: string }) {
  const { label, value } = props;
  return (
    <div className="root-item">
      <div className="label dim">{label}</div>
      {value ? <Code value={value} compact allowCopy /> : <div className="mono">—</div>}
    </div>
  );
}

function KV(props: {
  label: string;
  value?: string | number;
  copy?: boolean;
  linkTo?: string;
}) {
  const { label, value, copy, linkTo } = props;
  return (
    <div className="kv">
      <div className="label dim">{label}</div>
      {value === undefined || value === null || value === "" ? (
        <div className="mono">—</div>
      ) : linkTo ? (
        <Link className="mono" to={linkTo}>
          {typeof value === "string" ? shortHash(value) : String(value)}
        </Link>
      ) : typeof value === "string" ? (
        <Code value={value} compact allowCopy={!!copy} />
      ) : (
        <div className="mono">{String(value)}</div>
      )}
    </div>
  );
}
