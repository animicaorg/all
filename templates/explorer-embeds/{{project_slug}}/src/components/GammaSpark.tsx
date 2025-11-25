import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

type Hex = `0x${string}`;

export interface GammaSparkProps {
  /** Base RPC URL, e.g. https://node.example.com */
  rpcUrl: string;
  /** JSON-RPC path (default: /rpc) */
  rpcPath?: string;
  /**
   * RPC method that returns the current Γ (fairness/mix) value in [0,1].
   * You can override if your node exposes a different name.
   * Default: "consensus.getGamma"
   */
  method?: string;
  /** Poll interval in ms (default: 3000) */
  pollMs?: number;
  /** Max number of points in the rolling window (default: 60) */
  capacity?: number;
  /** Canvas width in CSS pixels (default: 240) */
  width?: number;
  /** Canvas height in CSS pixels (default: 60) */
  height?: number;
  /** Optional title text shown above the chart */
  title?: string;
  /** Show guide bands at 0.25 / 0.5 / 0.75 (default: true) */
  bands?: boolean;
  /** Extra class names for the root container */
  className?: string;
  /** Called whenever a new value is read */
  onValue?: (value: number) => void;
  /**
   * Custom renderer if you want full control. Receive the latest value and points.
   * If provided, the default UI won't render.
   */
  render?: (state: {
    value?: number;
    points: Array<{ t: number; v: number }>;
    error?: string;
    loading: boolean;
  }) => React.ReactNode;
}

/* ────────────────────────────────────────────────────────────────────────── */
/* Utilities                                                                  */
/* ────────────────────────────────────────────────────────────────────────── */

function joinUrl(base: string, path: string): string {
  const b = base.replace(/\/+$/g, '');
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${b}${p}`;
}

let rpcCounter = 1;
async function rpcCall<T = any>(
  baseUrl: string,
  method: string,
  params?: any,
  rpcPath = '/rpc',
): Promise<T> {
  const url = joinUrl(baseUrl, rpcPath);
  const body = { jsonrpc: '2.0', id: rpcCounter++, method, params };
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`RPC HTTP ${res.status} ${res.statusText}`);
  const json = await res.json();
  if (json.error) throw new Error(json.error.message ?? 'RPC error');
  return json.result as T;
}

function clamp01(x: number) {
  return Math.max(0, Math.min(1, x));
}

let stylesInjected = false;
function injectStyles() {
  if (stylesInjected) return;
  stylesInjected = true;
  const css = `
  .animica-embed{font:13px/1.4 system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,Helvetica,Arial,sans-serif;color:#0f172a}
  .animica-embed.card{border:1px solid #e5e7eb;border-radius:8px;padding:.75rem;background:#fff;box-shadow:0 1px 1px rgba(0,0,0,.04)}
  .animica-row{display:flex;align-items:center;gap:.5rem}
  .animica-row + .animica-row{margin-top:.5rem}
  .spark-wrap{display:flex;flex-direction:column;gap:.4rem}
  .muted{color:#64748b}
  .mono{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,Liberation Mono,monospace}
  .badge{display:inline-block;padding:.1rem .4rem;border-radius:999px;border:1px solid #e5e7eb}
  .dot{width:.5rem;height:.5rem;border-radius:999px;background:#10b981;display:inline-block}
  .legend{display:flex;align-items:center;gap:.5rem;justify-content:space-between}
  .legend .title{font-weight:600}
  .legend .value{font-weight:600}
  .legend small{font-size:11px}
  `;
  const style = document.createElement('style');
  style.setAttribute('data-animica-embeds', '1');
  style.textContent = css.trim();
  document.head.appendChild(style);
}

/* ────────────────────────────────────────────────────────────────────────── */
/* Component                                                                  */
/* ────────────────────────────────────────────────────────────────────────── */

export const GammaSpark: React.FC<GammaSparkProps> = ({
  rpcUrl,
  rpcPath = '/rpc',
  method = 'consensus.getGamma',
  pollMs = 3000,
  capacity = 60,
  width = 240,
  height = 60,
  title = 'Γ fairness',
  bands = true,
  className,
  onValue,
  render,
}) => {
  type Pt = { t: number; v: number };
  const [points, setPoints] = useState<Pt[]>([]);
  const [value, setValue] = useState<number | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState<boolean>(true);
  const mounted = useRef(true);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Inject drop-in CSS
  useEffect(() => {
    injectStyles();
  }, []);

  const intervalMs = useMemo(() => Math.max(500, pollMs | 0), [pollMs]);

  // Fetch loop
  useEffect(() => {
    mounted.current = true;

    async function tick() {
      try {
        const v = await rpcCall<number>(rpcUrl, method, undefined, rpcPath);
        if (!mounted.current) return;
        const vv = clamp01(Number(v));
        setValue(vv);
        setPoints((prev) => {
          const now = Date.now();
          const next = [...prev, { t: now, v: vv }];
          while (next.length > capacity) next.shift();
          return next;
        });
        setError(undefined);
        setLoading(false);
        onValue?.(vv);
      } catch (e) {
        if (!mounted.current) return;
        setError((e as Error)?.message ?? String(e));
        setLoading(false);
      }
    }

    tick(); // prime immediately
    const id = window.setInterval(tick, intervalMs);
    return () => {
      mounted.current = false;
      window.clearInterval(id);
    };
  }, [rpcUrl, rpcPath, method, capacity, intervalMs, onValue]);

  // Drawing routine
  const draw = useCallback(
    (ctx: CanvasRenderingContext2D, w: number, h: number) => {
      // Clear
      ctx.clearRect(0, 0, w, h);

      // Background bands
      if (bands) {
        ctx.save();
        ctx.strokeStyle = '#e5e7eb';
        ctx.lineWidth = 1;
        [0.25, 0.5, 0.75].forEach((p) => {
          const y = (1 - p) * h;
          ctx.beginPath();
          ctx.moveTo(0, Math.round(y) + 0.5);
          ctx.lineTo(w, Math.round(y) + 0.5);
          ctx.stroke();
        });
        ctx.restore();
      }

      if (points.length < 2) return;

      // Use fixed y-range [0,1] for comparability across time
      const N = points.length;
      const step = w / Math.max(1, capacity - 1);
      const path = new Path2D();
      const area = new Path2D();

      // Build line path
      points.forEach((p, i) => {
        const x = Math.min(w, i * step);
        const y = (1 - clamp01(p.v)) * h;
        if (i === 0) {
          path.moveTo(x, y);
          area.moveTo(x, h);
          area.lineTo(x, y);
        } else {
          path.lineTo(x, y);
          area.lineTo(x, y);
        }
      });
      area.lineTo(Math.min(w, (N - 1) * step), h);
      area.closePath();

      // Choose a color that shifts with the latest value
      const cur = clamp01(points[points.length - 1]?.v ?? 0.5);
      // Hue from red(0) → green(120)
      const hue = 120 * cur;
      const stroke = `hsl(${hue} 70% 35%)`;
      const fill = `hsl(${hue} 85% 55% / 0.12)`;

      // Fill area
      ctx.fillStyle = fill;
      ctx.fill(area);

      // Draw line
      ctx.lineWidth = 2;
      ctx.strokeStyle = stroke;
      ctx.stroke(path);

      // Draw last point marker
      const xEnd = Math.min(w, (N - 1) * step);
      const yEnd = (1 - cur) * h;
      ctx.fillStyle = stroke;
      ctx.beginPath();
      ctx.arc(xEnd, yEnd, 2.5, 0, Math.PI * 2);
      ctx.fill();
    },
    [points, capacity, bands],
  );

  // Canvas resize & render
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const dpr = Math.max(1, window.devicePixelRatio || 1);
    const cssW = Math.max(40, width | 0);
    const cssH = Math.max(24, height | 0);
    const pxW = Math.floor(cssW * dpr);
    const pxH = Math.floor(cssH * dpr);

    // Set size if changed
    if (canvas.width !== pxW || canvas.height !== pxH) {
      canvas.width = pxW;
      canvas.height = pxH;
      canvas.style.width = `${cssW}px`;
      canvas.style.height = `${cssH}px`;
    }

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    ctx.save();
    ctx.scale(dpr, dpr);
    draw(ctx, cssW, cssH);
    ctx.restore();
  }, [points, width, height, draw]);

  if (render) {
    return <>{render({ value, points, error, loading })}</>;
  }

  const pct = value != null ? `${(value * 100).toFixed(1)}%` : '—';

  return (
    <div
      className={['animica-embed', 'card', className].filter(Boolean).join(' ')}
      aria-live="polite"
      aria-busy={loading || undefined}
    >
      <div className="spark-wrap">
        <div className="legend">
          <div className="animica-row">
            <span className="dot" aria-hidden="true" />
            <div className="title">{title}</div>
          </div>
          <div className="animica-row">
            <span className="badge mono" title="latest Γ">{pct}</span>
          </div>
        </div>
        <canvas
          ref={canvasRef}
          role="img"
          aria-label={value != null ? `Gamma fairness ${pct}` : 'Gamma fairness loading'}
        />
        {error && (
          <div className="animica-row">
            <span className="badge" style={{ borderColor: '#ef4444' }}>error</span>
            <span className="mono muted" title={error}>
              {error.length > 96 ? `${error.slice(0, 93)}…` : error}
            </span>
          </div>
        )}
        <div className="animica-row muted" style={{ justifyContent: 'space-between' }}>
          <small>0</small>
          <small>0.25</small>
          <small>0.5</small>
          <small>0.75</small>
          <small>1.0</small>
        </div>
      </div>
    </div>
  );
};

export default GammaSpark;
