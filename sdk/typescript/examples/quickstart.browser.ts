/**
 * Browser Quickstart (TypeScript)
 * --------------------------------
 * A small helper you can import in a page to:
 *  - detect the Animica wallet provider (window.animica)
 *  - connect and read accounts/chainId
 *  - query the current head
 *  - subscribe to newHeads
 *  - send a tiny transfer
 *
 * This file is framework-agnostic and expects the demo HTML to contain
 * elements with the following IDs (see quickstart.browser.html):
 *   btn-detect, btn-connect, btn-accounts, btn-chain, btn-head, btn-subscribe, btn-send
 *   prov-status, acc-status, chain-status, head-out, send-out, log, to, amount
 *
 * You can also use the exported functions programmatically without any DOM.
 */

declare global {
  interface Window {
    animica?: AnimicaProvider;
    ethereum?: AnimicaProvider; // fallback for generic dapp patterns
  }
}

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

export interface AnimicaProvider {
  isAnimica?: boolean;
  version?: string;
  request(args: { method: string; params?: any[] | Record<string, any> }): Promise<any>;
  on?(event: string, handler: (...args: any[]) => void): void;
  removeListener?(event: string, handler: (...args: any[]) => void): void;
}

// ──────────────────────────────────────────────────────────────────────────────
// Utilities
// ──────────────────────────────────────────────────────────────────────────────
const enc = new TextEncoder();

function $(id: string): HTMLElement {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Missing element #${id}`);
  return el;
}

function setBadge(el: HTMLElement, text: string, color?: string) {
  el.textContent = text;
  if (color) (el as HTMLElement).style.borderColor = color;
}

function escapeHtml(s: string) {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!));
}

export function getProvider(): AnimicaProvider | null {
  const p = window.animica || window.ethereum || null;
  return p && (p.isAnimica || 'request' in p) ? p : null;
}

export async function providerRequest<T = any>(
  p: AnimicaProvider,
  method: string,
  params?: any[] | Record<string, any>
): Promise<T> {
  // Try canonical shape first; then retry with positional array if necessary
  try {
    return await p.request({ method, params });
  } catch (e) {
    try {
      const pos = Array.isArray(params) ? params : params == null ? [] : [params];
      return await p.request({ method, params: pos });
    } catch {
      throw e;
    }
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Convenience high-level calls that try multiple method names
// ──────────────────────────────────────────────────────────────────────────────
export async function requestAccounts(p: AnimicaProvider, interactive = false): Promise<string[]> {
  const trials: Array<[string, any[]]> = [];
  if (interactive) {
    trials.push(['wallet_requestPermissions', [{ eth_accounts: {} }]]);
    trials.push(['animica_requestPermissions', [{ accounts: {} }]]);
    trials.push(['eth_requestAccounts', []]);
    trials.push(['animica_requestAccounts', []]);
  } else {
    trials.push(['eth_accounts', []]);
    trials.push(['animica_accounts', []]);
  }

  for (const [m, ps] of trials) {
    try {
      const out = await providerRequest<any>(p, m, ps);
      if (Array.isArray(out) && out.length) return out;
      if (out && typeof out === 'object') {
        if (Array.isArray((out as any).accounts)) return (out as any).accounts as string[];
        if (Array.isArray((out as any).result)) return (out as any).result as string[];
      }
      // Some permissions flows require a follow-up read:
      if (m.endsWith('Permissions')) {
        try {
          const accs = await providerRequest<string[]>(p, 'eth_accounts', []);
          if (accs?.length) return accs;
        } catch {}
      }
    } catch {
      // try next method
    }
  }
  return [];
}

export async function getChainId(p: AnimicaProvider): Promise<number | string | null> {
  const trials: Array<[string, any[]]> = [
    ['chain.getChainId', []],
    ['eth_chainId', []],
    ['chainId', []],
  ];
  for (const [m, ps] of trials) {
    try {
      const out = await providerRequest<any>(p, m, ps);
      return typeof out === 'string' && /^0x[0-9a-f]+$/i.test(out) ? parseInt(out, 16) : out;
    } catch {}
  }
  return null;
}

export async function getHead(p: AnimicaProvider): Promise<Json> {
  const trials: Array<[string, any[]]> = [
    ['chain.getHead', []],
    ['chain_head', []],
  ];
  for (const [m, ps] of trials) {
    try {
      return await providerRequest<Json>(p, m, ps);
    } catch {}
  }
  throw new Error('No head method available on provider.');
}

export function listenHeads(p: AnimicaProvider, cb: (head: Json) => void): () => void {
  const handler = (msg: any) => {
    const head = msg?.result ?? msg?.params?.result ?? msg;
    cb(head);
  };

  // Best-effort subscription command (some providers require an explicit call)
  (async () => {
    const subs: Array<[string, any[]]> = [
      ['subscribe', ['newHeads']],
      ['ws.subscribe', ['newHeads']],
    ];
    for (const [m, ps] of subs) {
      try {
        await providerRequest(p, m, ps);
        break;
      } catch {}
    }
  })();

  // Event hookup
  if (typeof p.on === 'function') {
    try {
      p.on('newHeads', handler);
      // Some providers emit a generic 'message' event
      p.on('message', (m: any) => {
        if (m?.type && /newHeads/i.test(String(m.type))) handler(m);
      });
      return () => {
        try { p.removeListener?.('newHeads', handler); } catch {}
        try { p.removeListener?.('message', handler); } catch {}
      };
    } catch {
      // fall through; return noop
    }
  }
  return () => {};
}

export async function sendTransaction(
  p: AnimicaProvider,
  tx: {
    from: string;
    to?: string;
    value?: number | string;
    gasPrice?: number | string;
    gasLimit?: number | string;
    chainId?: number | string;
    data?: string;
    nonce?: number;
  }
): Promise<string> {
  const trials: Array<[string, any[]]> = [
    ['tx.sendTransaction', [tx]],
    ['animica_sendTransaction', [tx]],
    ['wallet_sendTransaction', [tx]],
    ['eth_sendTransaction', [tx]],
    ['sendTransaction', [tx]],
  ];
  for (const [m, ps] of trials) {
    try {
      const hash = await providerRequest<any>(p, m, ps);
      return String(hash);
    } catch {}
  }
  throw new Error('No sendTransaction method accepted this payload.');
}

// ──────────────────────────────────────────────────────────────────────────────
// Demo UI wiring
// ──────────────────────────────────────────────────────────────────────────────
function logTo(el: HTMLElement, ...args: any[]) {
  const line = args
    .map((x) => {
      try { return typeof x === 'string' ? x : JSON.stringify(x, null, 2); }
      catch { return String(x); }
    })
    .join(' ');
  el.textContent += line + '\n';
  el.scrollTop = el.scrollHeight;
}

export function initQuickstartDOM(): void {
  // If essential elements are missing, do nothing (allow programmatic usage)
  const mustHave = ['prov-status', 'acc-status', 'chain-status', 'head-out', 'log'];
  for (const id of mustHave) {
    if (!document.getElementById(id)) return;
  }

  const logEl = $('log');
  const provBadge = $('prov-status');
  const accBadge = $('acc-status');
  const chainBadge = $('chain-status');
  const headOut = $('head-out');
  const sendOut = document.getElementById('send-out');

  const provider = getProvider();
  setBadge(provBadge, provider ? 'provider: found ✓' : 'provider: not found', provider ? '#19c37d' : '#e8a20c');
  if (provider) {
    logTo(logEl, 'Provider detected:', JSON.stringify({ isAnimica: !!provider.isAnimica, version: provider.version ?? '?' }));
  } else {
    logTo(logEl, 'No provider found. Install the wallet extension and reload.');
  }

  const onDetect = () => {
    const p = getProvider();
    if (p) {
      setBadge(provBadge, 'provider: found ✓', '#19c37d');
      logTo(logEl, 'Provider OK.');
    } else {
      setBadge(provBadge, 'provider: not found', '#ff5460');
      logTo(logEl, 'Provider not found.');
    }
  };

  const onConnect = async () => {
    const p = getProvider();
    if (!p) return alert('No provider found.');
    try {
      const accs = await requestAccounts(p, true);
      setBadge(accBadge, 'accounts: ' + (accs[0] || '—'), '#19c37d');
      logTo(logEl, 'Connected accounts:', accs);

      // Live events
      try { p.on?.('accountsChanged', (a: string[]) => { setBadge(accBadge, 'accounts: ' + (a?.[0] || '—')); logTo(logEl, 'accountsChanged', a); }); } catch {}
      try { p.on?.('chainChanged', (cid: any) => { setBadge(chainBadge, 'chainId: ' + cid); logTo(logEl, 'chainChanged', cid); }); } catch {}
    } catch (e) {
      setBadge(accBadge, 'accounts: (failed)', '#ff5460');
      logTo(logEl, 'Connect failed:', String(e));
    }
  };

  const onAccounts = async () => {
    const p = getProvider();
    if (!p) return alert('No provider found.');
    const accs = await requestAccounts(p, false);
    setBadge(accBadge, 'accounts: ' + (accs[0] || '—'));
    logTo(logEl, 'accounts:', accs);
  };

  const onChain = async () => {
    const p = getProvider();
    if (!p) return alert('No provider found.');
    const cid = await getChainId(p);
    setBadge(chainBadge, 'chainId: ' + (cid ?? '—'));
    logTo(logEl, 'chainId:', cid);
  };

  const onHead = async () => {
    const p = getProvider();
    if (!p) return alert('No provider found.');
    try {
      const head = await getHead(p);
      headOut.innerHTML = '<small class="mono">head: ' + escapeHtml(JSON.stringify(head)) + '</small>';
      logTo(logEl, 'head:', head);
    } catch (e) {
      headOut.innerHTML = '<small class="mono">head: (error)</small>';
      logTo(logEl, 'getHead error:', String(e));
    }
  };

  const onSubscribe = async () => {
    const p = getProvider();
    if (!p) return alert('No provider found.');
    listenHeads(p, (h) => {
      headOut.innerHTML = '<small class="mono">head: ' + escapeHtml(JSON.stringify(h)) + '</small>';
      logTo(logEl, 'newHead:', h);
    });
    logTo(logEl, 'Subscribed to newHeads.');
  };

  const onSend = async () => {
    const p = getProvider();
    if (!p) return alert('No provider found.');
    const accs = await requestAccounts(p, false);
    const from = accs[0];
    if (!from) return alert('Connect and select an account first.');
    const cid = (await getChainId(p)) ?? 1;

    const toEl = document.getElementById('to') as HTMLInputElement | null;
    const amtEl = document.getElementById('amount') as HTMLInputElement | null;
    const to = (toEl?.value || '').trim();
    const value = Number(amtEl?.value ?? 0) || 1;

    if (!to) return alert('Enter a destination address.');
    try {
      const hash = await sendTransaction(p, {
        from, to, value,
        gasPrice: 1,       // demo defaults; your node may enforce floors
        gasLimit: 50_000,
        chainId: cid
      });
      if (sendOut) sendOut.innerHTML = '<small class="mono">tx: ' + escapeHtml(hash) + '</small>';
      logTo(logEl, 'sent tx:', hash);
    } catch (e) {
      if (sendOut) sendOut.innerHTML = '<small class="mono">tx: (error)</small>';
      logTo(logEl, 'send tx error:', String(e));
    }
  };

  // Wire buttons if present
  document.getElementById('btn-detect')?.addEventListener('click', onDetect);
  document.getElementById('btn-connect')?.addEventListener('click', onConnect);
  document.getElementById('btn-accounts')?.addEventListener('click', onAccounts);
  document.getElementById('btn-chain')?.addEventListener('click', onChain);
  document.getElementById('btn-head')?.addEventListener('click', onHead);
  document.getElementById('btn-subscribe')?.addEventListener('click', onSubscribe);
  document.getElementById('btn-send')?.addEventListener('click', onSend);
}

// Auto-init if running in a page that looks like the demo
if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      try { initQuickstartDOM(); } catch { /* ignore if elements are missing */ }
    });
  } else {
    try { initQuickstartDOM(); } catch { /* ignore if elements are missing */ }
  }
}
