/**
 * Connected sites & selected account/network
 *
 * - Tracks the currently selected wallet account (global selection for the popup/UI).
 * - Tracks the currently selected network (global) and optional per-origin preference.
 * - Surfaces a view of "connected sites" by joining durable grants with active sessions.
 * - Provides helpers to initiate or revoke a site's connection (delegates to permissions API).
 *
 * Storage keys (chrome.storage.local via runtime.storage):
 *  - "wallet.selectedAccount": Address (bech32m)
 *  - "net.selected": Network (see network/networks.ts)
 *  - "sessions.sitePrefs.v1": { [origin]: { account?: Address, chainId?: number } }
 */

import { storage } from "./runtime";
import type { Address } from "./permissions";
import {
  listPermissions,
  revokeOrigin,
  getActiveSession,
  ensureSessionForOrigin,
  type ApprovalRequest,
  type SessionRecord,
} from "./permissions";
import {
  KNOWN_NETWORKS,
  getNetworkByChainId,
  getDefaultNetwork,
  type Network,
} from "./network/networks";

// Lazy import to avoid cycles (keyring is optional for some calls)
let _keyring: typeof import("./keyring/index");
async function keyring() {
  if (!_keyring) {
    _keyring = await import("./keyring/index");
  }
  return _keyring;
}

/* ----------------------------- storage keys ------------------------------ */

const KEY_SELECTED_ACCOUNT = "wallet.selectedAccount";
const KEY_SELECTED_NETWORK = "net.selected";
const KEY_SITE_PREFS = "sessions.sitePrefs.v1";

/* ----------------------------- types ------------------------------ */

export interface SitePreference {
  account?: Address;
  chainId?: number;
}
export type SitePrefsMap = Record<string, SitePreference>;

export interface ConnectedSiteRecord {
  origin: string;
  accounts: Address[];
  chains: number[];
  activeSession: SessionRecord | null;
  lastSeenAccount?: Address;
}

/* ----------------------------- broadcast helpers ------------------------------ */

function broadcast(type: string, payload: any): void {
  // eslint-disable-next-line @typescript-eslint/ban-ts-comment
  // @ts-ignore
  if (chrome?.runtime?.sendMessage) {
    chrome.runtime.sendMessage({ __animica: true, type, payload });
  }
}

/* ----------------------------- selected account ------------------------------ */

export async function getSelectedAccount(): Promise<Address | null> {
  const rec = await storage.get<Address | null>(KEY_SELECTED_ACCOUNT);
  if (rec) return rec;

  // Fallback: first available keyring account, if any
  try {
    const kr = await keyring();
    const accounts = await kr.listAccounts();
    if (accounts.length) {
      await setSelectedAccount(accounts[0]);
      return accounts[0];
    }
  } catch {
    // ignore if keyring not available yet
  }
  return null;
}

export async function setSelectedAccount(addr: Address): Promise<void> {
  await storage.set(KEY_SELECTED_ACCOUNT, addr);
  broadcast("wallet:selectedAccount", { account: addr });
}

/* ----------------------------- selected network ------------------------------ */

export async function getSelectedNetwork(): Promise<Network> {
  const rec = await storage.get<Network | null>(KEY_SELECTED_NETWORK);
  if (rec && typeof rec.chainId === "number") return rec;

  const def = getDefaultNetwork();
  await setSelectedNetworkByChainId(def.chainId);
  return def;
}

export async function setSelectedNetworkById(id: string): Promise<Network> {
  const found = KNOWN_NETWORKS.find((n) => n.id === id);
  if (!found) throw new Error(`Unknown network id: ${id}`);
  await storage.set(KEY_SELECTED_NETWORK, found);
  broadcast("net:selectedNetwork", { network: found });
  return found;
}

export async function setSelectedNetworkByChainId(chainId: number): Promise<Network> {
  const found = getNetworkByChainId(chainId);
  if (!found) throw new Error(`Unknown chainId: ${chainId}`);
  await storage.set(KEY_SELECTED_NETWORK, found);
  broadcast("net:selectedNetwork", { network: found });
  return found;
}

/* ----------------------------- per-site preferences ------------------------------ */

export async function getSitePreference(origin: string): Promise<SitePreference | null> {
  const prefs = (await storage.get<SitePrefsMap | null>(KEY_SITE_PREFS)) || {};
  const o = normalizeOrigin(origin);
  return prefs[o] ?? null;
}

export async function setSitePreference(origin: string, pref: SitePreference): Promise<void> {
  const o = normalizeOrigin(origin);
  const prefs = (await storage.get<SitePrefsMap | null>(KEY_SITE_PREFS)) || {};
  prefs[o] = { ...prefs[o], ...pref };
  await storage.set(KEY_SITE_PREFS, prefs);
  broadcast("sessions:sitePreference", { origin: o, pref: prefs[o] });
}

export async function clearSitePreference(origin: string): Promise<void> {
  const o = normalizeOrigin(origin);
  const prefs = (await storage.get<SitePrefsMap | null>(KEY_SITE_PREFS)) || {};
  if (prefs[o]) {
    delete prefs[o];
    await storage.set(KEY_SITE_PREFS, prefs);
    broadcast("sessions:sitePreferenceCleared", { origin: o });
  }
}

/* ----------------------------- connected sites view ------------------------------ */

export async function listConnectedSites(): Promise<ConnectedSiteRecord[]> {
  const perms = await listPermissions();
  const out: ConnectedSiteRecord[] = [];
  for (const p of perms) {
    const preferred = p.lastSession?.account ?? p.accounts[0];
    const active = await getActiveSession(p.origin, preferred);
    out.push({
      origin: p.origin,
      accounts: p.accounts,
      chains: p.chains,
      activeSession: active,
      lastSeenAccount: p.lastSession?.account,
    });
  }
  // Sort: active first, then alpha by origin
  out.sort((a, b) => {
    const act = Number(Boolean(b.activeSession)) - Number(Boolean(a.activeSession));
    if (act) return act;
    return a.origin.localeCompare(b.origin);
    });
  return out;
}

/* ----------------------------- connect / disconnect helpers ------------------------------ */

export async function connectSite(originOrUrl: string, opts?: {
  requestedAccounts?: number | Address[];
  requestedChainId?: number;
  sessionTtlMs?: number;
}): Promise<{ session?: SessionRecord; approval?: ApprovalRequest }> {
  const res = await ensureSessionForOrigin(originOrUrl, opts);
  // If an approval is created, signal the UI to open the Approvals window.
  if (res.approval) {
    broadcast("ui:openApprovalWindow", { approvalId: res.approval.id, kind: res.approval.kind, origin: res.approval.origin });
  }
  // Snapshot preferred account/chain for this origin if we already know them.
  if (res.session) {
    await setSitePreference(res.session.origin, { account: res.session.account, chainId: res.session.chainId });
  }
  return res;
}

export async function disconnectSite(originOrUrl: string): Promise<void> {
  const origin = normalizeOrigin(originOrUrl);
  await revokeOrigin(origin);
  await clearSitePreference(origin);
  broadcast("sessions:siteDisconnected", { origin });
}

/* ----------------------------- utilities ------------------------------ */

function normalizeOrigin(input: string): string {
  try {
    const u = new URL(input);
    return u.origin;
  } catch {
    return input;
  }
}

/* ----------------------------- convenience for UI ------------------------------ */

/**
 * Determine which account/chain should be preselected for a given origin,
 * based on (in priority order): site preference → active session → wallet/global defaults.
 */
export async function resolveSelectionForOrigin(originOrUrl: string): Promise<{ account: Address | null; chainId: number }> {
  const origin = normalizeOrigin(originOrUrl);
  const pref = await getSitePreference(origin);
  if (pref?.account || pref?.chainId) {
    const net = pref.chainId ? getNetworkByChainId(pref.chainId) || (await getSelectedNetwork()) : await getSelectedNetwork();
    return { account: pref.account ?? (await getSelectedAccount()), chainId: net.chainId };
  }

  // Try an active session
  const perms = await listPermissions();
  const p = perms.find((x) => x.origin === origin);
  if (p) {
    const acct = p.lastSession?.account ?? p.accounts[0] ?? null;
    const s = acct ? await getActiveSession(origin, acct) : null;
    if (s) return { account: s.account, chainId: s.chainId };
  }

  // Fallback to globals
  const acct = await getSelectedAccount();
  const net = await getSelectedNetwork();
  return { account: acct, chainId: net.chainId };
}

export default {
  getSelectedAccount,
  setSelectedAccount,
  getSelectedNetwork,
  setSelectedNetworkById,
  setSelectedNetworkByChainId,
  getSitePreference,
  setSitePreference,
  clearSitePreference,
  listConnectedSites,
  connectSite,
  disconnectSite,
  resolveSelectionForOrigin,
};
