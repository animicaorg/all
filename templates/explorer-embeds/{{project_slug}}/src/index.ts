/**
 * Explorer Embeds — zero-deps, drop-in widgets for Animica nodes.
 *
 * Usage (ESM):
 *   import { initExplorerEmbeds } from './dist/index.js';
 *   const embeds = initExplorerEmbeds({ rpcUrl: 'https://devnet.animica.org', chainId: 1 });
 *   const head = embeds.mountHeadTicker('#head');
 *   const bal  = embeds.mountAddressBalance('#bal', 'anim1qq...xyz');
 *   const tx   = embeds.mountTxStatus('#tx', '0xabc123...');
 *
 * Usage (UMD-like, via <script> bundle):
 *   const embeds = window.AnimicaExplorerEmbeds.init({ rpcUrl: 'https://...', chainId: 1 });
 *   const head = embeds.mountHeadTicker(document.getElementById('head'));
 */

type Hex = `0x${string}`;

type InitOptions = {
  /** Base URL of the node RPC, e.g. https://host:port or http://localhost:8545 */
  rpcUrl: string;
  /** Chain id the UI expects (purely informational here; use to prevent user mistakes) */
  chainId?: number;
  /** Poll interval for head/balance updates if WS is unavailable (ms) */
  pollMs?: number;
  /** Optional path to JSON-RPC endpoint (default: '/rpc') */
  rpcPath?: string;
};

type WidgetHandle = { destroy: () => void };

type Head = {
  number: number;
  height?: number;
  hash: Hex;
  parentHash?: Hex;
  timestamp?: number;
};

type Receipt = {
  status: 'SUCCESS' | 'REVERT' | 'OOG' | string;
  gasUsed?: number;
  blockHash?: Hex;
  blockNumber?: number;
  transactionHash?: Hex;
  logs?: Array<any>;
};

type BalanceResult = string | number | bigint;

/* ────────────────────────────────────────────────────────────────────────── */
/* Internal: tiny JSON-RPC client                                            */
/* ────────────────────────────────────────────────────────────────────────── */

function joinUrl(base: string, path: string): string {
  const b = base.replace(/\/+$/g, '');
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${b}${p}`;
}

let rpcCounter = 1;
async function rpcCall<T = any>(baseUrl: string, method: string, params?: any, rpcPath = '/rpc'): Promise<T> {
  const url = joinUrl(baseUrl, rpcPath);
  const body = { jsonrpc: '2.0', id: rpcCounter++, method, params };
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`RPC HTTP ${res.status} ${res.statusText}`);
  const json = await res.json();
  if (json.error) throw new Error(`RPC ${method} error: ${json.error.message ?? 'unknown'}`);
  return json.result as T;
}

/* ────────────────────────────────────────────────────────────────────────── */
/* Helpers: DOM, formatting, styles                                          */
/* ────────────────────────────────────────────────────────────────────────── */

function $(target: string | Element): HTMLElement {
  if (typeof target === 'string') {
    const el = document.querySelector(target);
    if (!el) throw new Error(`Selector not found: ${target}`);
    return el as HTMLElement;
  }
  return target as HTMLElement;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function shortenHex(h: string, left = 6, right = 6) {
  if (!/^0x[0-9a-fA-F]+$/.test(h)) return h;
  if (h.length <= left + right + 2) return h;
  return `${h.slice(0, left + 2)}…${h.slice(-right)}`;
}

function formatAmount(x: BalanceResult): string {
  try {
    const n = typeof x === 'bigint' ? x : BigInt(x);
    // Render as whole units with thin-space groups (assumes 18 decimals; tweak per-chain if needed)
    const s = n.toString();
    const groups = s.replace(/\B(?=(\d{3})+(?!\d))/g, '\u2009');
    return groups;
  } catch {
    return String(x);
  }
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
/* Widgets                                                                   */
/* ────────────────────────────────────────────────────────────────────────── */

export function initExplorerEmbeds(opts: InitOptions) {
  const rpcUrl = opts.rpcUrl.replace(/\/+$/g, '');
  const pollMs = Math.max(1000, opts.pollMs ?? 3000);
  const rpcPath = opts.rpcPath ?? '/rpc';

  injectStyles();

  /** Head ticker: shows latest height and hash; polls JSON-RPC. */
  function mountHeadTicker(target: string | Element): WidgetHandle {
    const host = $(target);
    host.classList.add('animica-embed', 'card');

    const row1 = el('div', 'animica-row');
    const dot = el('span', 'dot');
    const title = el('div', '', 'Latest block');
    title.style.fontWeight = '600';
    row1.append(dot, title);

    const row2 = el('div', 'animica-row');
    const heightEl = el('span', 'badge mono', '#0');
    const hashEl = el('span', 'mono muted', '—');
    row2.append(heightEl, hashEl);

    host.replaceChildren(row1, row2);

    let timer: number | undefined;

    async function tick() {
      try {
        const head = await rpcCall<Head>(rpcUrl, 'chain.getHead', undefined, rpcPath);
        const height = head.height ?? head.number;
        heightEl.textContent = `#${height}`;
        hashEl.textContent = shortenHex(head.hash);
      } catch (err) {
        hashEl.textContent = `error: ${(err as Error)?.message ?? err}`;
      }
    }

    // Prime immediately, then poll
    tick();
    // @ts-ignore - in browsers setInterval returns number
    timer = window.setInterval(tick, pollMs);

    return {
      destroy() {
        if (timer) window.clearInterval(timer);
        host.replaceChildren(); // optional cleanup
      },
    };
  }

  /** Transaction status: resolves receipt and updates when included. */
  function mountTxStatus(target: string | Element, txHash: Hex): WidgetHandle {
    const host = $(target);
    host.classList.add('animica-embed', 'card');

    const row1 = el('div', 'animica-row');
    const dot = el('span', 'dot');
    const title = el('div', '', 'Transaction');
    title.style.fontWeight = '600';
    const txEl = el('span', 'mono muted', shortenHex(txHash));
    row1.append(dot, title, txEl);

    const row2 = el('div', 'animica-row');
    const statusLbl = el('span', 'badge', 'PENDING');
    const details = el('span', 'mono muted', '');
    row2.append(statusLbl, details);

    host.replaceChildren(row1, row2);

    let timer: number | undefined;
    let done = false;

    async function checkOnce() {
      try {
        // First try direct receipt (included), else try pending lookup
        const rec = await rpcCall<Receipt | null>(rpcUrl, 'tx.getTransactionReceipt', [txHash], rpcPath);
        if (rec) {
          done = true;
          statusLbl.textContent = rec.status ?? 'UNKNOWN';
          statusLbl.style.borderColor = rec.status === 'SUCCESS' ? '#10b981' : '#ef4444';
          details.textContent = rec.blockNumber != null ? `#${rec.blockNumber}` : rec.blockHash ? shortenHex(rec.blockHash) : '';
          if (timer) window.clearInterval(timer);
          return;
        }
        // Not included yet; show pending
        statusLbl.textContent = 'PENDING';
        statusLbl.style.borderColor = '#e5e7eb';
        details.textContent = 'waiting for inclusion…';
      } catch (err) {
        details.textContent = `error: ${(err as Error)?.message ?? err}`;
      }
    }

    checkOnce();
    // @ts-ignore
    timer = window.setInterval(() => {
      if (!done) checkOnce();
    }, Math.max(1500, Math.floor(pollMs / 2)));

    return {
      destroy() {
        if (timer) window.clearInterval(timer);
        host.replaceChildren();
      },
    };
  }

  /** Address balance: polls on each new head. */
  function mountAddressBalance(target: string | Element, address: string): WidgetHandle {
    const host = $(target);
    host.classList.add('animica-embed', 'card');

    const row1 = el('div', 'animica-row');
    const dot = el('span', 'dot');
    const title = el('div', '', 'Balance');
    title.style.fontWeight = '600';
    const addrEl = el('span', 'mono muted', shortenHex(address));
    row1.append(dot, title, addrEl);

    const row2 = el('div', 'animica-row');
    const balanceEl = el('span', 'badge mono', '—');
    const noteEl = el('span', 'muted', 'updates each block');
    row2.append(balanceEl, noteEl);

    host.replaceChildren(row1, row2);

    let timer: number | undefined;

    async function refresh() {
      try {
        // state.getBalance(address) -> returns integer (smallest unit)
        const bal = await rpcCall<BalanceResult>(rpcUrl, 'state.getBalance', [address], rpcPath);
        balanceEl.textContent = formatAmount(bal);
      } catch (err) {
        balanceEl.textContent = `error`;
        noteEl.textContent = (err as Error)?.message ?? String(err);
      }
    }

    async function tick() {
      try {
        await rpcCall<Head>(rpcUrl, 'chain.getHead', undefined, rpcPath);
        await refresh();
      } catch {
        // ignore head error; next tick will retry
      }
    }

    refresh();
    // @ts-ignore
    timer = window.setInterval(tick, pollMs);

    return {
      destroy() {
        if (timer) window.clearInterval(timer);
        host.replaceChildren();
      },
    };
  }

  return {
    mountHeadTicker,
    mountTxStatus,
    mountAddressBalance,
  };
}

/* ────────────────────────────────────────────────────────────────────────── */
/* Attach a convenient global for simple <script> usage                       */
/* ────────────────────────────────────────────────────────────────────────── */

declare global {
  interface Window {
    AnimicaExplorerEmbeds?: {
      init: (opts: InitOptions) => ReturnType<typeof initExplorerEmbeds>;
    };
  }
}
if (typeof window !== 'undefined') {
  // Avoid clobber if bundled multiple times
  window.AnimicaExplorerEmbeds = window.AnimicaExplorerEmbeds ?? { init: initExplorerEmbeds };
}

export type { InitOptions, WidgetHandle, Head, Receipt };
