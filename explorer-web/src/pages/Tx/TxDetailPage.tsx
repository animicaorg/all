import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useExplorerStore } from "../../state/store";
import Copy from "../../components/Copy";
import Code from "../../components/Code";
import Badge from "../../components/Badge";
import Tag from "../../components/Tag";
import { shortHash } from "../../utils/format";

type Hex = string;

type Tx = {
  hash: Hex;
  from?: Hex;
  to?: Hex | null;
  input?: Hex; // call data
  value?: string | number;
  gas?: string | number;
  gasPrice?: string | number;
  nonce?: number | string;
  method?: string;
  blockNumber?: number | string | null;
  timestamp?: number | string;
  type?: string | number;
  [k: string]: any;
};

type Log = {
  address: Hex;
  topics: Hex[];
  data: Hex;
  logIndex?: number | string;
  removed?: boolean;
  [k: string]: any;
};

type Receipt = {
  status?: boolean | string | number;
  gasUsed?: number | string;
  cumulativeGasUsed?: number | string;
  contractAddress?: Hex | null;
  logs?: Log[];
  blockNumber?: number | string;
  transactionIndex?: number | string;
  [k: string]: any;
};

type DetailResult = {
  tx?: Tx | null;
  receipt?: Receipt | null;
  blockTimestamp?: number | null;
};

function isHex(s?: any): s is string {
  return typeof s === "string" && /^0x[0-9a-fA-F]*$/.test(s);
}
function toNum(v: any): number | undefined {
  if (v == null) return undefined;
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    if (isHex(v)) return Number(BigInt(v));
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
}
function toWeiString(v: any): string | undefined {
  if (v == null) return undefined;
  if (typeof v === "number") return String(v);
  if (typeof v === "string") return isHex(v) ? String(BigInt(v)) : v;
  return undefined;
}
function fmtAmountLike(wei?: string | number): string {
  const n = toNum(wei);
  if (n == null) return "—";
  // Keep generic units (no chain-specific decimals). Show scientific for huge.
  return n.toLocaleString();
}
function fmtGas(n?: number | string): string {
  const v = toNum(n);
  return v == null ? "—" : v.toLocaleString();
}
function fmtStatus(s?: Receipt["status"]): "success" | "failed" | "pending" {
  if (s === true) return "success";
  if (s === false) return "failed";
  if (typeof s === "string" || typeof s === "number") {
    const n = toNum(s);
    if (n === 1) return "success";
    if (n === 0) return "failed";
  }
  return "pending";
}
function hexSize(h?: Hex): string {
  if (!h) return "0 B";
  const bytes = Math.max(0, (h.length - 2) / 2);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function TxDetailPage(): JSX.Element {
  const { hash = "" } = useParams<{ hash: string }>();

  const store = useExplorerStore((s) => s as any);
  const rpc = store?.services?.rpc || store?.network?.rpc;
  const explorerApi = store?.services?.explorerApi;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | undefined>(undefined);
  const [detail, setDetail] = useState<DetailResult>({ tx: undefined, receipt: undefined, blockTimestamp: null });

  const status = useMemo(() => fmtStatus(detail.receipt?.status), [detail.receipt]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(undefined);
    try {
      // 1) Explorer REST (preferred if available)
      if (explorerApi?.txs?.get) {
        const res = await explorerApi.txs.get(hash);
        // Accept flexible shapes:
        const tx: Tx | null = res?.tx ?? res?.transaction ?? res ?? null;
        const receipt: Receipt | null = res?.receipt ?? null;

        let ts: number | null = null;
        const fromTx = toNum(tx?.timestamp);
        if (fromTx != null) ts = fromTx;
        if (ts == null && receipt?.blockNumber != null) {
          ts = await fetchBlockTimestamp(rpc, receipt.blockNumber);
        }
        setDetail({ tx, receipt, blockTimestamp: ts });
        setLoading(false);
        return;
      }

      // 2) RPC fallbacks: get tx + receipt
      const tx: Tx | null = await getTxByHash(rpc, hash);
      const receipt: Receipt | null = await getReceiptByHash(rpc, hash);

      let ts: number | null = null;
      const fromTx = toNum(tx?.timestamp);
      if (fromTx != null) ts = fromTx;
      const bn = tx?.blockNumber ?? receipt?.blockNumber;
      if (ts == null && bn != null) {
        ts = await fetchBlockTimestamp(rpc, bn);
      }

      if (!tx && !receipt) throw new Error("Transaction not found on backend.");
      setDetail({ tx, receipt, blockTimestamp: ts });
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [hash, explorerApi, rpc]);

  useEffect(() => {
    if (!hash) return;
    refresh();
  }, [hash, refresh]);

  const fee = useMemo(() => {
    const gasPrice = toNum(detail.tx?.gasPrice);
    const gasUsed = toNum(detail.receipt?.gasUsed);
    if (gasPrice == null || gasUsed == null) return undefined;
    try {
      const n = BigInt(gasPrice) * BigInt(gasUsed);
      return n.toString();
    } catch {
      return String(gasPrice * gasUsed);
    }
  }, [detail]);

  return (
    <div className="page page-tx-detail">
      <header className="page-header">
        <div>
          <h1>
            Transaction <span className="mono">{hash ? shortHash(hash) : "—"}</span>
          </h1>
          <p className="dim">Inputs, outputs, logs, and receipt</p>
        </div>
        <div className="actions">
          <button className="btn" onClick={refresh} disabled={loading}>{loading ? "Loading…" : "Refresh"}</button>
        </div>
      </header>

      {error && <div className="alert warn">{error}</div>}

      {!error && (loading && !detail.tx && !detail.receipt) && (
        <p className="dim">Fetching transaction…</p>
      )}

      {!loading && !error && (
        <>
          <section className="card">
            <div className="card-header">
              <h2 className="card-title">Summary</h2>
              <div className="card-actions">
                <Badge kind={status === "success" ? "success" : status === "failed" ? "danger" : "warn"}>
                  {status.toUpperCase()}
                </Badge>
              </div>
            </div>
            <div className="card-body">
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                <KV label="Hash">
                  <Mono>
                    {hash} <Copy text={hash} />
                  </Mono>
                </KV>

                <KV label="Block">
                  {detail.tx?.blockNumber != null || detail.receipt?.blockNumber != null ? (
                    <Link
                      className="link"
                      to={`/blocks/${toNum(detail.tx?.blockNumber ?? detail.receipt?.blockNumber)}`}
                    >
                      #{toNum(detail.tx?.blockNumber ?? detail.receipt?.blockNumber)}
                    </Link>
                  ) : (
                    "—"
                  )}
                </KV>

                <KV label="Timestamp">
                  {detail.blockTimestamp != null
                    ? new Date(Number(detail.blockTimestamp) * 1000).toLocaleString()
                    : "—"}
                </KV>

                <KV label="From">
                  {detail.tx?.from ? (
                    <Link className="link mono" to={`/address/${detail.tx.from}`}>
                      {shortHash(detail.tx.from)}
                    </Link>
                  ) : (
                    "—"
                  )}
                </KV>

                <KV label="To / Created">
                  {detail.receipt?.contractAddress ? (
                    <span className="flex items-center gap-2">
                      <Tag>Contract</Tag>
                      <Link className="link mono" to={`/address/${detail.receipt.contractAddress}`}>
                        {shortHash(detail.receipt.contractAddress)}
                      </Link>
                    </span>
                  ) : detail.tx?.to ? (
                    <Link className="link mono" to={`/address/${detail.tx.to}`}>
                      {shortHash(detail.tx.to)}
                    </Link>
                  ) : (
                    <span className="dim">Contract creation</span>
                  )}
                </KV>

                <KV label="Method">{detail.tx?.method || "—"}</KV>

                <KV label="Value">{fmtAmountLike(detail.tx?.value)}</KV>

                <KV label="Gas (limit / used)">
                  {fmtGas(detail.tx?.gas)} {" / "} {fmtGas(detail.receipt?.gasUsed)}
                </KV>

                <KV label="Gas Price">{fmtAmountLike(detail.tx?.gasPrice)}</KV>

                <KV label="Fee (≈)">
                  {fee ? `${fmtAmountLike(fee)} (wei-like units)` : "—"}
                </KV>

                <KV label="Nonce">{detail.tx?.nonce ?? "—"}</KV>

                <KV label="Type">{detail.tx?.type ?? "—"}</KV>
              </div>
            </div>
          </section>

          <section className="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-3">
            <div className="card">
              <div className="card-header">
                <h2 className="card-title">Inputs</h2>
                <div className="card-actions">
                  <span className="dim small">{hexSize(detail.tx?.input)}</span>
                </div>
              </div>
              <div className="card-body">
                {detail.tx?.input ? (
                  <Code value={detail.tx.input} />
                ) : (
                  <p className="dim">No input data.</p>
                )}
              </div>
            </div>

            <div className="card">
              <div className="card-header">
                <h2 className="card-title">Outputs</h2>
                <div className="card-actions">
                  <span className="dim small">
                    {detail.receipt?.contractAddress ? "Contract deployed" : "Return data / events"}
                  </span>
                </div>
              </div>
              <div className="card-body">
                {detail.receipt?.contractAddress ? (
                  <p className="mono">
                    Contract Address:{" "}
                    <Link className="link" to={`/address/${detail.receipt.contractAddress}`}>
                      {detail.receipt.contractAddress}
                    </Link>{" "}
                    <Copy text={detail.receipt.contractAddress} />
                  </p>
                ) : (
                  <p className="dim">No direct return data captured. See Events & Logs below.</p>
                )}
              </div>
            </div>
          </section>

          <section className="card mt-3">
            <div className="card-header">
              <h2 className="card-title">Events & Logs</h2>
              <div className="card-actions">
                <span className="dim small">{detail.receipt?.logs?.length ?? 0} logs</span>
              </div>
            </div>
            <div className="card-body">
              {detail.receipt?.logs && detail.receipt.logs.length > 0 ? (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>Address</th>
                        <th>Topic0</th>
                        <th>Topics</th>
                        <th>Data</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.receipt.logs.map((log, i) => (
                        <tr key={`${i}-${log.logIndex ?? ""}`}>
                          <td className="mono">{toNum(log.logIndex) ?? i}</td>
                          <td className="mono">
                            <Link className="link" to={`/address/${log.address}`}>{shortHash(log.address)}</Link>
                          </td>
                          <td className="mono">{log.topics?.[0] ? shortHash(log.topics[0]) : "—"}</td>
                          <td className="mono">
                            {log.topics?.length ? (
                              <span title={log.topics.join(", ")}>{log.topics.length} topics</span>
                            ) : (
                              "—"
                            )}
                          </td>
                          <td className="mono">
                            <span title={log.data}>{hexSize(log.data)}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="dim">No logs emitted.</p>
              )}
            </div>
          </section>

          <section className="card mt-3">
            <div className="card-header">
              <h2 className="card-title">Receipt</h2>
              <div className="card-actions">
                <Badge kind={status === "success" ? "success" : status === "failed" ? "danger" : "warn"}>
                  {status.toUpperCase()}
                </Badge>
              </div>
            </div>
            <div className="card-body">
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                <KV label="Status">{status}</KV>
                <KV label="Gas Used">{fmtGas(detail.receipt?.gasUsed)}</KV>
                <KV label="Cumulative Gas Used">{fmtGas(detail.receipt?.cumulativeGasUsed)}</KV>
                <KV label="Tx Index">{detail.receipt?.transactionIndex ?? "—"}</KV>
                <KV label="Block">
                  {detail.receipt?.blockNumber != null ? (
                    <Link className="link" to={`/blocks/${toNum(detail.receipt.blockNumber)}`}>
                      #{toNum(detail.receipt.blockNumber)}
                    </Link>
                  ) : "—"}
                </KV>
              </div>
            </div>
          </section>

          <section className="card mt-3">
            <div className="card-header">
              <h2 className="card-title">Raw (TX)</h2>
            </div>
            <div className="card-body">
              <Code value={JSON.stringify(detail.tx ?? {}, null, 2)} />
            </div>
          </section>

          <section className="card mt-3">
            <div className="card-header">
              <h2 className="card-title">Raw (Receipt)</h2>
            </div>
            <div className="card-body">
              <Code value={JSON.stringify(detail.receipt ?? {}, null, 2)} />
            </div>
          </section>
        </>
      )}
    </div>
  );
}

/** Small helpers & subcomponents */

function KV(props: { label: string; children: React.ReactNode }) {
  return (
    <div className="kv">
      <div className="label dim small">{props.label}</div>
      <div className="value">{props.children}</div>
    </div>
  );
}
function Mono(props: { children: React.ReactNode }) {
  return <span className="mono">{props.children}</span>;
}

/** RPC helpers with graceful fallbacks */

async function getTxByHash(rpc: any, hash: string): Promise<Tx | null> {
  if (!rpc?.call) return null;
  // Try common methods
  const methods = [
    (h: string) => rpc.call("omni_getTransactionByHash", [h]),
    (h: string) => rpc.call("eth_getTransactionByHash", [h]),
    (h: string) => rpc.call("chain_getTransactionByHash", [h]),
  ];
  for (const fn of methods) {
    try {
      const tx = await fn(hash);
      if (tx) return normalizeTx(tx);
    } catch {
      /* continue trying */
    }
  }
  return null;
}

async function getReceiptByHash(rpc: any, hash: string): Promise<Receipt | null> {
  if (!rpc?.call) return null;
  const methods = [
    (h: string) => rpc.call("omni_getTransactionReceipt", [h]),
    (h: string) => rpc.call("eth_getTransactionReceipt", [h]),
    (h: string) => rpc.call("chain_getTransactionReceipt", [h]),
  ];
  for (const fn of methods) {
    try {
      const rcpt = await fn(hash);
      if (rcpt) return normalizeReceipt(rcpt);
    } catch {
      /* continue */
    }
  }
  return null;
}

async function fetchBlockTimestamp(rpc: any, blockNumber: number | string): Promise<number | null> {
  if (!rpc?.call) return null;
  const asHex = (n: number | string) => (typeof n === "string" && isHex(n) ? n : "0x" + Number(n).toString(16));
  const candidates = [
    (n: any) => rpc.call("omni_getBlockByNumber", [asHex(n), true]),
    (n: any) => rpc.call("eth_getBlockByNumber", [asHex(n), true]),
    (n: any) => rpc.call("chain_getBlockByNumber", [asHex(n), true]),
  ];
  for (const fn of candidates) {
    try {
      const block = await fn(blockNumber);
      const ts = toNum(block?.timestamp);
      if (ts != null) return ts;
    } catch {
      /* try next */
    }
  }
  return null;
}

function normalizeTx(tx: any): Tx {
  const out: Tx = { ...tx };
  // Normalize common hex fields to numbers where feasible
  out.gas = toNum(tx.gas) ?? tx.gas;
  out.gasPrice = toWeiString(tx.gasPrice) ?? tx.gasPrice;
  out.nonce = toNum(tx.nonce) ?? tx.nonce;
  out.blockNumber = tx.blockNumber != null ? toNum(tx.blockNumber) ?? tx.blockNumber : null;
  out.timestamp = toNum(tx.timestamp) ?? tx.timestamp;
  return out;
}
function normalizeReceipt(r: any): Receipt {
  const out: Receipt = { ...r };
  out.gasUsed = toNum(r.gasUsed) ?? r.gasUsed;
  out.cumulativeGasUsed = toNum(r.cumulativeGasUsed) ?? r.cumulativeGasUsed;
  out.blockNumber = r.blockNumber != null ? toNum(r.blockNumber) ?? r.blockNumber : r.blockNumber;
  return out;
}
