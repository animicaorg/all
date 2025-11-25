import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { classNames } from "../../utils/classnames";
import { shortHash } from "../../utils/format";
import { ago } from "../../utils/time";
import * as AICFService from "../../services/aicf";

// -------------------------------------------------------------------------------------
// Types
// -------------------------------------------------------------------------------------
type Settlement = {
  epoch?: number;
  providerId?: string;
  providerName?: string;
  jobs?: number;
  units?: number;
  payout?: string | number | bigint;
  timestamp?: number; // ms since epoch (end of epoch or settlement time)
};

type SettlementsQuery = {
  providerId?: string;
  from?: number;
  to?: number;
  limit?: number;
  offset?: number;
};

// -------------------------------------------------------------------------------------
// Constants
// -------------------------------------------------------------------------------------
const REFRESH_MS_DEFAULT = 30_000;

const RANGES = [
  { id: "24h", label: "24h", ms: 24 * 60 * 60 * 1000 },
  { id: "7d", label: "7d", ms: 7 * 24 * 60 * 60 * 1000 },
  { id: "30d", label: "30d", ms: 30 * 24 * 60 * 60 * 1000 },
] as const;

// -------------------------------------------------------------------------------------
// Utils
// -------------------------------------------------------------------------------------
function isHex(x: unknown): x is string {
  return typeof x === "string" && /^0x[0-9a-fA-F]+$/.test(x);
}
function toNumber(x: unknown): number {
  if (typeof x === "number") return x;
  if (typeof x === "string") {
    if (isHex(x)) {
      try {
        return Number(BigInt(x));
      } catch {
        return 0;
      }
    }
    const n = Number(x);
    return Number.isFinite(n) ? n : 0;
  }
  if (typeof x === "bigint") {
    const max = BigInt(Number.MAX_SAFE_INTEGER);
    return x > max ? Number(max) : Number(x);
  }
  return 0;
}
function toBigInt(x: unknown): bigint {
  if (typeof x === "bigint") return x;
  if (typeof x === "number") return BigInt(Math.trunc(x));
  if (typeof x === "string") {
    if (isHex(x)) return BigInt(x);
    // decimal string
    if (/^\d+$/.test(x)) return BigInt(x);
    // try float string -> scale to 18 decimals
    if (/^\d+(\.\d+)?$/.test(x)) {
      const [i, f = ""] = x.split(".");
      const frac = (f + "000000000000000000").slice(0, 18);
      return BigInt(i) * TEN18 + BigInt(frac);
    }
  }
  return 0n;
}
const TEN18 = 10n ** 18n;

function formatToken(value: bigint, decimals = 18, symbol = "ANM"): string {
  if (decimals < 0) decimals = 0;
  const scale = 10n ** BigInt(decimals);
  const sign = value < 0n ? "-" : "";
  const v = value < 0n ? -value : value;
  const int = v / scale;
  const frac = v % scale;

  // show up to 4 decimal places, trim trailing zeros
  const viewDec = Math.min(4, decimals);
  if (viewDec === 0) return `${sign}${int.toString()} ${symbol}`;

  const fracStrRaw = (frac + scale).toString().slice(1); // left-pad with zeros
  const fracStr = fracStrRaw.slice(0, viewDec).replace(/0+$/, "");
  return `${sign}${int.toString()}${fracStr ? "." + fracStr : ""} ${symbol}`;
}

function sumBigint(values: (bigint | undefined)[]): bigint {
  let s = 0n;
  for (const v of values) s += v ?? 0n;
  return s;
}

function normalizeSettlement(x: any): Settlement {
  return {
    epoch: toNumber(x.epoch ?? x.round ?? x.height),
    providerId: x.providerId ?? x.provider ?? x.address,
    providerName: x.providerName ?? x.provider_label,
    jobs: toNumber(x.jobs ?? x.jobCount),
    units: toNumber(x.units ?? x.usageUnits),
    payout: x.payout ?? x.amount ?? x.totalPayout,
    timestamp: toNumber(x.timestamp ?? x.time ?? x.settledAt),
  };
}

// -------------------------------------------------------------------------------------
// Small inline sparkline (self-contained to avoid external chart deps)
// -------------------------------------------------------------------------------------
function Sparkline({
  data,
  width = 240,
  height = 48,
  strokeWidth = 2,
  title,
}: {
  data: number[];
  width?: number;
  height?: number;
  strokeWidth?: number;
  title?: string;
}) {
  const pad = 4;
  const n = data.length;
  if (!n) {
    return (
      <svg width={width} height={height} aria-label={title}>
        <text x={width / 2} y={height / 2} textAnchor="middle" dominantBaseline="middle" fontSize="10" fill="currentColor" opacity="0.6">
          no data
        </text>
      </svg>
    );
  }
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;

  const dx = (width - pad * 2) / Math.max(1, n - 1);
  const points = data.map((v, i) => {
    const x = pad + i * dx;
    const y = height - pad - ((v - min) / range) * (height - pad * 2);
    return [x, y] as const;
  });

  const d = points.map((p, i) => (i === 0 ? `M ${p[0]},${p[1]}` : `L ${p[0]},${p[1]}`)).join(" ");

  // area under curve
  const area =
    d +
    ` L ${pad + (n - 1) * dx},${height - pad}` +
    ` L ${pad},${height - pad}` +
    " Z";

  return (
    <svg width={width} height={height} aria-label={title}>
      <path d={area} fill="currentColor" opacity="0.12" />
      <path d={d} fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// -------------------------------------------------------------------------------------
// Page
// -------------------------------------------------------------------------------------
export default function SettlementsPage() {
  const [range, setRange] = useState<(typeof RANGES)[number]>(RANGES[1]); // default 7d
  const [provider, setProvider] = useState<string>("");
  const [page, setPage] = useState(0);
  const pageSize = 50;

  const [rows, setRows] = useState<Settlement[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string>();

  const [decimals, setDecimals] = useState<number>(18);
  const [symbol, setSymbol] = useState<string>("ANM");

  const timer = useRef<number | undefined>(undefined);

  const refresh = useCallback(async () => {
    setErr(undefined);
    setLoading(true);
    try {
      const now = Date.now();
      const from = now - range.ms;

      const q: SettlementsQuery = {
        providerId: provider || undefined,
        from,
        to: now,
        limit: pageSize,
        offset: page * pageSize,
      };

      // Flexible adapters: prefer listSettlements(options), fallback to getSettlements(provider, options)
      const resp: any =
        (await (AICFService as any).listSettlements?.(q)) ??
        (provider
          ? await (AICFService as any).getSettlements?.(provider, q)
          : await (AICFService as any).getSettlements?.(undefined, q)) ??
        [];

      // Some APIs may return { items, token:{decimals,symbol} }
      if (resp && Array.isArray(resp.items)) {
        setRows(resp.items.map(normalizeSettlement));
        if (resp.token) {
          if (Number.isFinite(resp.token.decimals)) setDecimals(Number(resp.token.decimals));
          if (typeof resp.token.symbol === "string" && resp.token.symbol.length) setSymbol(resp.token.symbol);
        }
      } else {
        setRows((Array.isArray(resp) ? resp : []).map(normalizeSettlement));
      }
    } catch (e: any) {
      setErr(e?.message || String(e));
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, provider, range.ms]);

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range, provider, page]);

  useEffect(() => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(refresh, REFRESH_MS_DEFAULT);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [refresh]);

  // Aggregations
  const totals = useMemo(() => {
    const payouts = rows.map((r) => toBigInt(r.payout));
    const jobs = rows.map((r) => toNumber(r.jobs));
    const units = rows.map((r) => toNumber(r.units));
    return {
      payout: sumBigint(payouts),
      jobs: jobs.reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0),
      units: units.reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0),
      providers: new Set(rows.map((r) => r.providerId || "")).size,
      epochs: new Set(rows.map((r) => r.epoch || 0)).size,
    };
  }, [rows]);

  // Build epoch series (sum payout per epoch)
  const epochSeries = useMemo(() => {
    const m = new Map<number, bigint>();
    for (const r of rows) {
      const k = r.epoch || 0;
      m.set(k, (m.get(k) ?? 0n) + toBigInt(r.payout));
    }
    const sorted = Array.from(m.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([, v]) => v);
    const nums = sorted.map((v) => Number(v / 10n ** BigInt(Math.max(0, decimals - 6)))); // scale to avoid overflow
    return nums;
  }, [decimals, rows]);

  const totalInPage = rows.length;

  return (
    <section className="page">
      <header className="page-header">
        <div className="row wrap gap-12 items-end">
          <h1>AICF Settlements</h1>
          <div className="row gap-8">
            <RangeSelector value={range.id} onChange={(id) => setRange(RANGES.find((r) => r.id === id) || RANGES[0])} />
          </div>
        </div>

        <div className="row wrap gap-10 mt-3 items-end">
          <input
            className="input"
            placeholder="Filter by provider (addr or name)"
            value={provider}
            onChange={(e) => {
              setPage(0);
              setProvider(e.target.value);
            }}
            spellCheck={false}
          />
          <button className="btn" onClick={() => refresh()} disabled={loading}>
            Refresh
          </button>
          {err ? <div className="alert warn">{err}</div> : null}
        </div>

        <section className="grid cols-4 gap-12 mt-3">
          <StatCard label="Total payout" value={formatToken(totals.payout, decimals, symbol)} />
          <StatCard label="Jobs settled" value={fmtInt(totals.jobs)} />
          <StatCard label="Usage units" value={fmtInt(totals.units)} />
          <StatCard label="Providers / Epochs" value={`${fmtInt(totals.providers)} / ${fmtInt(totals.epochs)}`} />
        </section>

        <section className="panel mt-3">
          <div className="row space-between items-center">
            <h3 className="m-0">Epoch payout trend</h3>
            <small className="dim">Range: {range.label}</small>
          </div>
          <div className="mt-2">
            <Sparkline data={epochSeries} width={520} height={80} title="Payouts by epoch" />
          </div>
        </section>
      </header>

      <div className="table-wrap mt-3">
        <table className="table">
          <thead>
            <tr>
              <th>Epoch</th>
              <th>Provider</th>
              <th className="right">Jobs</th>
              <th className="right">Units</th>
              <th className="right">Payout</th>
              <th>Settled</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const payout = toBigInt(r.payout);
              const prov = r.providerName || r.providerId || "";
              return (
                <tr key={`${r.epoch}-${prov}-${i}`}>
                  <td className="mono">{Number.isFinite(r.epoch || NaN) ? r.epoch : "—"}</td>
                  <td className="mono" title={r.providerId || undefined}>
                    {prov ? shortHash(prov, 10) : "—"}
                  </td>
                  <td className="right">{fmtInt(r.jobs)}</td>
                  <td className="right">{fmtInt(r.units)}</td>
                  <td className="right mono">{formatToken(payout, decimals, symbol)}</td>
                  <td title={r.timestamp ? new Date(r.timestamp).toISOString() : undefined}>
                    {r.timestamp ? ago(r.timestamp) : "—"}
                  </td>
                </tr>
              );
            })}
            {!rows.length && !loading ? (
              <tr>
                <td className="center dim" colSpan={6}>
                  No settlements found for the selected filters.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <div className="row gap-8 items-center mt-3">
        <button className="btn small" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}>
          Prev
        </button>
        <span className="dim small">
          Page {page + 1} • showing {totalInPage}
        </span>
        <button className="btn small" onClick={() => setPage((p) => p + 1)} disabled={rows.length < pageSize}>
          Next
        </button>
        {loading ? <span className="dim small">Loading…</span> : null}
      </div>
    </section>
  );
}

// -------------------------------------------------------------------------------------
// UI bits
// -------------------------------------------------------------------------------------
function RangeSelector({
  value,
  onChange,
}: {
  value: (typeof RANGES)[number]["id"];
  onChange: (val: (typeof RANGES)[number]["id"]) => void;
}) {
  return (
    <div className="row gap-6">
      {RANGES.map((r) => (
        <button
          key={r.id}
          className={classNames("btn", "tiny", value === r.id && "primary")}
          onClick={() => onChange(r.id)}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="panel">
      <div className="dim small">{label}</div>
      <div className="h2 mt-1">{value}</div>
    </div>
  );
}

function fmtInt(n?: number): string {
  if (!Number.isFinite(n || NaN)) return "—";
  const v = n as number;
  if (Math.abs(v) >= 1_000_000_000) return (v / 1_000_000_000).toFixed(2) + "B";
  if (Math.abs(v) >= 1_000_000) return (v / 1_000_000).toFixed(2) + "M";
  if (Math.abs(v) >= 10_000) return Math.round(v / 1_000) + "k";
  return String(Math.round(v));
}
