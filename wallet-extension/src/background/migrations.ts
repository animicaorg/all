/**
 * Storage schema & migrations (MV3, chrome.storage.local).
 *
 * - We keep ALL extension state under a single root key to simplify atomic reads/writes.
 * - Forward-only migrations: apply sequentially from `schemaVersion` → LATEST_SCHEMA_VERSION.
 * - All migrations must be pure and idempotent (safe to re-run).
 */

 /* eslint-disable no-console */

export const STORAGE_ROOT_KEY = 'animica_wallet_v1'; // namespace root (keep stable once released)
export const LATEST_SCHEMA_VERSION = 3;

export type AlgoId = 'dilithium3' | 'sphincs_shake_128s';

export interface AccountMeta {
  id: string;               // stable uuid
  address: string;          // bech32m anim1...
  name?: string;
  algo: AlgoId;
  path?: string;            // derivation hint (if any)
  createdAt?: number;
}

export interface KeyringState {
  vaultCiphertext?: string; // base64 AES-GCM payload
  vaultSalt?: string;       // base64 salt for PBKDF/HKDF
  unlocked: boolean;
  accounts: AccountMeta[];
  selectedAccountId?: string;
  // Future: hardware/account import flags, lastUnlockAt, etc.
}

export interface NetworkEntry {
  id: string;               // e.g. "animica-devnet"
  chainId: number;
  name: string;
  rpcUrl: string;
  wsUrl?: string;
}

export interface NetworkState {
  currentId: string;                      // key of the selected network
  known: Record<string, NetworkEntry>;    // registry keyed by id
}

export interface PermissionGrant {
  approved: boolean;
  accounts?: string[];     // allowed account ids for this origin
  lastApprovedAt?: number;
}

export interface PermissionsState {
  perOrigin: Record<string, PermissionGrant>;
  allowHostnames: string[];
  denyHostnames: string[];
}

export interface SessionItem {
  origin: string;
  accountId: string | null; // currently selected for this origin
  networkId: string | null; // sticky network for this origin (optional)
  connectedAt: number;
}

export interface SessionsState {
  items: SessionItem[];
}

export interface SettingsState {
  language: string;         // 'en'
  theme: 'system' | 'light' | 'dark';
  telemetry: boolean;
  devMode: boolean;
}

export interface StorageState {
  schemaVersion: number;
  createdAt: number;
  updatedAt: number;
  keyring: KeyringState;
  network: NetworkState;
  permissions: PermissionsState;
  sessions: SessionsState;
  settings: SettingsState;
}

/* -------------------------------- Defaults ------------------------------- */

const DEFAULT_NETWORK_ID = (import.meta as any)?.env?.VITE_DEFAULT_NETWORK_ID ?? 'animica-devnet';
const DEFAULT_CHAIN_ID = Number((import.meta as any)?.env?.VITE_CHAIN_ID ?? 91002);
const DEFAULT_RPC_URL = (import.meta as any)?.env?.VITE_RPC_URL ?? 'http://127.0.0.1:8545';
const DEFAULT_WS_URL  = (import.meta as any)?.env?.VITE_WS_URL ?? 'ws://127.0.0.1:8546';

function defaultNetworks(): NetworkState {
  const entry: NetworkEntry = {
    id: DEFAULT_NETWORK_ID,
    chainId: DEFAULT_CHAIN_ID,
    name: 'Animica Devnet',
    rpcUrl: DEFAULT_RPC_URL,
    wsUrl: DEFAULT_WS_URL,
  };
  return {
    currentId: entry.id,
    known: { [entry.id]: entry },
  };
}

export function defaultState(now = Date.now()): StorageState {
  return {
    schemaVersion: LATEST_SCHEMA_VERSION,
    createdAt: now,
    updatedAt: now,
    keyring: {
      unlocked: false,
      accounts: [],
      selectedAccountId: undefined,
      vaultCiphertext: undefined,
      vaultSalt: undefined,
    },
    network: defaultNetworks(),
    permissions: {
      perOrigin: {},
      allowHostnames: [],
      denyHostnames: [],
    },
    sessions: {
      items: [],
    },
    settings: {
      language: 'en',
      theme: 'system',
      telemetry: false,
      devMode: false,
    },
  };
}

/* ------------------------------ Storage IO -------------------------------- */

async function readRoot(): Promise<any | undefined> {
  const obj = await chrome.storage.local.get(STORAGE_ROOT_KEY);
  return (obj as any)[STORAGE_ROOT_KEY];
}

async function writeRoot(state: StorageState): Promise<void> {
  const payload = { [STORAGE_ROOT_KEY]: state };
  await chrome.storage.local.set(payload);
}

/* ------------------------------ Migrations -------------------------------- */

// Helpers for defensive merges
function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === 'object' && x !== null && !Array.isArray(x);
}
function asNumber(x: unknown, fallback: number): number {
  return typeof x === 'number' && isFinite(x) ? x : fallback;
}
function asString(x: unknown, fallback: string): string {
  return typeof x === 'string' ? x : fallback;
}
function coerceBoolean(x: unknown, fallback: boolean): boolean {
  return typeof x === 'boolean' ? x : fallback;
}

type Migrator = (raw: any) => StorageState;

/**
 * v0 -> v1 (bootstrap):
 * - If nothing in storage (undefined), create default state.
 * - If some legacy scattered keys exist, attempt shallow import.
 */
const migrate_0_to_1: Migrator = (raw: any): StorageState => {
  const now = Date.now();
  const base = defaultState(now);

  if (!isObject(raw)) {
    return { ...base, schemaVersion: 1 };
  }

  // Attempt best-effort import of legacy shapes
  const keyringRaw = isObject(raw.keyring) ? raw.keyring : {};
  const networkRaw = isObject(raw.network) ? raw.network : {};
  const settingsRaw = isObject(raw.settings) ? raw.settings : {};
  const permissionsRaw = isObject(raw.permissions) ? raw.permissions : {};
  const sessionsRaw = isObject(raw.sessions) ? raw.sessions : {};

  const state: StorageState = {
    ...base,
    schemaVersion: 1,
    keyring: {
      unlocked: coerceBoolean((keyringRaw as any).unlocked, false),
      accounts: Array.isArray((keyringRaw as any).accounts) ? (keyringRaw as any).accounts : [],
      selectedAccountId: (keyringRaw as any).selectedAccountId,
      vaultCiphertext: (keyringRaw as any).vaultCiphertext,
      vaultSalt: (keyringRaw as any).vaultSalt,
    },
    network: {
      currentId: asString((networkRaw as any).currentId, base.network.currentId),
      known: isObject((networkRaw as any).known) ? (networkRaw as any).known as any : base.network.known,
    },
    permissions: {
      perOrigin: isObject((permissionsRaw as any).perOrigin) ? (permissionsRaw as any).perOrigin as any : {},
      allowHostnames: Array.isArray((permissionsRaw as any).allowHostnames) ? (permissionsRaw as any).allowHostnames : [],
      denyHostnames: Array.isArray((permissionsRaw as any).denyHostnames) ? (permissionsRaw as any).denyHostnames : [],
    },
    sessions: {
      items: Array.isArray((sessionsRaw as any).items) ? (sessionsRaw as any).items : [],
    },
    settings: {
      language: asString((settingsRaw as any).language, 'en'),
      theme: ['system', 'light', 'dark'].includes((settingsRaw as any).theme) ? (settingsRaw as any).theme : 'system',
      telemetry: coerceBoolean((settingsRaw as any).telemetry, false),
      devMode: coerceBoolean((settingsRaw as any).devMode, false),
    },
    createdAt: asNumber((raw as any).createdAt, now),
    updatedAt: now,
  };

  // Ensure at least one known network present
  if (!isObject(state.network.known) || Object.keys(state.network.known).length === 0) {
    state.network = defaultNetworks();
  }
  if (!state.network.known[state.network.currentId]) {
    state.network.currentId = Object.keys(state.network.known)[0];
  }

  return state;
};

/**
 * v1 -> v2:
 * - Ensure network entry contains wsUrl (introduced now).
 * - Backfill names for known networks when missing.
 */
const migrate_1_to_2: Migrator = (raw: any): StorageState => {
  const prev = raw as StorageState;
  const now = Date.now();
  const out: StorageState = { ...prev, schemaVersion: 2, updatedAt: now };

  if (!isObject(out.network?.known)) {
    out.network = defaultNetworks();
  } else {
    for (const [id, entry] of Object.entries(out.network.known)) {
      const e = entry as any;
      if (!('wsUrl' in e)) {
        e.wsUrl = DEFAULT_WS_URL;
      }
      if (!e.name) e.name = id.includes('dev') ? 'Animica Devnet' : 'Animica Network';
      if (typeof e.chainId !== 'number') e.chainId = DEFAULT_CHAIN_ID;
      if (!e.rpcUrl) e.rpcUrl = DEFAULT_RPC_URL;
      out.network.known[id] = e as NetworkEntry;
    }
    if (!out.network.currentId || !out.network.known[out.network.currentId]) {
      out.network.currentId = Object.keys(out.network.known)[0] ?? defaultNetworks().currentId;
    }
  }

  return out;
};

/**
 * v2 -> v3:
 * - Move any legacy `permissions.sessions` array to `sessions.items`.
 * - Normalize session fields and drop invalid ones.
 */
const migrate_2_to_3: Migrator = (raw: any): StorageState => {
  const prev = raw as StorageState;
  const now = Date.now();
  const out: StorageState = { ...prev, schemaVersion: 3, updatedAt: now };

  // Legacy location: permissions.sessions (array)
  const legacySessions = (prev as any)?.permissions?.sessions;
  const items: SessionItem[] = Array.isArray(prev.sessions?.items) ? [...prev.sessions.items] : [];

  if (Array.isArray(legacySessions)) {
    for (const it of legacySessions) {
      if (!isObject(it)) continue;
      const origin = asString((it as any).origin, '');
      if (!origin) continue;
      items.push({
        origin,
        accountId: typeof (it as any).accountId === 'string' ? (it as any).accountId : null,
        networkId: typeof (it as any).networkId === 'string' ? (it as any).networkId : null,
        connectedAt: asNumber((it as any).connectedAt, now),
      });
    }
    // Drop legacy field if we imported it
    if (isObject((out as any).permissions)) {
      delete (out as any).permissions.sessions;
    }
  }

  // De-duplicate by origin (keep most recent)
  items.sort((a, b) => b.connectedAt - a.connectedAt);
  const seen = new Set<string>();
  const deduped: SessionItem[] = [];
  for (const it of items) {
    if (seen.has(it.origin)) continue;
    seen.add(it.origin);
    deduped.push(it);
  }
  out.sessions = { items: deduped };

  return out;
};

const MIGRATIONS: Record<number, Migrator> = {
  0: migrate_0_to_1,
  1: migrate_1_to_2,
  2: migrate_2_to_3,
};

/* --------------------------- Public entrypoints --------------------------- */

/**
 * Ensure storage exists and is upgraded to the latest schema.
 * Returns the up-to-date state.
 */
export async function ensureStorageSchema(): Promise<StorageState> {
  const raw = await readRoot();

  // New install
  if (raw === undefined) {
    const fresh = defaultState();
    await writeRoot(fresh);
    if (process.env.NODE_ENV !== 'production') {
      console.info('[migrations] initialized fresh state @ v%s', LATEST_SCHEMA_VERSION);
    }
    return fresh;
  }

  // Existing install — figure out version
  let version = Number((raw as any).schemaVersion ?? 0);
  if (!Number.isFinite(version) || version < 0) version = 0;

  // Already current
  if (version >= LATEST_SCHEMA_VERSION) {
    return raw as StorageState;
  }

  // Apply forward-only migrations
  let state: StorageState = raw as any;
  while (version < LATEST_SCHEMA_VERSION) {
    const step = MIGRATIONS[version];
    if (!step) {
      // If we ever skip a version accidentally, fall back to default to avoid bricking users.
      console.warn('[migrations] missing migrator for v%s → v%s; resetting to defaults', version, version + 1);
      state = defaultState();
      break;
    }
    state = step(state);
    version = state.schemaVersion;
    if (process.env.NODE_ENV !== 'production') {
      console.info('[migrations] migrated → v%s', version);
    }
  }

  // Persist upgraded state
  await writeRoot(state);
  return state;
}

/**
 * Read the current (already-migrated) state from storage.
 * NOTE: Call ensureStorageSchema() once at startup.
 */
export async function readState(): Promise<StorageState> {
  const raw = await readRoot();
  if (!raw) {
    const fresh = defaultState();
    await writeRoot(fresh);
    return fresh;
  }
  return raw as StorageState;
}

/**
 * Write a partial update to storage, bumping updatedAt.
 * Use this for small atomic patches within background logic.
 */
export async function patchState(patch: Partial<StorageState> | ((curr: StorageState) => Partial<StorageState>)): Promise<StorageState> {
  const curr = await readState();
  const delta = typeof patch === 'function' ? patch(curr) : patch;
  const next: StorageState = {
    ...curr,
    ...delta,
    // Deep merge for nested slices we know about
    keyring: { ...curr.keyring, ...(delta as any).keyring },
    network: { ...curr.network, ...(delta as any).network },
    permissions: { ...curr.permissions, ...(delta as any).permissions },
    sessions: { ...curr.sessions, ...(delta as any).sessions },
    settings: { ...curr.settings, ...(delta as any).settings },
    updatedAt: Date.now(),
  };
  await writeRoot(next);
  return next;
}
