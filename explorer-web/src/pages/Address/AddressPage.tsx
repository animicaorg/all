import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useExplorerStore } from "../../state/store";
import Copy from "../../components/Copy";
import Tag from "../../components/Tag";
import { shortHash } from "../../utils/format";
import AddressTxTable from "../../components/tables/AddressTxTable";
import ContractsTable from "../../components/tables/ContractsTable";
import Code from "../../components/Code";

type Hex = string;

type Tx = {
  hash: Hex;
  from?: Hex;
  to?: Hex | null;
  value?: string | number;
  gas?: string | number;
  gasPrice?: string | number;
  nonce?: number | string;
  blockNumber?: number | string | null;
  timestamp?: number | string;
  status?: number | string | boolean;
  method?: string;
  [k: string]: any;
};

type ContractRow = {
  address: Hex;
  name?: string;
  verified?: boolean;
  codeHash?: Hex;
  [k: string]: any;
};

type AccountSnapshot = {
  address: Hex;
  balance?: string;
  nonce?: number;
  isContract: boolean;
  code?: Hex | null;
};

function isHex(s?: any): s is string {
  return typeof s === "string" && /^0x[0-9a-fA-F]*$/.test(s);
}
function toNum(v: any): number | undefined {
  if (v == null) return undefined;
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    if (isHex(v)) {
      try { return Number(BigInt(v)); } catch { return undefined; }
    }
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
}
function toBigIntString(v: any): string | undefined {
  if (v == null) return undefined;
  if (typeof v === "number") return String(v);
  if (typeof v === "string") return isHex(v) ? String(BigInt(v)) : v;
  return undefined;
}
function fmtAmountLike(wei?: string | number): string {
  const n = toNum(wei);
  if (n == null) return "—";
  return n.toLocaleString();
}
function hexSize(h?: Hex | null): string {
  if (!h) return "0 B";
  const bytes = Math.max(0, (h.length - 2) / 2);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function AddressPage(): JSX.Element {
  const { addr = "" } = useParams<{ addr: string }>();
  const store = useExplorerStore((s) => s as any);

  const rpc = store?.services?.rpc || store?.network?.rpc;
  const explorerApi = store?.services?.explorerApi;
  const servicesApi = store?.services?.servicesApi;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | undefined>(undefined);

  const [acct, setAcct] = useState<AccountSnapshot | undefined>(undefined);
  const [txs, setTxs] = useState<Tx[]>([]);
  const [contracts, setContracts] = useState<ContractRow[]>([]);
  const [verifiedArtifacts, setVerifiedArtifacts] = useState<any[]>([]);

  const refresh = useCallback(async () => {
    if (!addr) return;
    setLoading(true);
    setError(undefined);

    try {
      // Prefer explorer REST if available
      if (explorerApi?.address?.get) {
        const res = await explorerApi.address.get(addr);
        // Flexible shapes accepted:
        const balance = toBigIntString(res?.balance ?? res?.account?.balance);
        const nonce = toNum(res?.nonce ?? res?.account?.nonce);
        const code = res?.code ?? res?.account?.code ?? null;
        const isContract = Boolean(res?.isContract ?? (typeof code === "string" && code !== "0x" && code !== "0x0"));

        setAcct({ address: addr, balance, nonce, isContract, code });

        // Recent txs
        if (explorerApi.address.txs?.list) {
          const list = await explorerApi.address.txs.list(addr, { limit: 25 });
          setTxs(Array.isArray(list?.items) ? list.items.map(normalizeTx) : (Array.isArray(list) ? list.map(normalizeTx) : []));
        } else if (Array.isArray(res?.txs)) {
          setTxs(res.txs.map(normalizeTx));
        } else {
          setTxs([]);
        }

        // Contracts by creator / or contract itself
        if (explorerApi.address.contracts?.list) {
          const cl = await explorerApi.address.contracts.list(addr, { limit: 50 });
          const rows = Array.isArray(cl?.items) ? cl.items : (Array.isArray(cl) ? cl : []);
          setContracts(rows);
        } else if (isContract) {
          // If this address itself is a contract, show single row
          setContracts([{ address: addr, verified: undefined }]);
        } else {
          setContracts([]);
        }
      } else {
        // RPC fallbacks
        const [balance, nonce, code] = await Promise.all([
          getBalance(rpc, addr),
          getNonce(rpc, addr),
          getCode(rpc, addr),
        ]);
        const isContract = typeof code === "string" && code !== "0x" && code !== "0x0";
        setAcct({ address: addr, balance, nonce, isContract, code });

        // No standard RPC for "txs by address"; leave empty if no explorer
        setTxs([]);
        setContracts(isContract ? [{ address: addr }] : []);
      }

      // Verified artifacts via services (optional)
      if (servicesApi?.address?.artifacts?.list) {
        try {
          const arts = await servicesApi.address.artifacts.list(addr);
          setVerifiedArtifacts(Array.isArray(arts?.items) ? arts.items : (Array.isArray(arts) ? arts : []));
        } catch {
          setVerifiedArtifacts([]);
        }
      } else {
        setVerifiedArtifacts([]);
      }
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [addr, explorerApi, rpc, servicesApi]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const roleTag = useMemo(
    () => (acct?.isContract ? <Tag>Contract</Tag> : <Tag>EOA</Tag>),
    [acct?.isContract]
  );

  return (
    <div className="page page-address">
      <header className="page-header">
        <div>
          <h1>
            Address <span className="mono">{addr ? shortHash(addr) : "—"}</span>
          </h1>
          <p className="dim">Balance, nonce, recent transactions, and contracts</p>
        </div>
        <div className="actions">
          <button className="btn" onClick={refresh} disabled={loading}>{loading ? "Loading…" : "Refresh"}</button>
        </div>
      </header>

      {error && <div className="alert warn">{error}</div>}

      <section className="card">
        <div className="card-header">
          <h2 className="card-title">Summary</h2>
          <div className="card-actions">
            {roleTag}
          </div>
        </div>
        <div className="card-body">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            <KV label="Address">
              <span className="mono">{addr} <Copy text={addr} /></span>
            </KV>
            <KV label="Balance">
              {fmtAmountLike(acct?.balance)} <span className="dim small">(wei-like)</span>
            </KV>
            <KV label="Nonce">{acct?.nonce ?? "—"}</KV>
            <KV label="Code size">
              {acct?.code ? hexSize(acct.code) : "—"}
            </KV>
            <KV label="Type">{acct?.isContract ? "Contract" : "Externally Owned (EOA)"}</KV>
            <KV label="Explorer backend">
              {explorerApi ? "REST+RPC" : "RPC-only"}
            </KV>
          </div>
        </div>
      </section>

      {acct?.isContract && (
        <section className="card mt-3">
          <div className="card-header">
            <h2 className="card-title">Contract Code (hex preview)</h2>
            <div className="card-actions">
              <span className="dim small">{hexSize(acct?.code)}</span>
            </div>
          </div>
          <div className="card-body">
            {acct?.code ? <Code value={acct.code} /> : <p className="dim">No code available.</p>}
          </div>
        </section>
      )}

      <section className="card mt-3">
        <div className="card-header">
          <h2 className="card-title">Recent Transactions</h2>
          <div className="card-actions">
            <span className="dim small">{txs.length} total</span>
          </div>
        </div>
        <div className="card-body">
          {txs.length > 0 ? (
            <AddressTxTable address={addr} txs={txs} />
          ) : explorerApi?.address?.txs?.list ? (
            <p className="dim">No recent transactions.</p>
          ) : (
            <p className="dim">
              This backend does not provide an address transaction index. Connect an explorer API to see recent txs.
            </p>
          )}
        </div>
      </section>

      <section className="card mt-3">
        <div className="card-header">
          <h2 className="card-title">{acct?.isContract ? "This Contract" : "Contracts Deployed"}</h2>
          <div className="card-actions">
            <span className="dim small">
              {contracts.length} {contracts.length === 1 ? "entry" : "entries"}
            </span>
          </div>
        </div>
        <div className="card-body">
          {contracts.length > 0 ? (
            <ContractsTable contracts={contracts} />
          ) : acct?.isContract ? (
            <p className="dim">This address is a contract. No additional contract entries to list.</p>
          ) : explorerApi?.address?.contracts?.list ? (
            <p className="dim">No contracts deployed by this address.</p>
          ) : (
            <p className="dim">
              Contract indexing is unavailable on this backend. Connect an explorer API to list contracts.
            </p>
          )}
        </div>
      </section>

      <section className="card mt-3">
        <div className="card-header">
          <h2 className="card-title">Verified Artifacts</h2>
          <div className="card-actions">
            <span className="dim small">{verifiedArtifacts.length}</span>
          </div>
        </div>
        <div className="card-body">
          {verifiedArtifacts.length > 0 ? (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Artifact ID</th>
                    <th>Name</th>
                    <th>Code Hash</th>
                  </tr>
                </thead>
                <tbody>
                  {verifiedArtifacts.map((a: any) => (
                    <tr key={a.id ?? a.codeHash ?? a.name}>
                      <td className="mono">
                        {a.id ? <span title={a.id}>{shortHash(a.id)}</span> : "—"}
                      </td>
                      <td>{a.name ?? a.contractName ?? "—"}</td>
                      <td className="mono">
                        {a.codeHash ? <span title={a.codeHash}>{shortHash(a.codeHash)}</span> : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : servicesApi?.address?.artifacts?.list ? (
            <p className="dim">No verified artifacts found for this address.</p>
          ) : (
            <p className="dim">
              Verification service is not connected. Configure studio-services to display verified artifacts.
            </p>
          )}
        </div>
      </section>
    </div>
  );
}

/** Small label/value layout */
function KV(props: { label: string; children: React.ReactNode }) {
  return (
    <div className="kv">
      <div className="label dim small">{props.label}</div>
      <div className="value">{props.children}</div>
    </div>
  );
}

/** RPC helpers */

async function getBalance(rpc: any, address: string): Promise<string | undefined> {
  if (!rpc?.call) return undefined;
  const calls = [
    (a: string) => rpc.call("omni_getBalance", [a, "latest"]),
    (a: string) => rpc.call("eth_getBalance", [a, "latest"]),
    (a: string) => rpc.call("chain_getBalance", [a]),
  ];
  for (const fn of calls) {
    try {
      const res = await fn(address);
      const s = toBigIntString(res);
      if (s != null) return s;
    } catch {
      /* continue */
    }
  }
  return undefined;
}

async function getNonce(rpc: any, address: string): Promise<number | undefined> {
  if (!rpc?.call) return undefined;
  const calls = [
    (a: string) => rpc.call("omni_getTransactionCount", [a, "latest"]),
    (a: string) => rpc.call("eth_getTransactionCount", [a, "latest"]),
    (a: string) => rpc.call("chain_getTransactionCount", [a]),
    (a: string) => rpc.call("omni_getAccount", [a]).then((x: any) => x?.nonce),
  ];
  for (const fn of calls) {
    try {
      const res = await fn(address);
      const n = toNum(res);
      if (n != null) return n;
    } catch {
      /* continue */
    }
  }
  return undefined;
}

async function getCode(rpc: any, address: string): Promise<string | null> {
  if (!rpc?.call) return null;
  const calls = [
    (a: string) => rpc.call("omni_getCode", [a, "latest"]),
    (a: string) => rpc.call("eth_getCode", [a, "latest"]),
    (a: string) => rpc.call("chain_getCode", [a]),
  ];
  for (const fn of calls) {
    try {
      const res = await fn(address);
      if (typeof res === "string") return res;
    } catch {
      /* continue */
    }
  }
  return null;
}

function normalizeTx(tx: any): Tx {
  const out: Tx = { ...tx };
  out.blockNumber = tx.blockNumber != null ? toNum(tx.blockNumber) ?? tx.blockNumber : null;
  out.timestamp = toNum(tx.timestamp) ?? tx.timestamp;
  out.gas = toNum(tx.gas) ?? tx.gas;
  out.gasPrice = toBigIntString(tx.gasPrice) ?? tx.gasPrice;
  out.nonce = toNum(tx.nonce) ?? tx.nonce;
  out.status = typeof tx.status === "boolean" ? (tx.status ? 1 : 0) : (toNum(tx.status) ?? tx.status);
  return out;
}
