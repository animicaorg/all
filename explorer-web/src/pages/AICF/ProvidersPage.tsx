import React, { useEffect, useMemo, useRef, useState } from "react";
import { classNames } from "../../utils/classnames";
import { shortHash } from "../../utils/format";
import DonutChart from "../../components/charts/DonutChart";
// Service is intentionally imported with a wildcard to keep compatibility with
// different shapes in the mock/real implementations.
import * as AICFService from "../../services/aicf";

/**
 * ProvidersPage
 *  - Lists AICF providers in a sortable/filterable table
 *  - Renders a Donut showing stake share distribution
 *  - Auto-refreshes periodically, resilient to hex/decimal stake formats
 */

// ---- Types kept loose on purpose (match service return without tight coupling) ----
type Provider = {
  id?: string;
  address?: string;
  name?: string;
  // Stake can be a number, decimal string, or hex-encoded string (0x…)
  stake?: number | string;
  // Optional metadata
  lastSeen?: number;
  meta?: Record<string, unknown>;
};

// ---- Refresh & UI config ----
const REFRESH_MS = 30_000;
const DONUT_TOP = 6;

// ---- Helpers ----
function isHexString(x: unknown): x is string {
  return typeof x === "string" && /^0x[0-9a-fA-F]+$/.test(x);
}
function toNumericStake(stake: Provider["stake"]): number {
  if (typeof stake === "number") return stake;
  if (typeof stake === "string") {
    if (isHexString(stake)) {
      try {
        return Number(BigInt(stake));
      } catch {
        return 0;
      }
    }
    const n = Number(stake);
    return Number.isFinite(n) ? n : 0;
  }
  return 0;
}
function fmtInt(n: number): string {
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000) return (n / 1_000_000_000).toFixed(2) + "B";
  if (abs >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (abs >= 10_000) return Math.round(n / 1_000) + "k";
  return String(Math.round(n));
}
function pct(n: number, d: number): string {
  if (!(Number.isFinite(n) && Number.isFinite(d)) || d <= 0) return "—";
  return ((n / d) * 100).toFixed(2) + "%";
}
function byNumericStakeDesc(a: Provider, b: Provider) {
  return toNumericStake(b.stake) - toNumericStake(a.stake);
}

// ---- Component ----
export default function ProvidersPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string>();
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<"stake" | "name" | "address">("stake");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const timer = useRef<number | undefined>(undefined);

  async function refresh() {
    setErr(undefined);
    try {
      const list = (await (AICFService as any).listProviders?.()) as Provider[] | undefined;
      setProviders(Array.isArray(list) ? list : []);
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    timer.current = window.setInterval(refresh, REFRESH_MS);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    let arr = providers.slice();
    if (q) {
      arr = arr.filter((p) => {
        const name = (p.name || "").toLowerCase();
        const addr = (p.address || "").toLowerCase();
        return name.includes(q) || addr.includes(q);
      });
    }
    arr.sort((a, b) => {
      if (sortKey === "stake") {
        const d = toNumericStake(a.stake) - toNumericStake(b.stake);
        return sortDir === "asc" ? d : -d;
      } else if (sortKey === "name") {
        const d = (a.name || "").localeCompare(b.name || "");
        return sortDir === "asc" ? d : -d;
      } else {
        const d = (a.address || "").localeCompare(b.address || "");
        return sortDir === "asc" ? d : -d;
      }
    });
    return arr;
  }, [providers, query, sortKey, sortDir]);

  const totalStake = useMemo(
    () => providers.reduce((acc, p) => acc + toNumericStake(p.stake), 0),
    [providers]
  );

  const donutData = useMemo(() => {
    const top = providers.slice().sort(byNumericStakeDesc).slice(0, DONUT_TOP);
    const topSum = top.reduce((a, p) => a + toNumericStake(p.stake), 0);
    const rest = Math.max(totalStake - topSum, 0);
    const slices = top.map((p) => ({
      label: p.name || shortHash(p.address || p.id || "", 6),
      value: Math.max(0, toNumericStake(p.stake)),
    }));
    if (rest > 0) slices.push({ label: "Others", value: rest });
    return slices;
  }, [providers, totalStake]);

  function setSort(next: "stake" | "name" | "address") {
    if (next === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(next);
      setSortDir(next === "stake" ? "desc" : "asc");
    }
  }

  return (
    <section className="page">
      <header className="page-header">
        <h1>AICF Providers</h1>
        <div className="row gap-12">
          <div className="dim small">Auto-refreshing every {Math.round(REFRESH_MS / 1000)}s</div>
          <button className="btn small" onClick={() => refresh()} disabled={loading}>
            Refresh
          </button>
        </div>
      </header>

      {err ? <div className="alert warn">{err}</div> : null}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-20 items-start">
        <div className="card lg:col-span-1">
          <div className="card-title">Stake Share</div>
          <div className="dim small mb-2">
            Total stake: <strong>{fmtInt(totalStake)}</strong>
          </div>
          {/* DonutChart is typed as any to be resilient to prop-name variances */}
          {/* eslint-disable @typescript-eslint/no-explicit-any */}
          <DonutChart
            // @ts-ignore
            data={donutData as any}
            // Common prop names used across our codebase:
            valueKey="value"
            labelKey="label"
            // Some implementations also accept total; we pass it if present
            // @ts-ignore
            total={totalStake}
          />
          <ul className="list mt-3">
            {donutData.map((s, i) => (
              <li className="row spread small" key={s.label + i}>
                <span className="mono">{s.label}</span>
                <span className="dim">{pct(s.value, totalStake)}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="lg:col-span-2">
          <div className="row spread mb-2">
            <div className="card-title">Providers</div>
            <input
              className="input"
              placeholder="Search by name or address…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              spellCheck={false}
            />
          </div>

          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <Th active={sortKey === "name"} dir={sortDir} onClick={() => setSort("name")}>
                    Provider
                  </Th>
                  <Th active={sortKey === "address"} dir={sortDir} onClick={() => setSort("address")}>
                    Address
                  </Th>
                  <Th right active={sortKey === "stake"} dir={sortDir} onClick={() => setSort("stake")}>
                    Stake
                  </Th>
                  <Th right>Share</Th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((p) => {
                  const stake = toNumericStake(p.stake);
                  return (
                    <tr key={p.id || p.address}>
                      <td>{p.name || <span className="dim">—</span>}</td>
                      <td className="mono">{p.address ? shortHash(p.address, 8) : <span className="dim">—</span>}</td>
                      <td className="right">{fmtInt(stake)}</td>
                      <td className="right dim">{pct(stake, totalStake)}</td>
                    </tr>
                  );
                })}
                {!filtered.length && !loading ? (
                  <tr>
                    <td colSpan={4} className="center dim">
                      No providers found.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          {loading ? <div className="mt-2 dim">Loading…</div> : null}
        </div>
      </div>
    </section>
  );
}

// ---- Presentational bits ----
function Th({
  children,
  onClick,
  active,
  dir,
  right,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  active?: boolean;
  dir?: "asc" | "desc";
  right?: boolean;
}) {
  return (
    <th
      onClick={onClick}
      className={classNames("th", onClick && "th-sortable", active && "active", right && "right")}
      title={onClick ? "Sort" : undefined}
    >
      <span className="row gap-6">
        <span>{children}</span>
        {active ? <SortIcon dir={dir} /> : null}
      </span>
    </th>
  );
}

function SortIcon({ dir }: { dir?: "asc" | "desc" }) {
  return (
    <svg width="10" height="10" viewBox="0 0 16 16" aria-hidden>
      {dir === "asc" ? (
        <path d="M8 3l5 6H3z" fill="currentColor" />
      ) : (
        <path d="M8 13L3 7h10z" fill="currentColor" />
      )}
    </svg>
  );
}
