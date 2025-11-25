/**
 * Notifications helpers (MV3).
 *
 * Responsibilities:
 * - Show basic notifications for new heads (blocks) and tx lifecycle
 *   events (submitted/confirmed/failed).
 * - Optionally set the extension action badge for quick status hints.
 * - Provide a central click handler that opens a relevant URL (e.g. tx/blk
 *   on an explorer) when available.
 *
 * Notes:
 * - Requires "notifications" permission in manifest.
 * - Works in Chrome/Chromium and Firefox MV3.
 * - Keep state minimal and resilient: IDs are deterministic; click targets
 *   are stored in chrome.storage.session so they survive SW restarts for
 *   the session but do not persist across browser restarts.
 */

 /* eslint-disable no-console */

import { readState } from './migrations';

// ---- Types ----

export interface HeadInfo {
  height: number;
  hash: string;
  timestamp?: number;
}

export type TxStatus = 'submitted' | 'confirmed' | 'failed' | 'reverted';

export interface TxInfo {
  hash: string;
  to?: string;
  value?: string | number; // human-formatted string or number of base units
  status?: TxStatus;
}

// ---- Constants ----

const ICON_128 = 'public/icons/icon128.png'; // resolved relative to extension root
const NOTIF_PREFIX_HEAD = 'head-';
const NOTIF_PREFIX_TX = 'tx-';
const CLICK_MAP_KEY = 'animica_click_targets_v1'; // in chrome.storage.session

// ---- Small utils ----

function shortHash(h: string): string {
  if (!h || typeof h !== 'string') return '';
  return h.length <= 12 ? h : `${h.slice(0, 6)}…${h.slice(-4)}`;
}

function nowISO(): string {
  try {
    return new Date().toLocaleString();
  } catch {
    return '';
  }
}

function safeNumber(x: unknown): number | undefined {
  return typeof x === 'number' && Number.isFinite(x) ? x : undefined;
}

// Explorer URL helpers (best-effort; optional)
function getExplorerBase(): string | undefined {
  const fromEnv = (import.meta as any)?.env?.VITE_EXPLORER_URL as string | undefined;
  return fromEnv && typeof fromEnv === 'string' ? fromEnv.replace(/\/+$/, '') : undefined;
}
function txUrl(hash: string): string | undefined {
  const base = getExplorerBase();
  return base ? `${base}/tx/${encodeURIComponent(hash)}` : undefined;
}
function blockUrl(height: number): string | undefined {
  const base = getExplorerBase();
  return base ? `${base}/block/${encodeURIComponent(height)}` : undefined;
}

// ---- Click target mapping (session-scoped) ----

async function setClickTarget(id: string, url?: string) {
  if (!url) return;
  try {
    // @ts-ignore - session storage exists in MV3
    const cur = await chrome.storage.session.get(CLICK_MAP_KEY);
    const map = (cur?.[CLICK_MAP_KEY] ?? {}) as Record<string, string>;
    map[id] = url;
    // @ts-ignore
    await chrome.storage.session.set({ [CLICK_MAP_KEY]: map });
  } catch (e) {
    console.warn('[notifications] setClickTarget failed', e);
  }
}

async function consumeClickTarget(id: string): Promise<string | undefined> {
  try {
    // @ts-ignore
    const cur = await chrome.storage.session.get(CLICK_MAP_KEY);
    const map = (cur?.[CLICK_MAP_KEY] ?? {}) as Record<string, string>;
    const url = map[id];
    if (url) {
      delete map[id];
      // @ts-ignore
      await chrome.storage.session.set({ [CLICK_MAP_KEY]: map });
    }
    return url;
  } catch (e) {
    console.warn('[notifications] consumeClickTarget failed', e);
    return undefined;
  }
}

// ---- Badge helpers ----

export async function setBadge(text: string | undefined, color?: string) {
  try {
    await chrome.action.setBadgeText({ text: text ?? '' });
    if (color) {
      await chrome.action.setBadgeBackgroundColor({ color });
    }
  } catch (e) {
    // Some browsers/extensions may not support setBadgeBackgroundColor
    console.warn('[notifications] setBadge failed', e);
  }
}

// ---- Permission sanity (MV3 will allow if declared in manifest) ----

async function ensurePermission(): Promise<boolean> {
  try {
    const granted = await chrome.permissions.contains({ permissions: ['notifications'] });
    return granted;
  } catch {
    // If permissions API is not available, assume manifest permission suffices.
    return true;
  }
}

// ---- Core notify APIs ----

export async function notifyNewHead(head: HeadInfo) {
  if (!(await ensurePermission())) return;

  const height = safeNumber(head.height) ?? 0;
  const id = `${NOTIF_PREFIX_HEAD}${height}`;

  // Load chain name for context
  let chainLabel = 'Animica';
  try {
    const s = await readState();
    const net = s.network?.known?.[s.network?.currentId];
    if (net?.name) chainLabel = net.name;
  } catch {
    // ignore
  }

  const title = `${chainLabel}: New block #${height}`;
  const message = `Hash ${shortHash(head.hash)} • ${nowISO()}`;

  try {
    await chrome.notifications.create(id, {
      type: 'basic',
      iconUrl: ICON_128,
      title,
      message,
      priority: 1,
    });
    await setClickTarget(id, blockUrl(height));
    // Optional: subtle badge tick
    await setBadge('•');
    // Clear tiny dot after a few seconds (best-effort)
    setTimeout(() => setBadge(''), 4000);
  } catch (e) {
    console.warn('[notifications] notifyNewHead failed', e);
  }
}

export async function notifyTxEvent(info: TxInfo) {
  if (!(await ensurePermission())) return;

  const status: TxStatus = info.status ?? 'submitted';
  const id = `${NOTIF_PREFIX_TX}${info.hash}`;
  let title = 'Transaction';
  let message = '';

  switch (status) {
    case 'submitted':
      title = 'Transaction submitted';
      message = `${shortHash(info.hash)} ${info.to ? `→ ${info.to}` : ''}`;
      break;
    case 'confirmed':
      title = 'Transaction confirmed';
      message = `${shortHash(info.hash)} included on-chain`;
      break;
    case 'failed':
    case 'reverted':
      title = 'Transaction failed';
      message = `${shortHash(info.hash)} ${status === 'reverted' ? '(reverted)' : ''}`;
      break;
  }

  try {
    await chrome.notifications.create(id, {
      type: 'basic',
      iconUrl: ICON_128,
      title,
      message,
      priority: status === 'failed' || status === 'reverted' ? 2 : 1,
    });
    await setClickTarget(id, txUrl(info.hash));

    // Badge hint by status
    if (status === 'submitted') {
      await setBadge('…'); // pending
    } else if (status === 'confirmed') {
      await setBadge('✓', '#16a34a');
      setTimeout(() => setBadge(''), 5000);
    } else {
      await setBadge('!', '#dc2626');
      setTimeout(() => setBadge(''), 6000);
    }
  } catch (e) {
    console.warn('[notifications] notifyTxEvent failed', e);
  }
}

// Clear a notification explicitly (optional)
export async function clearNotification(id: string) {
  try {
    await chrome.notifications.clear(id);
  } catch (e) {
    console.warn('[notifications] clearNotification failed', e);
  }
}

// ---- Global click handler ----

// Install once (module evaluated in SW context)
try {
  chrome.notifications.onClicked.addListener(async (id: string) => {
    try {
      const url = await consumeClickTarget(id);
      if (url) {
        await chrome.tabs.create({ url });
      }
      // Always clear the notification after click
      await chrome.notifications.clear(id);
    } catch (e) {
      console.warn('[notifications] onClicked handler failed', e);
    }
  });
} catch (e) {
  // In some non-standard environments this may throw; ignore.
  console.warn('[notifications] onClicked binding failed', e);
}

