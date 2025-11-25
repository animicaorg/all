import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useExplorerStore } from "../../state/store";
import TxTable from "../../components/tables/TxTable";
import { shortHash } from "../../utils/format";

type Tx = {
  hash: string;
  from?: string;
  to?: string;
  method?: string;
  value?: string | number;
  gasUsed?: number;
  status?: "success" | "failed" | "pending";
  blockNumber?: number;
  timestamp?: number;
  [k: string]: any;
};

type PageResult = {
  items: Tx[];
  total: number;
};

const DEFAULT_PAGE_SIZE = 25;

export default function TxPage(): JSX.Element {
  const store = useExplorerStore((s) => s as any);
  const rpc = store?.services?.rpc || store?.network?.rpc;
  const explorerApi = store?.services?.explorerApi;
  const txsState = store?.txs;

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);

  // Filters
  const [qAddr, setQAddr] = useState("");
  const [qStatus, setQStatus] = useState<"" | "success" | "failed" | "pending">("");
  const [qMethod, setQMethod] = useState("");
  const [qMinHeight, setQMinHeight] = useState<string>("");
  const [qMaxHeight, setQMaxHeight] = useState<string>("");

  const [data, setData] = useState<PageResult>({ items: [], total: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | undefined>(undefined);

  const filters = useMemo(
    () => ({
      address: qAddr.trim() || undefined,
      status: qStatus || undefined,
      method: qMethod.trim() || undefined,
      minHeight: qMinHeight ? Number(qMinHeight) : undefined,
      maxHeight: qMaxHeight ? Number(qMaxHeight) : undefined,
    }),
    [qAddr, qStatus, qMethod, qMinHeight, qMaxHeight]
  );

  const totalPages = useMemo(
    () => Math.max(1, Math.ceil((data.total || 0) / pageSize)),
    [data.total, pageSize]
  );

  const resetToFirstPage = useCallback(() => setPage(1), []);

  const fetchPage = useCallback(
    async (pageNum: number, pSize: number) => {
      setLoading(true);
      setError(undefined);
      try {
        // 1) Prefer store-backed pagination if available
        if (txsState?.fetchPage) {
          const res: PageResult = await txsState.fetchPage({ page: pageNum, pageSize: pSize, filters });
          setData(res);
          setLoading(false);
          return;
        }

        // 2) Explorer API adapter (REST)
        if (explorerApi?.txs?.list) {
          const res: PageResult = await explorerApi.txs.list({ page: pageNum, pageSize: pSize, ...filters });
          setData(res);
          setLoading(false);
          return;
        }

        // 3) Node RPC fallbacks (best-effort; try a few common shapes)
        if (rpc?.call) {
          // omni_getTransactions(page, pageSize, filters)
          try {
            const res = await rpc.call("omni_getTransactions", [pageNum, pSize, filters]);
            const items: Tx[] = res?.items ?? res?.txs ?? [];
            const total: number = res?.total ?? res?.count ?? items.length;
            setData({ items, total });
            setLoading(false);
            return;
          } catch {
            /* continue */
          }
          // omni_getRecentTransactions(limit, offset)
          try {
            const offset = (pageNum - 1) * pSize;
            const res = await rpc.call("omni_getRecentTransactions", [pSize, offset]);
            const items: Tx[] = Array.isArray(res) ? res : res?.items ?? [];
            const total: number = res?.total ?? res?.count ?? offset + items.length + (items.length === pSize ? pSize : 0);
            setData({ items, total });
            setLoading(false);
            return;
          } catch {
            /* continue */
          }
          // omni_getTxs({page, pageSize, ...filters})
          try {
            const res = await rpc.call("omni_getTxs", [{ page: pageNum, pageSize: pSize, ...filters }]);
            const items: Tx[] = res?.items ?? res?.txs ?? [];
            const total: number = res?.total ?? res?.count ?? items.length;
            setData({ items, total });
            setLoading(false);
            return;
          } catch {
            /* continue */
          }
        }

        throw new Error("No transactions API available on this backend.");
      } catch (e: any) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    },
    [txsState, explorerApi, rpc, filters]
  );

  useEffect(() => {
    fetchPage(page, pageSize);
  }, [fetchPage, page, pageSize]);

  // Pagination handlers
  const prev = () => setPage((p) => Math.max(1, p - 1));
  const next = () => setPage((p) => Math.min(totalPages, p + 1));
  const goto = (n: number) => setPage(() => Math.min(Math.max(1, n), totalPages));

  return (
    <div className="page page-tx">
      <header className="page-header">
        <div>
          <h1>Transactions</h1>
          <p className="dim">Browse on-chain transactions with quick filters</p>
        </div>
        <div className="actions">
          <button
            className="btn"
            onClick={() => fetchPage(page, pageSize)}
            disabled={loading}
            title="Refresh"
          >
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>
      </header>

      <section className="card">
        <div className="card-header">
          <h2 className="card-title">Filters</h2>
        </div>
        <div className="card-body">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
            <div className="form-row">
              <label className="label" htmlFor="f-addr">Address (from/to)</label>
              <input
                id="f-addr"
                className="input mono"
                placeholder="0x… or bech32…"
                value={qAddr}
                onChange={(e) => setQAddr(e.target.value)}
              />
            </div>
            <div className="form-row">
              <label className="label" htmlFor="f-status">Status</label>
              <select
                id="f-status"
                className="input"
                value={qStatus}
                onChange={(e) => setQStatus(e.target.value as any)}
              >
                <option value="">Any</option>
                <option value="success">Success</option>
                <option value="failed">Failed</option>
                <option value="pending">Pending</option>
              </select>
            </div>
            <div className="form-row">
              <label className="label" htmlFor="f-method">Method</label>
              <input
                id="f-method"
                className="input"
                placeholder="transfer, approve, deploy…"
                value={qMethod}
                onChange={(e) => setQMethod(e.target.value)}
              />
            </div>
            <div className="form-row">
              <label className="label" htmlFor="f-minh">Min Height</label>
              <input
                id="f-minh"
                className="input"
                type="number"
                min={0}
                inputMode="numeric"
                placeholder="e.g. 1000"
                value={qMinHeight}
                onChange={(e) => setQMinHeight(e.target.value)}
              />
            </div>
            <div className="form-row">
              <label className="label" htmlFor="f-maxh">Max Height</label>
              <input
                id="f-maxh"
                className="input"
                type="number"
                min={0}
                inputMode="numeric"
                placeholder="e.g. 2000"
                value={qMaxHeight}
                onChange={(e) => setQMaxHeight(e.target.value)}
              />
            </div>
            <div className="form-row">
              <label className="label">&nbsp;</label>
              <div className="flex gap-2">
                <button className="btn" onClick={() => { resetToFirstPage(); fetchPage(1, pageSize); }}>
                  Apply
                </button>
                <button
                  className="btn ghost"
                  onClick={() => {
                    setQAddr("");
                    setQStatus("");
                    setQMethod("");
                    setQMinHeight("");
                    setQMaxHeight("");
                    resetToFirstPage();
                    fetchPage(1, pageSize);
                  }}
                >
                  Reset
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="card mt-3">
        <div className="card-header">
          <h2 className="card-title">Results</h2>
          <div className="card-actions">
            <div className="flex items-center gap-2">
              <span className="dim">Page size</span>
              <select
                className="input"
                value={pageSize}
                onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
              >
                {[10, 25, 50, 100].map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </div>
          </div>
        </div>

        <div className="card-body">
          {error ? (
            <div className="alert warn">{error}</div>
          ) : loading && data.items.length === 0 ? (
            <p className="dim">Loading transactions…</p>
          ) : data.items.length === 0 ? (
            <p className="dim">No transactions match your filters.</p>
          ) : (
            <TxTable txs={data.items} />
          )}
        </div>

        <div className="card-footer">
          <div className="flex items-center justify-between w-full">
            <div className="dim">
              Showing <strong>{data.items.length}</strong> of <strong>{data.total}</strong>
              {" "}· Page <strong>{page}</strong> / {totalPages}
            </div>
            <div className="flex items-center gap-2">
              <button className="btn" onClick={() => goto(1)} disabled={page <= 1}>« First</button>
              <button className="btn" onClick={prev} disabled={page <= 1}>‹ Prev</button>
              <button className="btn" onClick={next} disabled={page >= totalPages}>Next ›</button>
              <button className="btn" onClick={() => goto(totalPages)} disabled={page >= totalPages}>Last »</button>
            </div>
          </div>
        </div>
      </section>

      <section className="mt-3">
        <p className="dim small">
          Tip: Click a hash to view details. Example: {data.items[0]?.hash ? shortHash(data.items[0].hash) : "—"}
        </p>
      </section>
    </div>
  );
}
