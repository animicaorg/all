/**
 * Animica Wallet — Internal Message Bus Types
 * ------------------------------------------------------------
 * These types define the transport messages exchanged between:
 *  - background service worker
 *  - content script bridge
 *  - in-page provider (window.animica)
 *  - UI pages (popup / onboarding / approvals)
 *
 * Transport is chrome.runtime.{sendMessage,onMessage} and long-lived Ports.
 * All request/response messages carry a correlation id (`id`) so the
 * content bridge can multiplex in-page requests.
 */

export type BusSide = 'background' | 'content' | 'provider' | 'ui';

export interface BusError {
  /** Stable error code space: 1xxx generic, 2xxx permissions, 3xxx keyring, 4xxx tx, 5xxx network */
  code: number;
  message: string;
  data?: unknown;
}

/* ---------------------------- Commands (Requests) ---------------------------- */

export type EventTopic =
  | 'accountsChanged'
  | 'chainChanged'
  | 'newHeads'
  | 'pendingTxs'
  | 'txConfirmed'
  | 'notification';

export interface ConnectPermissions {
  /** The origin requesting access (e.g. https://app.example) */
  origin: string;
  /** Reason surfaced in approval UI */
  reason?: string;
  /** Scopes requested by the dapp */
  scopes?: ('connect' | 'sign' | 'call')[];
}

export interface ProviderRpcPayload {
  origin: string;
  method: string;
  params?: unknown;
}

/** Transaction primitives (mirrors background/tx/types.ts roughly) */
export interface TxLike {
  from: string; // bech32m address
  to?: string;
  value?: string; // decimal or hex
  data?: string;  // 0x…
  gasLimit?: string | number;
  maxFeePerGas?: string | number;
  nonce?: number;
  chainId?: number;
}

/** Simulation result subset used by UI */
export interface SimResult {
  success: boolean;
  gasUsed: number;
  returnData?: string;
  logs?: { address: string; topics: string[]; data: string }[];
  traceId?: string;
}

/** Receipt subset for UI/provider */
export interface ReceiptLike {
  txHash: string;
  status: 'success' | 'revert' | 'dropped';
  blockHash?: string;
  blockNumber?: number;
  gasUsed?: number;
  contractAddress?: string;
  logs?: { address: string; topics: string[]; data: string }[];
}

/** Background snapshot for popup state */
export interface WalletSnapshot {
  accounts: { address: string; algo: 'dilithium3' | 'sphincs+' }[];
  activeAccount?: string;
  chainId: number;
  networkName: string;
  head?: { number: number; hash: string };
}

/** Command → {req,res} mapping */
export interface MessageMap {
  /** Simple liveness check */
  PING: { req: { ts: number }; res: { ts: number } };

  /** Read current background state for the popup */
  GET_STATE: { req: {}; res: WalletSnapshot };

  /** Switch active account (by address) */
  SET_ACTIVE_ACCOUNT: { req: { address: string }; res: { ok: true } };

  /** Switch active network (by chainId) */
  SET_ACTIVE_NETWORK: { req: { chainId: number }; res: { ok: true } };

  /** Keyring lifecycle */
  KEYRING_CREATE: { req: { mnemonic?: string; algo?: 'dilithium3' | 'sphincs+'; password: string }; res: { ok: true } };
  KEYRING_IMPORT: { req: { mnemonic: string; password: string }; res: { ok: true } };
  KEYRING_EXPORT: { req: { password: string }; res: { mnemonic: string } };
  KEYRING_LOCK:   { req: {}; res: { ok: true } };
  KEYRING_UNLOCK: { req: { password: string }; res: { ok: true } };
  KEYRING_LIST:   { req: {}; res: WalletSnapshot['accounts'] };

  /** Dapp permissions & sessions */
  PERMISSIONS_CHECK:   { req: { origin: string }; res: { granted: boolean; scopes: string[] } };
  PERMISSIONS_REQUEST: { req: ConnectPermissions; res: { granted: boolean; scopes: string[] } };
  PERMISSIONS_REVOKE:  { req: { origin: string }; res: { ok: true } };

  /** Provider RPC from in-page → background (may require approval UI) */
  PROVIDER_REQUEST: { req: ProviderRpcPayload; res: unknown };

  /** Subscriptions routed via content script bridge */
  SUBSCRIBE:   { req: { origin?: string; topic: EventTopic }; res: { ok: true } };
  UNSUBSCRIBE: { req: { origin?: string; topic: EventTopic }; res: { ok: true } };

  /** Network helpers */
  HEAD_GET: { req: {}; res: { number: number; hash: string } };

  /** Tx pipeline */
  TX_SIMULATE: { req: { tx: TxLike }; res: SimResult };
  TX_SIGN:     { req: { tx: TxLike }; res: { signed: string; txHash: string } }; // signed CBOR/bytes + id
  TX_SUBMIT:   { req: { signed: string }; res: { txHash: string } };
  TX_SEND:     { req: { tx: TxLike }; res: { txHash: string } }; // convenience: sign+submit
  TX_WATCH:    { req: { txHash: string; timeoutMs?: number }; res: ReceiptLike };

  /** System notifications (background → UI) */
  NOTIFY: { req: { title: string; message: string; kind?: 'info' | 'success' | 'warning' | 'error' }; res: { ok: true } };
}

/* ----------------------- Events (push from background) ----------------------- */

export interface EventMap {
  accountsChanged: { accounts: string[] };
  chainChanged: { chainId: number; networkName: string };
  newHeads: { number: number; hash: string; parentHash: string };
  pendingTxs: { txHash: string; from: string; to?: string; value?: string };
  txConfirmed: ReceiptLike;
  notification: { title: string; message: string; kind?: 'info' | 'success' | 'warning' | 'error' };
}

/* --------------------------- Transport Message Envs -------------------------- */

export type Command = keyof MessageMap;
export type EventName = keyof EventMap;

/** Request envelope */
export interface Req<T extends Command = Command> {
  id: string;
  cmd: T;
  source: BusSide;
  /** Optional origin for dapp-initiated flows */
  origin?: string;
  params: MessageMap[T]['req'];
}

/** Success response envelope */
export interface ResOk<T extends Command = Command> {
  id: string;
  cmd: T;
  ok: true;
  result: MessageMap[T]['res'];
}

/** Error response envelope */
export interface ResErr<T extends Command = Command> {
  id: string;
  cmd: T;
  ok: false;
  error: BusError;
}

export type Res<T extends Command = Command> = ResOk<T> | ResErr<T>;

/** Push event envelope (background → content/UI) */
export interface Push<E extends EventName = EventName> {
  kind: 'event';
  topic: E;
  payload: EventMap[E];
}

/* --------------------------------- Helpers --------------------------------- */

export type RequestOf<T extends Command> = MessageMap[T]['req'];
export type ResponseOf<T extends Command> = MessageMap[T]['res'];
export type EventPayloadOf<E extends EventName> = EventMap[E];

/** Type guard for error responses */
export function isResError<T extends Command>(r: Res<T>): r is ResErr<T> {
  return (r as ResErr<T>).ok === false;
}

/** Narrow a push by topic string at runtime */
export function isPushTopic<E extends EventName>(
  msg: Push<EventName>,
  topic: E
): msg is Push<E> {
  return msg.kind === 'event' && msg.topic === topic;
}
