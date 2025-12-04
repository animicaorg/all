/**
 * MV3 Service Worker entry
 * - Boots the background runtime
 * - Wires the message router used by content/provider/UI
 * - Schedules/handles extension alarms (keepalive + GC)
 *
 * This file keeps runtime work minimal; static imports are used for SW compatibility
 * and runtime initialization is deferred where possible.
 */

/// <reference lib="webworker" />

/* eslint-disable no-console */

import * as migrations from './migrations';
import keyring from './keyring';
import { loadVaultEnvelope } from './keyring/storage';
import { addRoutes, createRouter } from './router';

// Some bundlers inject small helpers (e.g. modulepreload) that expect a `window`
// global. The MV3 background runs in a worker context where `window` is absent,
// so alias it to `self` to avoid ReferenceError during early bootstrap.
if (typeof (globalThis as any).window === 'undefined') {
  (globalThis as any).window = self as any;
}

// Alarm names (centralized here)
const ALARMS = {
  KEEPALIVE: 'animica:keepalive',
  GC: 'animica:gc',
} as const;

type AlarmName = typeof ALARMS[keyof typeof ALARMS];

// Router shape (kept minimal to decouple from implementation file)
interface Router {
  handleMessage: (
    msg: unknown,
    sender: chrome.runtime.MessageSender
  ) => Promise<unknown>;
  handlePort?: (port: chrome.runtime.Port) => void;
  onAlarm?: (name: AlarmName) => Promise<void> | void;
  onStartup?: () => Promise<void> | void;
}

// Lazy singletons (static imports because dynamic import() is disallowed in SWs)
let _routerPromise: Promise<Router> | null = null;
function getRouter(): Promise<Router> {
  if (!_routerPromise) {
    _routerPromise = Promise.resolve(createRouter());
  }
  return _routerPromise;
}

async function maybeHandleLegacyMessage(
  msg: any,
  sendResponse: (resp: unknown) => void,
): Promise<boolean> {
  if (!msg || typeof msg !== 'object') return false;

  if (msg.kind === 'accounts:list') {
    const snapshot = await listAccounts();
    sendResponse({ ok: true, ...snapshot });
    return true;
  }

  if (msg.kind === 'accounts:select') {
    const result = await selectAccount(msg.address);
    sendResponse(result);
    return true;
  }

  if (msg.type === 'vault.export') {
    try {
      const payload = await exportVault();
      sendResponse({ ok: true, ...payload });
    } catch (err: any) {
      sendResponse({ ok: false, error: err?.message ?? 'Failed to export vault' });
    }
    return true;
  }

  return false;
}

/* ---------------------------- Route registration ---------------------------- */

async function listAccounts() {
  await keyring.init();
  const accounts = await keyring.listAccounts();
  const selected = await keyring.getSelected();
  const locked = await keyring.isLocked();
  return {
    accounts: accounts.map((a) => ({
      address: a.address,
      name: a.label,
      algo: (a as any).algo ?? (a as any).alg,
      path: a.path,
    })),
    selected: selected?.address,
    locked,
  };
}

async function selectAccount(address: string | undefined) {
  await keyring.init();
  const accounts = await keyring.listAccounts();
  const acct = address ? accounts.find((a) => a.address === address) : undefined;
  if (!acct) {
    return { ok: false, error: 'Account not found' } as const;
  }
  await keyring.selectAccount(acct.id);
  return { ok: true } as const;
}

async function exportVault() {
  const envelope = await loadVaultEnvelope();
  if (!envelope) {
    throw new Error('No vault data to export yet.');
  }
  const json = JSON.stringify(envelope, null, 2);
  const dataUrl = `data:application/json,${encodeURIComponent(json)}`;
  return { dataUrl, fileName: 'animica-vault.json' } as const;
}

addRoutes({
  'accounts.list': async () => listAccounts(),
  'accounts.select': async (payload: { address?: string }) => selectAccount(payload?.address),
  'vault.export': async () => exportVault(),
});

// Schedule default alarms (idempotent: re-creates with same name)
function scheduleDefaultAlarms() {
  try {
    // MV3 minimum period for periodic alarms is 1 minute.
    chrome.alarms.create(ALARMS.KEEPALIVE, { periodInMinutes: 1 });
    // Occasional GC to clean sessions/old notifications/etc.
    chrome.alarms.create(ALARMS.GC, { periodInMinutes: 10 });
  } catch (err) {
    // In some environments (e.g. Firefox MV3 polyfills) alarms may differ.
    console.warn('[bg] Failed to create alarms:', err);
  }
}

// Keep the SW warm during development by pinging periodically.
// (In production it’s fine for the SW to sleep.)
function devKeepWarmTick() {
  if (import.meta && (import.meta as any).env && (import.meta as any).env.DEV) {
    // no-op: alarm is the tick; we just touch a lightweight API.
    void chrome.runtime.getPlatformInfo(() => void 0);
  }
}

// Install / update bootstrap
chrome.runtime.onInstalled.addListener(async (details) => {
  console.log(`[bg] onInstalled: ${details.reason}`);

  // Run storage migrations on install/update without blocking boot.
  // (Loaded lazily to avoid waking the worker on every event.)
  try {
    await migrations.runMigrations?.();
  } catch (err) {
    console.error('[bg] migrations failed:', err);
  }

  scheduleDefaultAlarms();

  // Notify router (so it can initialize stores, caches, etc.)
  try {
    const r = await getRouter();
    await r.onStartup?.();
  } catch (err) {
    console.error('[bg] router startup hook failed:', err);
  }
});

// Browser restart or extension startup
chrome.runtime.onStartup?.addListener(async () => {
  console.log('[bg] onStartup');
  scheduleDefaultAlarms();
  try {
    const r = await getRouter();
    await r.onStartup?.();
  } catch (err) {
    console.error('[bg] router onStartup failed:', err);
  }
});

// Message routing (provider/content/UI → background)
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      const handled = await maybeHandleLegacyMessage(msg, sendResponse);
      if (handled) return;

      const r = await getRouter();
      const res = await r.handleMessage(msg, sender);
      sendResponse({ ok: true, result: res });
    } catch (err: any) {
      console.error('[bg] onMessage error:', err);
      sendResponse({
        ok: false,
        error: String(err?.message ?? err ?? 'UnknownError'),
      });
    }
  })();
  // Keep the channel open for the async response
  return true;
});

// Long-lived port connections (e.g. content-script bridge)
chrome.runtime.onConnect.addListener(async (port) => {
  try {
    const r = await getRouter();
    if (r.handlePort) r.handlePort(port);
  } catch (err) {
    console.error('[bg] onConnect error:', err);
    try {
      port.disconnect();
    } catch {
      /* ignore */
    }
  }
});

// Alarms
chrome.alarms.onAlarm.addListener(async (alarm) => {
  const name = alarm.name as AlarmName;
  if (name === ALARMS.KEEPALIVE) {
    devKeepWarmTick();
  }

  // Delegate to router so features can hook alarm ticks
  try {
    const r = await getRouter();
    await r.onAlarm?.(name);
  } catch (err) {
    console.error('[bg] onAlarm handler failed:', err);
  }
});

// Unhandled errors (best-effort logging)
self.addEventListener('unhandledrejection', (e) => {
  console.error('[bg] Unhandled promise rejection:', e.reason);
});
self.addEventListener('error', (e) => {
  console.error('[bg] Unhandled error:', e.message, e.error);
});

// Initial scheduling in case onInstalled/onStartup didn’t fire (some polyfills)
scheduleDefaultAlarms();

// Dev hot-reload hint (Vite): accept reloads gracefully
declare const __VITE_HMR__: any;
if ((import.meta as any).hot) {
  (import.meta as any).hot.accept(() => {
    console.log('[bg] HMR: background reloaded');
  });
}

// Explicit export to aid unit tests (optional)
export {};
