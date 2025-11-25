import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useExplorerStore } from "../../state/store";
import Code from "../../components/Code";
import { shortHash } from "../../utils/format";
import { hexToBytes, bytesToHex } from "../../utils/bytes";

/**
 * BlockDaPanel
 * - Displays the block's Data Availability root (NMT / commitment root)
 * - Provides a small helper to fetch and verify a sample DA proof for a commitment
 *
 * This component uses best-effort adapters:
 *  - Prefer explorerApi.da.getProofForBlock / services.da.getProof
 *  - Fallback to node RPC methods if available (omni_getDAProof / omni_getBlockByHash)
 */
type AnyBlock = {
  hash?: string;
  height?: number;
  // Possible DA root field names depending on node version:
  dataRoot?: string;
  daRoot?: string;
  nmtRoot?: string;
  commitmentRoot?: string;
  blobs?: Array<{ commitment?: string; size?: number }>;
  [k: string]: any;
};

type ProofResponse = {
  // Expected fields for verification:
  leaf?: string;        // hex of the leaf (commitment or blob-hash)
  leafHash?: string;    // alias
  commitment?: string;  // alias for leaf
  index?: number;       // leaf index in tree
  siblings?: string[];  // hex list from leaf->root
  root?: string;        // computed root (optional)
  hash?: "sha256" | string; // hash name (we support sha256 here)
  // Extras:
  valid?: boolean;
  [k: string]: any;
};

async function sha256(data: Uint8Array): Promise<Uint8Array> {
  const d = await crypto.subtle.digest("SHA-256", data);
  return new Uint8Array(d);
}
async function sha256Concat(a: Uint8Array, b: Uint8Array): Promise<Uint8Array> {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return sha256(out);
}

async function verifyMerkleProof(proof: ProofResponse, expectedRootHex: string): Promise<boolean> {
  const expectedRoot = normalizeHex(expectedRootHex);
  // Choose leaf value
  const leafHex = proof.leaf || proof.leafHash || proof.commitment;
  if (!leafHex) return false;
  let cur = hexToBytes(normalizeHex(leafHex));
  const siblings = (proof.siblings || []).map((h) => hexToBytes(normalizeHex(h)));
  const index = Number(proof.index || 0);

  // Only sha256 supported here for demo; extend as needed.
  for (let i = 0; i < siblings.length; i++) {
    const sib = siblings[i];
    const bit = (index >> i) & 1;
    cur = bit ? await sha256Concat(sib, cur) : await sha256Concat(cur, sib);
  }
  const rootHex = "0x" + bytesToHex(cur);
  if (proof.root && normalizeHex(proof.root) !== rootHex) {
    // If backend provided a root, it must match recomputed one.
    return false;
  }
  return rootHex.toLowerCase() === expectedRoot.toLowerCase();
}

function normalizeHex(x?: string): string {
  if (!x) return "0x";
  return x.startsWith("0x") || x.startsWith("0X") ? x : `0x${x}`;
}

export default function BlockDaPanel(): JSX.Element {
  const { id } = useParams<{ id: string }>();

  // Store services (be defensive; these may be undefined in some builds)
  const store = useExplorerStore((s) => s as any);
  const explorerApi = store?.services?.explorerApi;
  const daService = store?.services?.da; // optional adapter
  const rpc = store?.services?.rpc || store?.network?.rpc;
  const fetchOneBlock = store?.blocks?.fetchOne;
  const chainId = store?.network?.chainId;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | undefined>(undefined);
  const [block, setBlock] = useState<AnyBlock | undefined>(undefined);
  const [daRoot, setDaRoot] = useState<string | undefined>(undefined);

  // Sample verification UI
  const [commitmentHex, setCommitmentHex] = useState<string>("");
  const [proof, setProof] = useState<ProofResponse | undefined>(undefined);
  const [verifying, setVerifying] = useState(false);
  const [verifyResult, setVerifyResult] = useState<"ok" | "fail" | undefined>(undefined);
  const [proofErr, setProofErr] = useState<string | undefined>(undefined);

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
      setProof(undefined);
      setVerifyResult(undefined);
      setProofErr(undefined);
      try {
        let blk: AnyBlock | undefined;
        if (fetchOneBlock) {
          blk = await fetchOneBlock(query);
        }
        if (!blk && rpc) {
          if ((query as any).hash) {
            blk = await rpc.call("omni_getBlockByHash", [(query as any).hash]);
          } else if ((query as any).height !== undefined) {
            blk = await rpc.call("omni_getBlockByHeight", [(query as any).height]);
          }
        }
        if (!blk) throw new Error("Block not found");

        const root =
          blk.dataRoot || blk.daRoot || blk.nmtRoot || blk.commitmentRoot || undefined;

        if (!cancelled) {
          setBlock(blk);
          setDaRoot(root);
          // If block exposes blob commitments, pre-fill sample commitment
          const sample = blk.blobs?.find((b: any) => !!b.commitment)?.commitment;
          if (sample) setCommitmentHex(sample);
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
  }, [fetchOneBlock, rpc, query]);

  const fetchProof = useCallback(
    async (commitment: string) => {
      if (!commitment) {
        setProofErr("Enter a commitment to fetch its proof.");
        return;
      }
      setProofErr(undefined);
      setVerifyResult(undefined);
      setProof(undefined);
      try {
        const c = normalizeHex(commitment);
        // 1) Explorer API adapter (if present)
        if (explorerApi?.da?.getProofForBlock) {
          const pr: ProofResponse = await explorerApi.da.getProofForBlock(
            block?.hash ?? block?.height,
            c
          );
          setProof(pr);
          return;
        }
        // 2) Custom services.da adapter
        if (daService?.getProof) {
          const pr: ProofResponse = await daService.getProof({
            block: block?.hash ?? block?.height,
            commitment: c,
          });
          setProof(pr);
          return;
        }
        // 3) RPC fallbacks
        if (rpc) {
          // Preferred: omni_getDAProof(blockRef, commitment)
          try {
            const pr = await rpc.call("omni_getDAProof", [block?.hash ?? block?.height, c]);
            setProof(pr as ProofResponse);
            return;
          } catch {
            /* try legacy signatures */
          }
          // Legacy variant: omni_getDAProof(commitment)
          try {
            const pr = await rpc.call("omni_getDAProof", [c]);
            setProof(pr as ProofResponse);
            return;
          } catch {
            /* last resort: embedded on block? */
          }
          // Some nodes expose proofs on block or via a different endpoint; try re-fetch block:
          try {
            const blk: AnyBlock = block?.hash
              ? await rpc.call("omni_getBlockByHash", [block.hash])
              : await rpc.call("omni_getBlockByHeight", [block?.height]);
            const pr =
              blk?.proofs?.find?.((p: any) =>
                [p.commitment, p.leaf, p.leafHash]?.some?.((h: string) => normalizeHex(h) === normalizeHex(c))
              ) || undefined;
            if (pr) {
              setProof(pr as ProofResponse);
              return;
            }
          } catch {
            /* ignore */
          }
        }
        setProofErr("No DA proof API found on this backend.");
      } catch (e: any) {
        setProofErr(e?.message || String(e));
      }
    },
    [explorerApi, daService, rpc, block]
  );

  const runVerify = useCallback(async () => {
    if (!proof) return;
    if (!daRoot) {
      setVerifyResult("fail");
      setProofErr("Block does not expose a DA root.");
      return;
    }
    setVerifying(true);
    setProofErr(undefined);
    try {
      const ok = await verifyMerkleProof(proof, daRoot);
      setVerifyResult(ok ? "ok" : "fail");
    } catch (e: any) {
      setVerifyResult("fail");
      setProofErr(e?.message || String(e));
    } finally {
      setVerifying(false);
    }
  }, [proof, daRoot]);

  return (
    <div className="page page-block-da">
      <header className="page-header">
        <div>
          <h1>Data Availability</h1>
          <p className="dim">
            {block?.height !== undefined ? <>Block #{block.height.toLocaleString()} · </> : null}
            {block?.hash ? <> {shortHash(block.hash)}</> : null}
            {chainId ? <> · Chain {chainId}</> : null}
          </p>
        </div>
        <div className="actions">
          {block?.hash ? (
            <Link className="btn" to={`/block/${block.hash}`}>Back to Block</Link>
          ) : null}
        </div>
      </header>

      {loading ? (
        <section className="card">
          <div className="card-body"><p>Loading block…</p></div>
        </section>
      ) : error ? (
        <section className="card error">
          <div className="card-body">
            <p>Failed to load: {error}</p>
          </div>
        </section>
      ) : (
        <>
          <section className="card">
            <div className="card-header">
              <h2 className="card-title">DA Root</h2>
            </div>
            <div className="card-body">
              {daRoot ? (
                <Code value={daRoot} allowCopy />
              ) : (
                <p className="dim">This block does not expose a DA root field.</p>
              )}

              {Array.isArray(block?.blobs) && block!.blobs!.length > 0 ? (
                <details className="mt-3">
                  <summary className="dim">Blobs in block ({block!.blobs!.length})</summary>
                  <div className="mt-2">
                    <table className="table">
                      <thead>
                        <tr>
                          <th>#</th>
                          <th>Commitment</th>
                          <th>Size</th>
                        </tr>
                      </thead>
                      <tbody>
                        {block!.blobs!.map((b: any, i: number) => (
                          <tr key={i}>
                            <td>{i}</td>
                            <td className="mono">{b?.commitment ? shortHash(b.commitment) : "—"}</td>
                            <td>{b?.size ?? "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              ) : null}
            </div>
          </section>

          <section className="card mt-3">
            <div className="card-header">
              <h2 className="card-title">Sample Verification Helper</h2>
            </div>
            <div className="card-body">
              <p className="dim">
                Enter a blob <strong>commitment</strong> (hex) from this block and fetch a Merkle proof.
                We will verify it against the block's DA root in your browser.
              </p>

              <div className="form-row mt-2">
                <label htmlFor="commitment" className="label">Commitment</label>
                <input
                  id="commitment"
                  type="text"
                  className="input mono"
                  placeholder="0x…"
                  value={commitmentHex}
                  onChange={(e) => setCommitmentHex(e.target.value.trim())}
                />
              </div>

              <div className="mt-2">
                <button className="btn" onClick={() => fetchProof(commitmentHex)}>
                  Fetch Proof
                </button>
              </div>

              {proofErr ? (
                <div className="alert warn mt-2">{proofErr}</div>
              ) : null}

              {proof ? (
                <div className="mt-3">
                  <h3 className="h3">Proof</h3>
                  <details open className="mt-1">
                    <summary className="dim">Raw</summary>
                    <pre className="mono small">
{JSON.stringify(proof, null, 2)}
                    </pre>
                  </details>

                  <div className="mt-2">
                    <button className="btn" onClick={runVerify} disabled={verifying}>
                      {verifying ? "Verifying…" : "Verify Against DA Root"}
                    </button>
                    {verifyResult === "ok" ? (
                      <span className="chip chip-success ml-2">Valid</span>
                    ) : verifyResult === "fail" ? (
                      <span className="chip chip-warn ml-2">Invalid</span>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
