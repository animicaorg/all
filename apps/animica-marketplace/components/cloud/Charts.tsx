'use client';

// Inline-SVG charts for the /cloud console. No chart library, no CDN.
//
// Design notes (dataviz method): every chart here is SINGLE-series — the title names the
// series, so no legend box; thin marks (bars with 4px rounded top corners anchored to the
// baseline, 2px lines); recessive grid (3 horizontal lines); selective direct labels (peak +
// last, not every point); a hover layer with a crosshair + tooltip; and a <details> table
// fallback so the data is readable without color or a pointer. Colors are passed in from the
// app's design tokens and validated for ≥3:1 contrast against the card surface.

import { useMemo, useRef, useState } from 'react';

export interface ChartPoint {
  x: string; // label (e.g. 'YYYY-MM-DD')
  y: number;
}

const W = 640;
const H = 200;
const PAD_L = 46;
const PAD_R = 12;
const PAD_T = 14;
const PAD_B = 26;

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(v)));
  const norm = v / mag;
  const step = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10;
  return step * mag;
}

function shortNum(v: number): string {
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(v % 1_000_000 ? 1 : 0)}M`;
  if (Math.abs(v) >= 1_000) return `${(v / 1_000).toFixed(v % 1_000 ? 1 : 0)}k`;
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

function shortDate(x: string): string {
  // 'YYYY-MM-DD' -> 'MM-DD'; anything else passes through.
  return /^\d{4}-\d{2}-\d{2}$/.test(x) ? x.slice(5) : x;
}

export default function SeriesChart({
  title,
  data,
  kind,
  color,
  yMax,
  fmtY = shortNum,
  fmtTip,
  emptyLabel = 'No data in this period yet.',
  tableLabel = 'View as table',
}: {
  title: string;
  data: ChartPoint[];
  kind: 'bar' | 'line';
  color: string;
  /** Fixed y max (e.g. 100 for a percentage). Otherwise a nice max is computed. */
  yMax?: number;
  fmtY?: (v: number) => string;
  /** Tooltip value formatter; defaults to fmtY. */
  fmtTip?: (v: number) => string;
  emptyLabel?: string;
  tableLabel?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  const max = yMax ?? niceMax(Math.max(0, ...data.map((d) => d.y)));
  const iw = W - PAD_L - PAD_R;
  const ih = H - PAD_T - PAD_B;
  const n = data.length;

  const xOf = (i: number) => PAD_L + (n <= 1 ? iw / 2 : (i * iw) / (n - 1));
  const bandW = n > 0 ? iw / n : iw;
  const bandX = (i: number) => PAD_L + i * bandW;
  const yOf = (v: number) => PAD_T + ih - (max > 0 ? (Math.min(v, max) / max) * ih : 0);

  const linePath = useMemo(() => {
    if (kind !== 'line' || n === 0) return '';
    return data.map((d, i) => `${i === 0 ? 'M' : 'L'}${xOf(i).toFixed(1)},${yOf(d.y).toFixed(1)}`).join(' ');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, kind, max, n]);

  const total = data.reduce((a, d) => a + d.y, 0);
  const peakIdx = data.reduce((best, d, i) => (d.y > (data[best]?.y ?? -1) ? i : best), 0);
  const tip = fmtTip ?? fmtY;

  // Sparse x labels: first, middle, last.
  const xLabelIdx = n <= 3 ? data.map((_, i) => i) : [0, Math.floor((n - 1) / 2), n - 1];

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg || n === 0) return;
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = kind === 'bar'
      ? Math.floor((px - PAD_L) / bandW)
      : Math.round(((px - PAD_L) / iw) * (n - 1));
    setHover(i >= 0 && i < n ? i : null);
  };

  return (
    <div className="panel" style={{ padding: 16, minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
        <h3 style={{ margin: 0, fontSize: 14.5, letterSpacing: '-0.01em' }}>{title}</h3>
        {total > 0 && <span className="muted" style={{ fontSize: 12 }}>{yMax != null ? '' : `total ${tip(total)}`}</span>}
      </div>

      {n === 0 || data.every((d) => d.y === 0) ? (
        <div className="empty" style={{ padding: '28px 12px', fontSize: 13 }}>{emptyLabel}</div>
      ) : (
        <>
          <svg
            ref={svgRef}
            viewBox={`0 0 ${W} ${H}`}
            style={{ width: '100%', height: 'auto', display: 'block', touchAction: 'pan-y' }}
            role="img"
            aria-label={`${title} chart`}
            onMouseMove={onMove}
            onMouseLeave={() => setHover(null)}
          >
            {/* recessive grid: 3 lines + baseline */}
            {[0.25, 0.5, 0.75].map((f) => (
              <g key={f}>
                <line x1={PAD_L} x2={W - PAD_R} y1={PAD_T + ih * (1 - f)} y2={PAD_T + ih * (1 - f)}
                  stroke="var(--border)" strokeWidth="1" />
                <text x={PAD_L - 6} y={PAD_T + ih * (1 - f) + 3.5} textAnchor="end" fontSize="10"
                  fill="var(--text-faint)">{fmtY(max * f)}</text>
              </g>
            ))}
            <line x1={PAD_L} x2={W - PAD_R} y1={PAD_T + ih} y2={PAD_T + ih} stroke="var(--border-bright)" strokeWidth="1" />
            <text x={PAD_L - 6} y={PAD_T + 4} textAnchor="end" fontSize="10" fill="var(--text-faint)">{fmtY(max)}</text>

            {/* marks */}
            {kind === 'bar'
              ? data.map((d, i) => {
                  const h = Math.max(d.y > 0 ? 2 : 0, ((Math.min(d.y, max)) / max) * ih);
                  const bw = Math.max(2, Math.min(18, bandW - 2));
                  const x = bandX(i) + (bandW - bw) / 2;
                  const y = PAD_T + ih - h;
                  const r = Math.min(4, bw / 2, h);
                  return (
                    <path
                      key={i}
                      d={`M${x},${PAD_T + ih} V${y + r} Q${x},${y} ${x + r},${y} H${x + bw - r} Q${x + bw},${y} ${x + bw},${y + r} V${PAD_T + ih} Z`}
                      fill={color}
                      opacity={hover === null || hover === i ? 0.92 : 0.45}
                    />
                  );
                })
              : (
                <>
                  <path d={linePath} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
                  {hover != null && (
                    <circle cx={xOf(hover)} cy={yOf(data[hover].y)} r="4" fill={color} stroke="var(--bg-card)" strokeWidth="2" />
                  )}
                </>
              )}

            {/* selective direct labels: peak (and only when it isn't hovered) */}
            {data[peakIdx] && data[peakIdx].y > 0 && hover !== peakIdx && (
              <text
                x={kind === 'bar' ? bandX(peakIdx) + bandW / 2 : xOf(peakIdx)}
                y={Math.max(10, yOf(data[peakIdx].y) - 6)}
                textAnchor="middle" fontSize="10" fill="var(--text-dim)"
              >
                {tip(data[peakIdx].y)}
              </text>
            )}

            {/* x labels */}
            {xLabelIdx.map((i) => (
              <text key={i} x={kind === 'bar' ? bandX(i) + bandW / 2 : xOf(i)} y={H - 8}
                textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'} fontSize="10" fill="var(--text-faint)">
                {shortDate(data[i].x)}
              </text>
            ))}

            {/* hover crosshair + tooltip */}
            {hover != null && data[hover] && (() => {
              const hx = kind === 'bar' ? bandX(hover) + bandW / 2 : xOf(hover);
              const label = `${shortDate(data[hover].x)} · ${tip(data[hover].y)}`;
              const tw = label.length * 6.2 + 14;
              const tx = Math.min(Math.max(hx - tw / 2, PAD_L), W - PAD_R - tw);
              return (
                <g pointerEvents="none">
                  <line x1={hx} x2={hx} y1={PAD_T} y2={PAD_T + ih} stroke="var(--border-bright)" strokeWidth="1" strokeDasharray="3 3" />
                  <rect x={tx} y={PAD_T - 12} width={tw} height={20} rx="6" fill="var(--bg-elev)" stroke="var(--border-bright)" />
                  <text x={tx + tw / 2} y={PAD_T + 2} textAnchor="middle" fontSize="10.5" fill="var(--text)">{label}</text>
                </g>
              );
            })()}
          </svg>

          <details style={{ marginTop: 6 }}>
            <summary className="muted" style={{ fontSize: 11.5, cursor: 'pointer' }}>{tableLabel}</summary>
            <div className="cl-scroll" style={{ maxHeight: 180, overflowY: 'auto', marginTop: 6 }}>
              <table className="cl-table" style={{ minWidth: 240 }}>
                <thead><tr><th>Date</th><th style={{ textAlign: 'right' }}>Value</th></tr></thead>
                <tbody>
                  {data.map((d) => (
                    <tr key={d.x}>
                      <td className="mono" style={{ fontSize: 12 }}>{d.x}</td>
                      <td className="mono" style={{ fontSize: 12, textAlign: 'right' }}>{tip(d.y)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </div>
  );
}
