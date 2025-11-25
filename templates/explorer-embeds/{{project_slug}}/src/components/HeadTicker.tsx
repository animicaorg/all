import React, { useEffect, useMemo, useRef, useState } from 'react';

type Hex = `0x${string}`;

export type Head = {
  number: number;
  height?: number;
  hash: Hex;
  parentHash?: Hex;
  timestamp?: number;
};

export interface HeadTickerProps {
  /** Base RPC URL, e.g. https://node.example.com */
  rpcUrl: string;
  /** JSON-RPC path (default: /rpc) */
  rpcPath?: string;
  /** Poll interval (ms). Default: 3000 */
  pollMs?: number;
  /** Extra class names for the root */
  className?: string;
  /** Called whenever a new head is fetched */
  onHead?: (head: Head) => void;
  /** Custom renderer. If provided, you fully control the inner UI. */
  render?: (state: {
    head?: Head;
    error?: string;
    loading: boolean;
  }) => React.ReactNode;
}

/* ────────────────────────────────────────────────────────────────────────── */
/* Small utilities                                                            */
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

function shortenHex(h: string, left = 6, right = 6) {
  if (!/^0x[0-9a-fA-F]+$/.test(h)) return h;
  if (h.length <= left + right + 2) return h;
  return `${h.slice(0, left + 2)}…${h.slice(-right)}`;
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
  .dot{width:.5rem;height:.5rem;border-radius:999px;background:#10b981;display:inline-block}
  .muted{color:#64748b}
  .mono{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,Liberation Mono,monospace}
  .badge{display:inline-block;padding:.1rem .4rem;border-radius:999px;border:1px solid #e5e7eb}
  `;
  const style = document.createElement('style');
  style.setAttribute('data-animica-embeds', '1');
  style.textContent = css.trim();
  document.head.appendChild(style);
}

/* ────────────────────────────────────────────────────────────────────────── */
/* Component                                                                  */
/* ────────────────────────────────────────────────────────────────────────── */

export const HeadTicker: React.FC<HeadTickerProps> = ({
  rpcUrl,
  rpcPath = '/rpc',
  pollMs = 3000,
  className,
  onHead,
  render,
}) => {
  const [head, setHead] = useState<Head | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState<boolean>(true);
  const mounted = useRef(true);

  // Inject CSS once for drop-in usage
  useEffect(() => {
    injectStyles();
  }, []);

  const intervalMs = useMemo(() => Math.max(1000, pollMs | 0), [pollMs]);

  useEffect(() => {
    mounted.current = true;

    async function tick() {
      try {
        const h = await rpcCall<Head>(rpcUrl, 'chain.getHead', undefined, rpcPath);
        if (!mounted.current) return;
        setHead(h);
        setError(undefined);
        setLoading(false);
        onHead?.(h);
      } catch (e) {
        if (!mounted.current) return;
        setError((e as Error)?.message ?? String(e));
        setLoading(false);
      }
    }

    // Prime immediately, then poll
    tick();
    const id = window.setInterval(tick, intervalMs);

    return () => {
      mounted.current = false;
      window.clearInterval(id);
    };
  }, [rpcUrl, rpcPath, intervalMs, onHead]);

  if (render) {
    return <>{render({ head, error, loading })}</>;
  }

  const height = head?.height ?? head?.number;
  const hash = head?.hash ? shortenHex(head.hash) : '—';

  return (
    <div
      className={['animica-embed', 'card', className].filter(Boolean).join(' ')}
      aria-live="polite"
      aria-busy={loading || undefined}
    >
      <div className="animica-row">
        <span className="dot" aria-hidden="true" />
        <div style={{ fontWeight: 600 }}>Latest block</div>
      </div>

      <div className="animica-row">
        <span className="badge mono">{height != null ? `#${height}` : '—'}</span>
        <span className="mono muted">{hash}</span>
      </div>

      {error && (
        <div className="animica-row">
          <span className="badge" style={{ borderColor: '#ef4444' }}>error</span>
          <span className="mono muted" title={error}>
            {error.length > 80 ? `${error.slice(0, 77)}…` : error}
          </span>
        </div>
      )}
    </div>
  );
};

export default HeadTicker;
