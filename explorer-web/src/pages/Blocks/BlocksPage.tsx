import React, { useEffect, useMemo, useState } from "react";
import { useExplorerStore } from "../../state/store";
import BlocksTable from "../../components/tables/BlocksTable";
import { shortHash } from "../../utils/format";

type BlockRow = {
  height: number;
  hash: string;
  time?: number;           // ms epoch
  txs?: number;
  producer?: string;
  gasUsed?: number;
  gasLimit?: number;
  // Optional: proof mix (e.g., { ai: 0.3, zk: 0.5, quantum: 0.2 })
  proofMix?: Record<string, number>;
};

type Filters = {
  fromHeight?: number | "";
  toHeight?: number | "";
  producerIncludes?: string;
  includeEmpty?: boolean;
};

const DEFAULT_PAGE_SIZE = 25;

export default function BlocksPage(): JSX.Element {
  const items: BlockRow[] =
    useExplorerStore((s) => (s.blocks?.pageItems ?? s.blocks?.items ?? [])) ||
    [];
  const loading = useExplorerStore((s) => s.blocks?.loading ?? false);
  const page = useExplorerStore((s) => s.blocks?.page ?? 1);
  const pageSize = useExplorerStore(
    (s) => s.blocks?.pageSize ?? DEFAULT_PAGE_SIZE
  );
  const hasNext = useExplorerStore((s) => s.blocks?.hasNext ?? false);
  const hasPrev = useExplorerStore((s) => s.blocks?.hasPrev ?? page > 1);
  const total = useExplorerStore((s) => s.blocks?.total ?? undefined);

  const fetchPage =
    useExplorerStore((s) => s.blocks?.fetchPage) ||
    (async (_page: number, _filters?: Partial<Filters>) => { /* noop fallback */ });

  const setPage =
    useExplorerStore((s) => s.blocks?.setPage) || ((_p: number) => {});

  const setFilters =
    useExplorerStore((s) => s.blocks?.setFilters) ||
    ((_f: Partial<Filters>) => {});

  const storeFilters: Partial<Filters> =
    useExplorerStore((s) => s.blocks?.filters ?? {}) || {};

  // Local editable filter state
  const [filters, updateFilters] = useState<Filters>({
    fromHeight:
      typeof storeFilters.fromHeight === "number"
        ? storeFilters.fromHeight
        : "",
    toHeight:
      typeof storeFilters.toHeight === "number" ? storeFilters.toHeight : "",
    producerIncludes: storeFilters.producerIncludes || "",
    includeEmpty: storeFilters.includeEmpty ?? true,
  });

  // Initial load or when page/filters in store change (external)
  useEffect(() => {
    fetchPage(page, storeFilters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, storeFilters.fromHeight, storeFilters.toHeight, storeFilters.producerIncludes, storeFilters.includeEmpty]);

  // Derived display helpers
  const rangeText = useMemo(() => {
    if (!items.length) return "—";
    const first = items[0]?.height;
    const last = items[items.length - 1]?.height;
    return first && last ? `${Math.min(first, last)}–${Math.max(first, last)}` : "—";
  }, [items]);

  const onApply = (e?: React.FormEvent) => {
    e?.preventDefault();
    const next: Partial<Filters> = {};
    next.fromHeight =
      filters.fromHeight === "" ? undefined : Number(filters.fromHeight);
    next.toHeight =
      filters.toHeight === "" ? undefined : Number(filters.toHeight);
    next.producerIncludes = filters.producerIncludes?.trim()
      ? filters.producerIncludes.trim()
      : undefined;
    next.includeEmpty = !!filters.includeEmpty;

    setFilters(next);
    setPage(1);
    fetchPage(1, next);
  };

  const onReset = () => {
    const cleared: Filters = {
      fromHeight: "",
      toHeight: "",
      producerIncludes: "",
      includeEmpty: true,
    };
    updateFilters(cleared);
    setFilters({});
    setPage(1);
    fetchPage(1, {});
  };

  const onPrev = () => {
    if (!hasPrev) return;
    setPage(page - 1);
    fetchPage(page - 1, storeFilters);
  };
  const onNext = () => {
    if (!hasNext) return;
    setPage(page + 1);
    fetchPage(page + 1, storeFilters);
  };

  return (
    <div className="page page-blocks">
      <header className="page-header">
        <div>
          <h1>Blocks</h1>
          <p className="dim">
            Paginated list of recent blocks with quick filters.
          </p>
        </div>
      </header>

      <section className="card" aria-labelledby="filters-title">
        <div className="card-header">
          <h2 id="filters-title" className="card-title">Filters</h2>
        </div>
        <div className="card-body">
          <form className="grid grid-cols-1 md:grid-cols-5 gap-3" onSubmit={onApply}>
            <div className="field">
              <label htmlFor="fromHeight">From height</label>
              <input
                id="fromHeight"
                type="number"
                inputMode="numeric"
                className="input"
                placeholder="e.g. 1000"
                value={filters.fromHeight}
                onChange={(e) =>
                  updateFilters((f) => ({
                    ...f,
                    fromHeight: e.target.value === "" ? "" : Number(e.target.value),
                  }))
                }
              />
            </div>
            <div className="field">
              <label htmlFor="toHeight">To height</label>
              <input
                id="toHeight"
                type="number"
                inputMode="numeric"
                className="input"
                placeholder="e.g. 5000"
                value={filters.toHeight}
                onChange={(e) =>
                  updateFilters((f) => ({
                    ...f,
                    toHeight: e.target.value === "" ? "" : Number(e.target.value),
                  }))
                }
              />
            </div>
            <div className="field md:col-span-2">
              <label htmlFor="producer">Producer contains</label>
              <input
                id="producer"
                type="text"
                className="input mono"
                placeholder="address, prefix, or hash part"
                value={filters.producerIncludes}
                onChange={(e) =>
                  updateFilters((f) => ({
                    ...f,
                    producerIncludes: e.target.value,
                  }))
                }
              />
              {filters.producerIncludes ? (
                <p className="hint">
                  Matching substring: <code>{shortHash(filters.producerIncludes)}</code>
                </p>
              ) : null}
            </div>
            <div className="field">
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={!!filters.includeEmpty}
                  onChange={(e) =>
                    updateFilters((f) => ({ ...f, includeEmpty: e.target.checked }))
                  }
                />
                <span>Include empty blocks</span>
              </label>
            </div>

            <div className="md:col-span-5 flex gap-2 justify-end">
              <button type="button" className="button ghost" onClick={onReset}>
                Reset
              </button>
              <button type="submit" className="button primary">
                Apply
              </button>
            </div>
          </form>
        </div>
      </section>

      <section className="card" aria-labelledby="list-title">
        <div className="card-header">
          <h2 id="list-title" className="card-title">Results</h2>
          <div className="card-actions">
            <span className="dim">
              Page <strong>{page}</strong>
              {typeof total === "number" ? <> · {total.toLocaleString()} total</> : null}
              {" · "}
              Showing <strong>{items.length}</strong> ({rangeText})
            </span>
          </div>
        </div>

        <div className="card-body">
          {loading && items.length === 0 ? (
            <div className="skeleton-list" role="status" aria-live="polite">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="skeleton-row" />
              ))}
            </div>
          ) : items.length === 0 ? (
            <div className="empty">
              <p>No blocks found for the selected filters.</p>
            </div>
          ) : (
            <BlocksTable blocks={items} />
          )}
        </div>

        <div className="card-footer">
          <div className="pager">
            <button
              className="button"
              onClick={onPrev}
              disabled={!hasPrev || loading}
              aria-label="Previous page"
            >
              ← Prev
            </button>
            <span className="dim">
              Page <strong>{page}</strong> · Size {pageSize}
            </span>
            <button
              className="button"
              onClick={onNext}
              disabled={!hasNext || loading}
              aria-label="Next page"
            >
              Next →
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
