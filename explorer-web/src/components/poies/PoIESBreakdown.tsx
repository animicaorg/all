import React, { useMemo } from "react";
import { DonutChart } from "../charts/DonutChart";
import {
  resolveTheme,
  seriesColor,
  FORMATS_DEFAULT,
  makeNumberFormats,
} from "../charts/chart.theme";
import {
  aggregatePsiByType,
  applyCaps,
  computeAcceptanceS,
} from "../../utils/poies_math";
import { cn } from "../../utils/classnames";

// If your store slice is available, we'll read live data from it.
// (Falls back to props if the slice/values are not present.)
import { useStore } from "../../state/store";

type PsiMap = Record<string, number>;
type CapsMap = Record<string, number>;

export interface PoIESBreakdownProps {
  /** Optional snapshot of recent samples: each entry contains per-type ψ. */
  samples?: Array<{ psi: PsiMap }>;
  /** Optional per-type caps used to clip Σψ before computing S. */
  caps?: CapsMap;
  /** Optional pre-computed S (acceptance). If missing we compute it. */
  acceptanceS?: number;
  /** Explicit theme mode; defaults to system preference. */
  themeMode?: "light" | "dark";
  className?: string;
}

/**
 * PoIESBreakdown
 * - Aggregates Σψ across proof types (e.g., ZK, AI, Quantum, DA)
 * - Applies per-type caps (if configured)
 * - Shows acceptance S based on capped totals
 * - Renders a donut for mix and a tabular breakdown
 */
export const PoIESBreakdown: React.FC<PoIESBreakdownProps> = ({
  samples,
  caps,
  acceptanceS,
  themeMode,
  className,
}) => {
  // Prefer live store data if available; otherwise use props.
  const storeMix = useStore((s: any) => s?.poies?.mix as Array<{ psi: PsiMap }> | undefined);
  const storeCaps = useStore((s: any) => s?.poies?.caps as CapsMap | undefined);
  const storeS = useStore((s: any) => s?.poies?.acceptanceS as number | undefined);

  const _samples = samples ?? storeMix ?? [];
  const _caps: CapsMap | undefined = caps ?? storeCaps;
  const F = makeNumberFormats();

  const totals = useMemo(() => aggregatePsiByType(_samples), [_samples]);
  const totalsCapped = useMemo(() => (_caps ? applyCaps(totals, _caps) : totals), [totals, _caps]);

  const sumPsi = useMemo(
    () => Object.values(totals).reduce((a, b) => a + (isFinite(b) ? b : 0), 0),
    [totals]
  );
  const sumPsiCapped = useMemo(
    () => Object.values(totalsCapped).reduce((a, b) => a + (isFinite(b) ? b : 0), 0),
    [totalsCapped]
  );

  const S =
    typeof acceptanceS === "number"
      ? acceptanceS
      : typeof storeS === "number"
      ? storeS
      : computeAcceptanceS(totalsCapped, _caps ?? {});

  const theme = resolveTheme(themeMode);

  const rows = useMemo(() => {
    const keys = Object.keys(totalsCapped).sort((a, b) => (totalsCapped[b] ?? 0) - (totalsCapped[a] ?? 0));
    return keys.map((k, i) => {
      const value = totals[k] ?? 0;
      const valueCapped = totalsCapped[k] ?? 0;
      const cap = _caps?.[k];
      const share = sumPsiCapped > 0 ? valueCapped / sumPsiCapped : 0;
      return {
        key: k,
        color: seriesColor(i, theme),
        value,
        valueCapped,
        cap,
        share,
      };
    });
  }, [totals, totalsCapped, _caps, sumPsiCapped, theme]);

  const donutData = useMemo(
    () =>
      rows.map((r) => ({
        label: r.key,
        value: r.valueCapped,
        color: r.color,
      })),
    [rows]
  );

  if ((_samples?.length ?? 0) === 0 && Object.keys(totals).length === 0) {
    return (
      <div
        className={cn(
          "rounded-md border",
          "p-4",
          className
        )}
        style={{ background: theme.surface, borderColor: theme.grid }}
      >
        <div style={{ color: theme.textMuted }}>No PoIES data yet.</div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "rounded-md border",
        "p-4 md:p-5",
        "grid gap-4 md:gap-6",
        "md:grid-cols-[minmax(260px,360px)_1fr]",
        className
      )}
      style={{ background: theme.surface, borderColor: theme.grid }}
    >
      {/* Left: Donut + KPIs */}
      <div className="flex flex-col gap-4 md:gap-5">
        <div className="text-base font-semibold" style={{ color: theme.textPrimary }}>
          PoIES Mix (Σψ per proof type)
        </div>

        <DonutChart
          data={donutData}
          innerRadius={0.62}
          outerRadius={0.92}
          themeMode={theme.mode}
          centerLabelPrimary={F.number(sumPsiCapped)}
          centerLabelSecondary="Σψ (capped)"
        />

        <div className="grid grid-cols-2 gap-3">
          <KPI
            label="Σψ (raw)"
            value={F.number(sumPsi)}
            hint={sumPsiCapped !== sumPsi ? "Capped totals differ" : undefined}
            themeMode={theme.mode}
          />
          <KPI
            label="Acceptance S"
            value={FORMATS_DEFAULT.percent(S)}
            hint="Derived from capped mix"
            themeMode={theme.mode}
          />
        </div>
      </div>

      {/* Right: Breakdown table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm" style={{ color: theme.textPrimary }}>
          <thead>
            <tr style={{ color: theme.textMuted }}>
              <th className="text-left font-medium pb-2 pr-3">Proof type</th>
              <th className="text-right font-medium pb-2 pr-3 whitespace-nowrap">ψ</th>
              <th className="text-right font-medium pb-2 pr-3 whitespace-nowrap">cap</th>
              <th className="text-right font-medium pb-2 pr-3 whitespace-nowrap">ψ (capped)</th>
              <th className="text-left font-medium pb-2">mix</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="align-middle">
                <td className="py-2 pr-3">
                  <div className="flex items-center gap-2">
                    <span
                      aria-hidden
                      className="inline-block rounded-sm"
                      style={{
                        width: 10,
                        height: 10,
                        background: r.color,
                      }}
                    />
                    <span>{labelize(r.key)}</span>
                  </div>
                </td>
                <td className="py-2 pr-3 text-right tabular-nums">{F.number(r.value)}</td>
                <td className="py-2 pr-3 text-right tabular-nums">
                  {typeof r.cap === "number" ? F.number(r.cap) : "—"}
                </td>
                <td className="py-2 pr-3 text-right tabular-nums">{F.number(r.valueCapped)}</td>
                <td className="py-2">
                  <Bar value={r.share} color={r.color} themeMode={theme.mode} />
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr style={{ color: theme.textMuted }}>
              <td className="pt-2 pr-3">Totals</td>
              <td className="pt-2 pr-3 text-right tabular-nums">{F.number(sumPsi)}</td>
              <td className="pt-2 pr-3 text-right tabular-nums">
                {(_caps && Object.keys(_caps).length > 0) ? "—" : "—"}
              </td>
              <td className="pt-2 pr-3 text-right tabular-nums">{F.number(sumPsiCapped)}</td>
              <td className="pt-2" />
            </tr>
          </tfoot>
        </table>

        <p className="mt-3 text-xs leading-5" style={{ color: theme.textMuted }}>
          Caps are applied per proof type before computing the acceptance metric S.
          Mix bars reflect the share of capped Σψ. If caps are unset, capped and raw totals are identical.
        </p>
      </div>
    </div>
  );
};

/* --------------------------------- UI bits -------------------------------- */

const KPI: React.FC<{
  label: string;
  value: string;
  hint?: string;
  themeMode?: "light" | "dark";
}> = ({ label, value, hint, themeMode }) => {
  const theme = resolveTheme(themeMode);
  return (
    <div
      className="rounded-md border p-3"
      style={{ borderColor: theme.grid, background: theme.background }}
    >
      <div className="text-xs" style={{ color: theme.textMuted }}>
        {label}
      </div>
      <div className="text-lg font-semibold leading-7" style={{ color: theme.textPrimary }}>
        {value}
      </div>
      {hint ? (
        <div className="text-[11px] mt-0.5" style={{ color: theme.textMuted }}>
          {hint}
        </div>
      ) : null}
    </div>
  );
};

const Bar: React.FC<{
  value: number; // 0..1
  color: string;
  themeMode?: "light" | "dark";
}> = ({ value, color, themeMode }) => {
  const theme = resolveTheme(themeMode);
  const v = Math.max(0, Math.min(1, isFinite(value) ? value : 0));
  return (
    <div
      className="w-full h-2 rounded"
      aria-valuemin={0}
      aria-valuemax={1}
      aria-valuenow={v}
      role="progressbar"
      style={{
        background: theme.withAlpha(color, 0.15),
      }}
      title={`${FORMATS_DEFAULT.percent(v)} of capped Σψ`}
    >
      <div
        className="h-2 rounded"
        style={{
          width: `${v * 100}%`,
          background: color,
          transition: "width 180ms ease",
        }}
      />
    </div>
  );
};

/* --------------------------------- utils ---------------------------------- */

function labelize(key: string): string {
  // Pretty-print common proof identifiers; fall back to title case.
  const k = key.toLowerCase();
  if (k === "zk" || k === "zero_knowledge") return "ZK";
  if (k === "ai") return "AI";
  if (k === "quantum" || k === "q") return "Quantum";
  if (k === "da" || k === "data_availability") return "DA";
  if (k === "classical") return "Classical";
  return key
    .replace(/[_\-]+/g, " ")
    .replace(/\b\w/g, (m) => m.toUpperCase());
}

export default PoIESBreakdown;
