import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useExplorerStore } from "../../state/store";
import ContractsTable from "../../components/tables/ContractsTable";
import Tag from "../../components/Tag";

type Hex = `0x${string}`;

export type ContractRow = {
  address: Hex;
  name?: string;
  verified?: boolean;
  codeHash?: Hex;
  createdAt?: number;
  creator?: Hex;
  txHash?: Hex;
  [k: string]: any;
};

function isHex(x: any): x is Hex {
  return typeof x === "string" && /^0x[0-9a-fA-F]+$/.test(x);
}

function normalize(row: any): ContractRow | null {
  const address: string | undefined =
    row?.address ?? row?.contract ?? row?.addr ?? row?.contractAddress;
  if (!address || !isHex(address)) return null;
  const codeHash: string | undefined =
    row?.codeHash ?? row?.bytecodeHash ?? row?.hash ?? row?.code_hash;
  const createdAt =
    typeof row?.createdAt === "number"
      ? row.createdAt
      : typeof row?.timestamp === "number"
      ? row.timestamp
      : undefined;
  const creator: string | undefined =
    row?.creator ?? row?.deployer ?? row?.from ?? row?.creatorAddress;

  return {
    address: address as Hex,
    name: row?.name ?? row?.contractName ?? row?.artifactName ?? undefined,
    verified:
      typeof row?.verified === "boolean"
        ? row.verified
        : typeof row?.isVerified === "boolean"
        ? row.isVerified
        : codeHash
        ? true
        : undefined,
    codeHash: isHex(codeHash) ? (codeHash as Hex) : undefined,
    createdAt,
    creator: isHex(creator) ? (creator as Hex) : undefined,
    txHash: isHex(row?.txHash) ? (row.txHash as Hex) : undefined,
    ...row,
  };
}

export default function ContractsPage(): JSX.Element {
  const store = useExplorerStore((s) => s as any);
  const explorerApi = store?.services?.explorerApi;
  const servicesApi = store?.services?.servicesApi;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | undefined>(undefined);
  const [rows, setRows] = useState<ContractRow[]>([]);

  // UI state
  const [query, setQuery] = useState("");
  const [onlyVerified, setOnlyVerified] = useState(true);
  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(1);

  const fetchRemote = useCallback(async (): Promise<ContractRow[]> => {
    // Try services first (explicit verified contracts/artifacts)
    if (servicesApi?.contracts?.verified?.list) {
      const out = await servicesApi.contracts.verified.list({ limit: 1000 }).catch(() => []);
      const items = Array.isArray(out?.items) ? out.items : Array.isArray(out) ? out : [];
      return items.map(normalize).filter(Boolean) as ContractRow[];
    }
    if (servicesApi?.artifacts?.verified?.list) {
      const out = await servicesApi.artifacts.verified.list({ limit: 1000 }).catch(() => []);
      const items = Array.isArray(out?.items) ? out.items : Array.isArray(out) ? out : [];
      return items
        .map((a: any) =>
          normalize({
            address: a?.address,
            name: a?.name ?? a?.contractName,
            codeHash: a?.codeHash,
            verified: true,
            createdAt: a?.createdAt,
          })
        )
        .filter(Boolean) as ContractRow[];
    }

    // Explorer REST (may have /contracts/verified)
    if (explorerApi?.contracts?.verified?.list) {
      const out = await explorerApi.contracts.verified.list({ limit: 1000 }).catch(() => []);
      const items = Array.isArray(out?.items) ? out.items : Array.isArray(out) ? out : [];
      return items.map(normalize).filter(Boolean) as ContractRow[];
    }

    // Fallback: generic contracts list, then filter by "verified" shape
    if (explorerApi?.contracts?.list) {
      const out = await explorerApi.contracts.list({ limit: 1000 }).catch(() => []);
      const items = Array.isArray(out?.items) ? out.items : Array.isArray(out) ? out : [];
      return items.map(normalize).filter(Boolean) as ContractRow[];
    }

    // No backend available
    return [];
  }, [explorerApi, servicesApi]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(undefined);
    setPage(1);
    try {
      const list = await fetchRemote();
      // De-dup by address, prefer entries with verified=true
      const byAddr = new Map<string, ContractRow>();
      for (const r of list) {
        const prev = byAddr.get(r.address);
        if (!prev) byAddr.set(r.address, r);
        else if (!prev.verified && r.verified) byAddr.set(r.address, r);
        else if (r.createdAt && (!prev.createdAt || r.createdAt > prev.createdAt)) byAddr.set(r.address, r);
      }
      // Sort: verified first, then newest
      const sorted = Array.from(byAddr.values()).sort((a, b) => {
        const v = Number(Boolean(b.verified)) - Number(Boolean(a.verified));
        if (v !== 0) return v;
        return (b.createdAt ?? 0) - (a.createdAt ?? 0);
      });
      setRows(sorted);
    } catch (e: any) {
      setError(e?.message || String(e));
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [fetchRemote]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter((r) => {
      if (onlyVerified && !r.verified) return false;
      if (!q) return true;
      const name = (r.name ?? "").toLowerCase();
      const addr = r.address.toLowerCase();
      const code = (r.codeHash ?? "").toLowerCase();
      return name.includes(q) || addr.includes(q) || code.includes(q);
    });
  }, [rows, query, onlyVerified]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const pageClamped = Math.min(page, totalPages);
  const paged = useMemo(() => {
    const start = (pageClamped - 1) * pageSize;
    return filtered.slice(start, start + pageSize);
  }, [filtered, pageClamped, pageSize]);

  return (
    <div className="page page-contracts">
      <header className="page-header">
        <div>
          <h1>Verified Contracts</h1>
          <p className="dim">
            Browse contracts with published source or verified code hashes.{" "}
            <span className="inline">
              Backend:{" "}
              <Tag>
                {servicesApi
                  ? "studio-services"
                  : explorerApi
                  ? "explorer REST"
                  : "RPC-only (limited)"}
              </Tag>
            </span>
          </p>
        </div>
        <div className="actions">
          <button className="btn" onClick={refresh} disabled={loading}>
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>
      </header>

      {error && <div className="alert warn">{error}</div>}

      <section className="card">
        <div className="card-header">
          <h2 className="card-title">Filters</h2>
          <div className="card-actions">
            <span className="dim small">
              Showing {paged.length} of {filtered.length} (total {rows.length})
            </span>
          </div>
        </div>
        <div className="card-body">
          <div className="filters grid grid-cols-1 md:grid-cols-3 gap-3">
            <label className="field">
              <div className="label">Search</div>
              <input
                className="input"
                type="text"
                placeholder="Name, address, or code hash…"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setPage(1);
                }}
              />
            </label>

            <label className="field flex items-center gap-2">
              <input
                type="checkbox"
                checked={onlyVerified}
                onChange={(e) => {
                  setOnlyVerified(e.target.checked);
                  setPage(1);
                }}
              />
              <span>Only verified</span>
            </label>

            <label className="field">
              <div className="label">Per page</div>
              <select
                className="input"
                value={pageSize}
                onChange={(e) => {
                  setPageSize(Number(e.target.value));
                  setPage(1);
                }}
              >
                {[10, 25, 50, 100].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
      </section>

      <section className="card mt-3">
        <div className="card-header">
          <h2 className="card-title">Contracts</h2>
          <div className="card-actions">
            <Pagination
              page={pageClamped}
              totalPages={totalPages}
              onPrev={() => setPage((p) => Math.max(1, p - 1))}
              onNext={() => setPage((p) => Math.min(totalPages, p + 1))}
            />
          </div>
        </div>
        <div className="card-body">
          {paged.length > 0 ? (
            <ContractsTable contracts={paged} />
          ) : loading ? (
            <p className="dim">Loading…</p>
          ) : explorerApi || servicesApi ? (
            <p className="dim">No contracts found matching the filters.</p>
          ) : (
            <p className="dim">
              No REST backend is configured. Connect <code>explorerApi</code> or{" "}
              <code>studio-services</code> to list verified contracts.
            </p>
          )}
        </div>
      </section>
    </div>
  );
}

function Pagination(props: {
  page: number;
  totalPages: number;
  onPrev: () => void;
  onNext: () => void;
}) {
  const { page, totalPages, onPrev, onNext } = props;
  return (
    <div className="pagination flex items-center gap-2">
      <button className="btn small" onClick={onPrev} disabled={page <= 1}>
        Prev
      </button>
      <span className="dim small">
        Page {page} / {totalPages}
      </span>
      <button className="btn small" onClick={onNext} disabled={page >= totalPages}>
        Next
      </button>
    </div>
  );
}
