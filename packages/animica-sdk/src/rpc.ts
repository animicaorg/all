/**
 * JsonRpcClient — typed JSON-RPC 2.0 client for Animica nodes.
 *
 * Method names mirror the node's registered dot-style names
 * (rpc/methods/*.py in the animicaorg/all monorepo; discoverable live via
 * `rpc.discover`): chain.getHead, chain.getParams, state.getTotalSupply,
 * state.getBalance, chain.getBlockByHeight, chain.getBlockByHash,
 * tx.sendRawTransaction, mempool.getStats, ...
 *
 * All params are sent positionally. Anything not covered by a typed helper
 * can be reached through `call(method, params)`.
 */

import {
  FetchLike,
  HttpOptions,
  fetchWithTimeout,
  resolveFetch,
} from "./http.js";

export const DEFAULT_RPC_URL = "https://rpc.animica.org/rpc";

// ---------------------------------------------------------------------------
// Types (shaped after live node responses, chainId 1, Aug 2026)
// ---------------------------------------------------------------------------

/** 0x-prefixed hex string (hashes, roots, raw CBOR tx bytes, quantities). */
export type Hex = `0x${string}`;

export interface BlockRoots {
  stateRoot: Hex;
  txsRoot: Hex;
  receiptsRoot: Hex;
  proofsRoot: Hex;
  daRoot: Hex;
  [key: string]: unknown;
}

export interface Head {
  height: number;
  number: number;
  hash: Hex;
  chainId: number;
  /** PoIES acceptance threshold Θ in micro-nats — the chain's difficulty field. */
  thetaMicro: number;
  mixSeed: Hex;
  nonce: number;
  roots: BlockRoots;
  [key: string]: unknown;
}

export interface Block {
  number: number;
  hash: Hex;
  parentHash: Hex;
  timestamp: number;
  chainId: number;
  thetaMicro: number;
  mixSeed: Hex;
  nonce: number;
  roots: BlockRoots;
  /** Tx hashes, or tx objects when includeTxObjects=true. */
  transactions: unknown[];
  txs: unknown[];
  header?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ChainParams {
  symbol?: string;
  decimals?: number;
  genesis_time_utc?: string;
  system_addresses?: Record<string, string>;
  [key: string]: unknown;
}

export interface TotalSupply {
  /** Total supply in base units (nANM), 0x hex quantity. */
  totalSupply: Hex;
  /** Number of accounts with non-zero balance. */
  addressCount: number;
  [key: string]: unknown;
}

export interface MempoolStats {
  count: number;
  totalBytes: number;
  oldestAgeSec: number | null;
  [key: string]: unknown;
}

export interface NetworkHashrate {
  [key: string]: unknown;
}

export interface JsonRpcErrorShape {
  code: number;
  message: string;
  data?: unknown;
}

/** Error raised when the node returns a JSON-RPC error object. */
export class RpcError extends Error {
  readonly code: number;
  readonly data?: unknown;
  readonly method: string;

  constructor(method: string, err: JsonRpcErrorShape) {
    super(`RPC ${method} failed: ${err.message} (code ${err.code})`);
    this.name = "RpcError";
    this.code = err.code;
    this.data = err.data;
    this.method = method;
  }
}

/** Error raised on transport-level failures (non-2xx HTTP, bad envelope). */
export class RpcTransportError extends Error {
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "RpcTransportError";
    this.status = status;
  }
}

export interface JsonRpcClientOptions extends HttpOptions {
  url?: string;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export class JsonRpcClient {
  readonly url: string;
  private readonly fetchImpl: FetchLike;
  private readonly timeoutMs: number;
  private readonly headers: Record<string, string>;
  private nextId = 1;

  constructor(urlOrOptions?: string | JsonRpcClientOptions) {
    const opts: JsonRpcClientOptions =
      typeof urlOrOptions === "string"
        ? { url: urlOrOptions }
        : urlOrOptions ?? {};
    this.url = opts.url ?? DEFAULT_RPC_URL;
    this.fetchImpl = resolveFetch(opts.fetch);
    this.timeoutMs = opts.timeoutMs ?? 30_000;
    this.headers = opts.headers ?? {};
  }

  /** Generic JSON-RPC call with positional params. */
  async call<T = unknown>(method: string, params: unknown[] = []): Promise<T> {
    const id = this.nextId++;
    const res = await fetchWithTimeout(
      this.fetchImpl,
      this.url,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "application/json",
          ...this.headers,
        },
        body: JSON.stringify({ jsonrpc: "2.0", id, method, params }),
      },
      this.timeoutMs
    );
    if (!res.ok) {
      throw new RpcTransportError(
        `HTTP ${res.status} from ${this.url}`,
        res.status
      );
    }
    let envelope: {
      jsonrpc?: string;
      id?: unknown;
      result?: T;
      error?: JsonRpcErrorShape;
    };
    try {
      envelope = (await res.json()) as typeof envelope;
    } catch (e) {
      throw new RpcTransportError(
        `Invalid JSON-RPC response from ${this.url}: ${(e as Error).message}`
      );
    }
    if (envelope.error) {
      throw new RpcError(method, envelope.error);
    }
    if (!("result" in envelope)) {
      throw new RpcTransportError(
        `JSON-RPC envelope missing both result and error (method ${method})`
      );
    }
    return envelope.result as T;
  }

  // --- chain ---------------------------------------------------------------

  /** Current canonical head (height, hash, thetaMicro difficulty, roots). */
  getHead(): Promise<Head> {
    return this.call<Head>("chain.getHead");
  }

  /** Canonical chain parameters (symbol ANM, decimals 9, system addresses). */
  getParams(): Promise<ChainParams> {
    return this.call<ChainParams>("chain.getParams");
  }

  /** Numeric chain id (mainnet = 1). */
  getChainId(): Promise<number> {
    return this.call<number>("chain.getChainId");
  }

  /** Block by height. Returns null when the height is beyond the head. */
  getBlockByHeight(
    height: number,
    includeTxObjects = false
  ): Promise<Block | null> {
    return this.call<Block | null>("chain.getBlockByHeight", [
      height,
      includeTxObjects,
    ]);
  }

  /** Block by 0x-prefixed hash. Returns null when unknown. */
  getBlockByHash(
    hash: string,
    includeTxObjects = false
  ): Promise<Block | null> {
    return this.call<Block | null>("chain.getBlockByHash", [
      hash,
      includeTxObjects,
    ]);
  }

  /** Network hashrate estimate derived from thetaMicro over recent blocks. */
  getNetworkHashrate(): Promise<NetworkHashrate> {
    return this.call<NetworkHashrate>("chain.getNetworkHashrate");
  }

  // --- state ---------------------------------------------------------------

  /** Total supply in base units (hex) + count of non-zero accounts. */
  getTotalSupply(): Promise<TotalSupply> {
    return this.call<TotalSupply>("state.getTotalSupply");
  }

  /** Balance of a bech32m anim1… address, in base units (nANM) as bigint. */
  async getBalance(address: string, tag = "latest"): Promise<bigint> {
    const hex = await this.call<string>("state.getBalance", [address, tag]);
    return BigInt(hex);
  }

  /** Raw hex-quantity balance as returned by the node. */
  getBalanceHex(address: string, tag = "latest"): Promise<Hex> {
    return this.call<Hex>("state.getBalance", [address, tag]);
  }

  /** Full account view (balance/nonce/…) for an address. */
  getAccount(address: string): Promise<Record<string, unknown>> {
    return this.call<Record<string, unknown>>("state.getAccount", [address]);
  }

  // --- tx ------------------------------------------------------------------

  /**
   * Submit a signed CBOR transaction as 0x-prefixed hex (opaque passthrough —
   * signing is out of scope for this SDK; see the wallet docs).
   * Resolves to the tx hash.
   */
  sendRawTransaction(rawTxHex: string): Promise<Hex> {
    return this.call<Hex>("tx.sendRawTransaction", [rawTxHex]);
  }

  /** Transaction lookup by hash (null when unknown). */
  getTransaction(hash: string): Promise<Record<string, unknown> | null> {
    return this.call<Record<string, unknown> | null>(
      "tx.getTransactionByHash",
      [hash]
    );
  }

  /** Receipt lookup by tx hash (null when not yet executed). */
  getTransactionReceipt(hash: string): Promise<Record<string, unknown> | null> {
    return this.call<Record<string, unknown> | null>(
      "tx.getTransactionReceipt",
      [hash]
    );
  }

  // --- mempool -------------------------------------------------------------

  /** Pending-pool summary: tx count / bytes / oldest age. */
  getMempoolStats(): Promise<MempoolStats> {
    return this.call<MempoolStats>("mempool.getStats");
  }

  /** Whether a given tx hash is currently known to the mempool. */
  getMempoolStatus(txHash: string): Promise<Record<string, unknown>> {
    return this.call<Record<string, unknown>>("mempool.getStatus", [txHash]);
  }
}
