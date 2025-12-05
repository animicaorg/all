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
import { loadVaultEnvelope, clearSession as clearKeyringSession } from './keyring/storage';
import { generateMnemonic } from './keyring/mnemonic';
import { createRouter } from './router';
import type { BgResponse } from './runtime';
import { getRpcClient, listNetworks, selectNetworkByChainId, rpcSanityCheck } from './network/state';
import type { Network } from './network/networks';
import { RpcClient } from './network/rpc';

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
  ) => Promise<BgResponse>;
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

function broadcast(msg: unknown) {
  try {
    chrome.runtime.sendMessage(msg);
  } catch (err) {
    console.warn('[bg] broadcast failed', err);
  }
}

async function notifyAccountsChanged() {
  try {
    const accounts = await keyring.listAccounts();
    const selected = await keyring.getSelected();
    const payload = accounts.map((a) => ({
      address: a.address,
      name: a.label,
      algo: (a as any).algo ?? (a as any).alg,
      path: a.path,
    }));

    broadcast({ type: 'accounts/updated', accounts: payload, selected: selected?.address, locked: await keyring.isLocked() });
    broadcast({ channel: 'animica', type: 'EVENT', event: 'accountsChanged', payload: payload.map((a) => a.address) });
  } catch (err) {
    console.warn('[bg] failed to broadcast accountsChanged', err);
  }
}

async function notifyChainChanged(net?: Network) {
  try {
    const network = net ?? (await getRpcClient()).network;
    const chainIdHex = `0x${network.chainId.toString(16)}`;
    broadcast({ type: 'network/changed', chainId: network.chainId, name: network.name });
    broadcast({ channel: 'animica', type: 'EVENT', event: 'chainChanged', payload: { chainId: chainIdHex, name: network.name } });
  } catch (err) {
    console.warn('[bg] failed to broadcast chainChanged', err);
  }
}

async function fetchHead(client: RpcClient): Promise<any> {
  const attempts = ['chain_getHead', 'animica_getHead', 'omni_getHead'];
  let lastError: unknown;
  for (const method of attempts) {
    try {
      return await client.call<any>(method);
    } catch (err) {
      lastError = err;
    }
  }
  // As a last resort, try a health ping.
  await client.health();
  if (lastError) throw lastError;
  return null;
}

async function maybeHandleLegacyMessage(
  msg: any,
  sendResponse: (resp: unknown) => void,
): Promise<boolean> {
  if (!msg || typeof msg !== 'object') return false;

  // In-page provider bridge (content script → background)
  if (msg.channel === 'animica' && msg.type === 'REQUEST') {
    const { method, params } = msg.payload || {};
    try {
      const { client, network } = await getRpcClient();
      if (method === 'animica_requestAccounts' || method === 'eth_requestAccounts' || method === 'wallet_requestPermissions') {
        await keyring.init();
        const accounts = await keyring.listAccounts();
        const addrs = accounts.map((a) => a.address);
        sendResponse({ ok: true, id: msg.id, result: addrs });
        await notifyAccountsChanged();
        return true;
      }
      if (method === 'animica_accounts' || method === 'eth_accounts') {
        await keyring.init();
        const accounts = await keyring.listAccounts();
        sendResponse({ ok: true, id: msg.id, result: accounts.map((a) => a.address) });
        return true;
      }
      if (method === 'animica_chainId' || method === 'eth_chainId') {
        sendResponse({ ok: true, id: msg.id, result: `0x${network.chainId.toString(16)}` });
        return true;
      }

      const result = await client.call(method, params as any);
      sendResponse({ ok: true, id: msg.id, result });
      return true;
    } catch (err: any) {
      sendResponse({ ok: false, id: msg.id, error: { message: err?.message ?? String(err), code: err?.code ?? err?.status } });
      return true;
    }
  }

  if (msg.kind === 'accounts:list') {
    await keyring.init();
    const accounts = await keyring.listAccounts();
    const selected = await keyring.getSelected();
    const locked = await keyring.isLocked();
    sendResponse({
      ok: true,
      accounts: accounts.map((a) => ({ address: a.address, name: a.label, algo: (a as any).algo ?? (a as any).alg, path: a.path })),
      selected: selected?.address,
      locked,
    });
    return true;
  }

  if (msg.kind === 'accounts:select') {
    await keyring.init();
    const accounts = await keyring.listAccounts();
    const acct = accounts.find((a) => a.address === msg.address);
    if (!acct) {
      sendResponse({ ok: false, error: 'Account not found' });
      return true;
    }
    await keyring.selectAccount(acct.id);
    await notifyAccountsChanged();
    sendResponse({ ok: true });
    return true;
  }

  if (msg.kind === 'keyring.generateMnemonic' || msg.type === 'keyring.generateMnemonic') {
    const words = msg.words === 24 ? 24 : 12;
    const mnemonic = generateMnemonic(words);
    sendResponse({ ok: true, mnemonic });
    return true;
  }

  if (msg.kind === 'keyring.setupVault' || msg.type === 'keyring.setupVault') {
    const pin = msg.pin ?? msg.password ?? '';
    const { account } = await keyring.importMnemonic({
      mnemonic: msg.mnemonic,
      password: pin,
      storeMnemonic: true,
      initialAlg: msg.algo ?? msg.initialAlg,
    });
    await notifyAccountsChanged();
    void rpcSanityCheck('post-setup');
    sendResponse({ ok: true, result: { address: account?.address ?? '' } });
    return true;
  }

  if (msg.kind === 'sessions.reset') {
    try {
      await clearKeyringSession();
    } catch (err) {
      console.warn('[bg] failed to clear keyring session', err);
    }
    try {
      keyring.lock();
    } catch (err) {
      console.warn('[bg] failed to lock keyring', err);
    }
    sendResponse({ ok: true });
    return true;
  }

  if (msg.type === 'keyring/getPrimaryAddress' || msg.kind === 'keyring.getPrimaryAddress') {
    await keyring.init();
    const selected = await keyring.getSelected();
    const all = await keyring.listAccounts();
    const primary = selected ?? all[0];
    sendResponse({ ok: true, result: { address: primary?.address ?? '' } });
    return true;
  }

  if (msg.type === 'vault.export') {
    const envelope = await loadVaultEnvelope();
    if (!envelope) {
      sendResponse({ ok: false, error: 'No vault data to export yet.' });
      return true;
    }
    const json = JSON.stringify(envelope, null, 2);
    const dataUrl = `data:application/json,${encodeURIComponent(json)}`;
    sendResponse({ ok: true, dataUrl, fileName: 'animica-vault.json' });
    return true;
  }

  if (msg.kind === 'networks:list') {
    const { networks, selected } = await listNetworks();
    sendResponse({
      ok: true,
      networks: networks.map((n) => ({ chainId: n.chainId, name: n.name, rpcUrl: n.rpcHttp, key: n.id })),
      selected: selected.chainId,
    });
    return true;
  }

  if (msg.kind === 'networks:select') {
    try {
      const net = await selectNetworkByChainId(Number(msg.chainId));
      await notifyChainChanged(net);
      sendResponse({ ok: true });
    } catch (err: any) {
      sendResponse({ ok: false, error: err?.message ?? String(err) });
    }
    return true;
  }

  if (msg.type === 'network/getSelected') {
    const { network } = await getRpcClient();
    sendResponse({ ok: true, result: { chainId: network.chainId, name: network.name } });
    return true;
  }

  if (msg.type === 'rpc.getHead') {
    try {
      const { client, network } = await getRpcClient();
      const head = await fetchHead(client);
      sendResponse({ ok: true, result: { ...(head ?? {}), chainId: network.chainId, networkName: network.name } });
    } catch (err: any) {
      sendResponse({ ok: false, error: err?.message ?? String(err) });
    }
    return true;
  }

  if (msg.type === 'wallet.getRecentTxs') {
    sendResponse({ ok: true, result: [] });
    return true;
  }

  return false;
}

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

async function logRpcStatus(context: string) {
  await rpcSanityCheck(`bg:${context}`);
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

  void logRpcStatus('install');

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
  void logRpcStatus('startup');
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
      sendResponse(res);
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
