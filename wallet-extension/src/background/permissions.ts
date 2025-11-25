/**
 * Per-origin permissions & session approvals.
 *
 * Responsibilities
 *  - Track which sites (origins) are allowed to connect and with which account(s).
 *  - Maintain short-lived "sessions" so subsequent requests don't re-prompt immediately.
 *  - Provide an approval workflow surface that the Approvals UI can drive.
 *  - Persist durable grants in chrome.storage.local; keep pending approvals in-memory.
 *
 * Design notes
 *  - We scope grants by origin (scheme+host+port).
 *  - A grant contains the allowed account list and default chainId used for requests.
 *  - A "session" is an ephemeral approval window (default TTL 8h) attached to a single account.
 *  - The Approvals UI opens when a pending approval is created; it resolves or rejects by id.
 *  - We broadcast updates via chrome.runtime.sendMessage for UI/content to react to.
 */

import { storage } from "./runtime";
import type { Network } from "./network/networks";
import { isOriginAllowed } from "./host_permissions"; // simple allow/deny list (implemented separately)

export type Hex = `0x${string}`;
export type Address = string; // bech32m anim1... string

/* ----------------------------- constants ------------------------------ */

const STORAGE_KEY = "permissions.v1";
const SESSION_TTL_MS = 8 * 60 * 60 * 1000; // 8h default

const DEFAULT_METHODS = Object.freeze([
  "wallet_requestAccounts",
  "animica_requestAccounts",
  "animica_signMessage",
  "animica_sendTransaction",
  "animica_call",
  "animica_estimateGas",
]);

/* ----------------------------- types ------------------------------ */

export interface PermissionRecord {
  origin: string;
  accounts: Address[];          // accounts this origin may access
  methods: readonly string[];   // allowed method names (broad, coarse-grained)
  chains: number[];             // chainIds this origin may use
  createdAt: number;            // ms epoch
  updatedAt: number;            // ms epoch
  // A convenience "last session" snapshot (non-authoritative; sessions are listed separately)
  lastSession?: SessionRecord;
}

export interface SessionRecord {
  id: string;
  origin: string;
  account: Address;
  chainId: number;
  approvedAt: number;  // ms epoch
  expiresAt: number;   // ms epoch
  lastActiveAt: number;// ms epoch
}

export type ApprovalKind = "connect" | "sign" | "sendTx";

export interface ApprovalRequestBase {
  id: string;
  kind: ApprovalKind;
  origin: string;
  createdAt: number;
}

export interface ConnectApprovalPayload {
  requestedAccounts?: number | Address[]; // number = how many accounts to expose; list = explicit accounts
  requestedChainId?: number;
}

export interface SignApprovalPayload {
  account: Address;
  domain: string;       // domain separation string
  bytes: Hex;           // message or sign-bytes
}

export interface SendTxApprovalPayload {
  account: Address;
  chainId: number;
  signBytes: Hex;       // CBOR-encoded sign bytes preview
  pretty?: any;         // optional pre-parsed human preview (fee, to, value)
}

export type ApprovalPayload =
  | ({ kind: "connect" } & ConnectApprovalPayload)
  | ({ kind: "sign" } & SignApprovalPayload)
  | ({ kind: "sendTx" } & SendTxApprovalPayload);

export type ApprovalRequest = ApprovalRequestBase & ApprovalPayload;

export interface ApprovalResolution {
  id: string;
  approved: boolean;
  reason?: string;
  // On success, optional enriched fields depending on kind:
  accounts?: Address[];
  account?: Address;
  chainId?: number;
}

/* ----------------------------- helpers ------------------------------ */

function now(): number {
  return Date.now();
}

function toOrigin(u: string): string {
  try {
    const url = new URL(u);
    return url.origin;
  } catch {
    // If already an origin, return as-is
    return u;
  }
}

function randId(): string {
  const a = new Uint8Array(16);
  crypto.getRandomValues(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
}

/* ----------------------------- state ------------------------------ */

type PermMap = Record<string, PermissionRecord>; // key: origin

let _loaded = false;
let _perms: PermMap = Object.create(null);
let _sessions: Map<string, SessionRecord> = new Map(); // by session id
let _pending: Map<string, ApprovalRequest> = new Map(); // approvals awaiting user action

/* ----------------------------- persistence ------------------------------ */

async function load(): Promise<void> {
  if (_loaded) return;
  const data = await storage.get<{ perms?: PermMap; sessions?: SessionRecord[] }>(STORAGE_KEY);
  if (data?.perms) _perms = data.perms;
  if (Array.isArray(data?.sessions)) {
    for (const s of data!.sessions!) _sessions.set(s.id, s);
  }
  _loaded = true;
}

async function persist(): Promise<void> {
  // Persist sessions as an array; pending approvals are intentionally not persisted.
  await storage.set(STORAGE_KEY, {
    perms: _perms,
    sessions: Array.from(_sessions.values()),
  });
}

/* ----------------------------- broadcasts ------------------------------ */

function broadcast(type: string, payload: any): void {
  // eslint-disable-next-line @typescript-eslint/ban-ts-comment
  // @ts-ignore
  if (chrome?.runtime?.sendMessage) {
    chrome.runtime.sendMessage({ __animica: true, type, payload });
  }
}

/* ----------------------------- core API ------------------------------ */

export async function getPermission(originOrUrl: string): Promise<PermissionRecord | null> {
  await load();
  const key = toOrigin(originOrUrl);
  return _perms[key] ?? null;
}

export async function listPermissions(): Promise<PermissionRecord[]> {
  await load();
  return Object.values(_perms).sort((a, b) => a.origin.localeCompare(b.origin));
}

export async function isConnected(originOrUrl: string): Promise<boolean> {
  return (await getPermission(originOrUrl)) != null;
}

export async function revokeOrigin(originOrUrl: string): Promise<void> {
  await load();
  const key = toOrigin(originOrUrl);
  const existed = Boolean(_perms[key]);
  delete _perms[key];
  // drop sessions for this origin
  for (const [id, s] of _sessions) {
    if (s.origin === key) _sessions.delete(id);
  }
  if (existed) {
    await persist();
    broadcast("perm:revoked", { origin: key });
  }
}

/**
 * Upsert a durable grant for an origin.
 * - `accounts` must be a non-empty list.
 * - `chainId` will be added to allowed chains if absent.
 */
export async function upsertGrant(params: {
  origin: string;
  accounts: Address[];
  chainId: number;
  methods?: readonly string[];
}): Promise<PermissionRecord> {
  await load();
  const origin = toOrigin(params.origin);
  if (!isOriginAllowed(origin)) {
    throw new Error("Origin not allowed by policy");
  }
  if (!params.accounts?.length) {
    throw new Error("accounts must be non-empty");
  }
  const createdAt = now();
  const existing = _perms[origin];
  const methods = params.methods ?? existing?.methods ?? DEFAULT_METHODS;
  const chains = new Set<number>(existing?.chains ?? []);
  chains.add(params.chainId);

  const record: PermissionRecord = {
    origin,
    accounts: Array.from(new Set([...(existing?.accounts ?? []), ...params.accounts])),
    methods,
    chains: Array.from(chains.values()),
    createdAt: existing?.createdAt ?? createdAt,
    updatedAt: createdAt,
    lastSession: existing?.lastSession,
  };

  _perms[origin] = record;
  await persist();
  broadcast("perm:updated", { origin, record });
  return record;
}

/**
 * Create a new session for an origin/account.
 * Sessions are ephemeral approvals that avoid re-prompts.
 */
export async function createSession(originOrUrl: string, account: Address, chainId: number, ttlMs = SESSION_TTL_MS): Promise<SessionRecord> {
  await load();
  const origin = toOrigin(originOrUrl);
  const t = now();
  const sess: SessionRecord = {
    id: randId(),
    origin,
    account,
    chainId,
    approvedAt: t,
    lastActiveAt: t,
    expiresAt: t + Math.max(60_000, ttlMs), // at least 1 min
  };
  _sessions.set(sess.id, sess);

  // Update lastSession snapshot on the durable record (if present)
  if (_perms[origin]) {
    _perms[origin].lastSession = sess;
  }
  await persist();
  broadcast("perm:sessionCreated", { session: sess });
  return sess;
}

/**
 * Get a still-valid session for an origin (optionally for a specific account).
 * If expired, it is removed and null is returned.
 */
export async function getActiveSession(originOrUrl: string, account?: Address): Promise<SessionRecord | null> {
  await load();
  const origin = toOrigin(originOrUrl);
  const t = now();
  for (const s of _sessions.values()) {
    if (s.origin !== origin) continue;
    if (account && s.account !== account) continue;
    if (s.expiresAt <= t) {
      _sessions.delete(s.id);
      continue;
    }
    return s;
  }
  return null;
}

export async function touchSession(sessionId: string): Promise<void> {
  await load();
  const s = _sessions.get(sessionId);
  if (!s) return;
  s.lastActiveAt = now();
  await persist();
}

export async function endSession(sessionId: string): Promise<void> {
  await load();
  if (_sessions.delete(sessionId)) {
    await persist();
    broadcast("perm:sessionEnded", { sessionId });
  }
}

/* ----------------------------- approval flow ------------------------------ */

/**
 * Create a pending approval. This does not change state until approved/denied.
 * The Approvals UI should be opened by the caller after this returns, using `approval.id`.
 */
export async function createApproval(input: ApprovalPayload & { origin: string }): Promise<ApprovalRequest> {
  await load();
  const origin = toOrigin(input.origin);
  if (!isOriginAllowed(origin)) {
    throw new Error("Origin not allowed by policy");
  }
  const approval: ApprovalRequest = {
    id: randId(),
    kind: input.kind,
    origin,
    createdAt: now(),
    ...(input as any),
  };
  _pending.set(approval.id, approval);
  broadcast("perm:approvalCreated", { approval });
  return approval;
}

export function getPendingApproval(id: string): ApprovalRequest | null {
  return _pending.get(id) ?? null;
}

export async function denyApproval(id: string, reason = "User rejected"): Promise<ApprovalResolution> {
  await load();
  const req = _pending.get(id);
  if (!req) throw new Error("Unknown approval id");
  _pending.delete(id);
  const res: ApprovalResolution = { id, approved: false, reason };
  broadcast("perm:approvalResolved", { resolution: res, request: req });
  return res;
}

/**
 * Resolve an approval positively and apply the implied state changes.
 * - connect → upsert grant + create session (single account exposure by default)
 * - sign/sendTx → no durable changes; caller proceeds with signing / submitting
 */
export async function approveApproval(
  id: string,
  decision: { accounts?: Address[]; account?: Address; chainId?: number; sessionTtlMs?: number }
): Promise<ApprovalResolution> {
  await load();
  const req = _pending.get(id);
  if (!req) throw new Error("Unknown approval id");

  const resolution: ApprovalResolution = { id, approved: true };

  if (req.kind === "connect") {
    const chosenAccounts =
      (decision.accounts && decision.accounts.length ? decision.accounts :
        Array.isArray((req as any).requestedAccounts) ? (req as any).requestedAccounts as Address[] :
        []); // empty means "let UI pick" — but we expect caller to pass at least one

    if (!chosenAccounts.length) {
      throw new Error("At least one account must be granted");
    }
    const chainId = decision.chainId ?? (req as any).requestedChainId ?? (await defaultChainId());
    const grant = await upsertGrant({ origin: req.origin, accounts: chosenAccounts, chainId });
    // Create a session for the first account
    const sess = await createSession(req.origin, chosenAccounts[0], chainId, decision.sessionTtlMs ?? SESSION_TTL_MS);
    grant.lastSession = sess;
    resolution.accounts = chosenAccounts;
    resolution.chainId = chainId;
  } else if (req.kind === "sign") {
    // No durable state to mutate; approval authorizes the one-off sign.
    resolution.account = decision.account ?? (req as any).account;
  } else if (req.kind === "sendTx") {
    resolution.account = decision.account ?? (req as any).account;
    resolution.chainId = decision.chainId ?? (req as any).chainId;
  }

  _pending.delete(id);
  broadcast("perm:approvalResolved", { resolution, request: req });
  return resolution;
}

/* ----------------------------- policy helpers ------------------------------ */

async function defaultChainId(): Promise<number> {
  // Derive from stored "selected network" if present; fall back to 1 (mainnet-like).
  // We keep this minimal to avoid a hard import cycle; router or sessions manager can set it.
  const cfg = await storage.get<{ "net.selected"?: Network }>("net.selected");
  return cfg?.["net.selected"]?.chainId ?? 1;
}

/* ----------------------------- convenience API for provider ------------------------------ */

/**
 * Ensure the origin has a valid session; if not, initiate a connect approval.
 * The caller (router) should then open Approvals UI using the returned approval id.
 */
export async function ensureSessionForOrigin(originOrUrl: string, opts?: {
  requestedAccounts?: number | Address[];
  requestedChainId?: number;
  sessionTtlMs?: number;
}): Promise<{ session?: SessionRecord; approval?: ApprovalRequest }> {
  await load();
  const origin = toOrigin(originOrUrl);

  // If we have an active session, return it.
  const existingGrant = await getPermission(origin);
  if (existingGrant) {
    const preferredAccount = existingGrant.lastSession?.account ?? existingGrant.accounts[0];
    const s = await getActiveSession(origin, preferredAccount);
    if (s) return { session: s };
  }

  // Otherwise, create a connect approval.
  const approval = await createApproval({
    kind: "connect",
    origin,
    requestedAccounts: opts?.requestedAccounts,
    requestedChainId: opts?.requestedChainId,
  } as any);
  return { approval };
}

export default {
  getPermission,
  listPermissions,
  isConnected,
  revokeOrigin,
  upsertGrant,
  createSession,
  getActiveSession,
  touchSession,
  endSession,
  createApproval,
  getPendingApproval,
  approveApproval,
  denyApproval,
  ensureSessionForOrigin,
};
