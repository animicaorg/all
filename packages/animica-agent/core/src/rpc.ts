/**
 * JSON-RPC client for the Animica node.
 *
 * Uses globalThis.fetch (Node 18+). All bigint values are emitted as 0x-hex on
 * the wire (Animica convention). Responses are returned verbatim; the caller
 * decides how to interpret hex-encoded fields.
 *
 * This adapter is intentionally minimal — it does NOT auto-decode block /
 * transaction structures. That belongs in higher-level helpers so the agent
 * core stays usable for both raw and typed call sites.
 */

import { RpcError } from "./errors.js";
import { safeStringify, toBigInt } from "./safe-json.js";

export interface RpcRequest {
  method: string;
  params?: unknown[];
  id?: number | string;
  timeoutMs?: number;
}

export interface RpcSuccess<T = unknown> {
  jsonrpc: "2.0";
  id: number | string;
  result: T;
}

export interface RpcFailure {
  jsonrpc: "2.0";
  id: number | string;
  error: { code: number; message: string; data?: unknown };
}

export type RpcResponse<T = unknown> = RpcSuccess<T> | RpcFailure;

export interface RpcClientOptions {
  url: string;
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
}

let counter = 0;

export class RpcClient {
  private readonly url: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(opts: RpcClientOptions) {
    if (!opts.url) throw new RpcError("CFG", "RPC url is required");
    this.url = opts.url;
    this.timeoutMs = opts.timeoutMs ?? 15_000;
    this.fetchImpl = opts.fetchImpl ?? globalThis.fetch;
    if (typeof this.fetchImpl !== "function") {
      throw new RpcError(
        "ENV",
        "global fetch is unavailable; Node 18.17+ is required or pass fetchImpl",
      );
    }
  }

  async call<T = unknown>(req: RpcRequest): Promise<T> {
    const id = req.id ?? ++counter;
    const body = safeStringify({ jsonrpc: "2.0", id, method: req.method, params: req.params ?? [] }, { hex: true });
    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), req.timeoutMs ?? this.timeoutMs);
    let res: Response;
    try {
      res = await this.fetchImpl(this.url, {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body,
        signal: controller.signal,
      });
    } catch (err) {
      throw new RpcError("NETWORK", `RPC ${req.method} failed: ${(err as Error).message}`);
    } finally {
      clearTimeout(t);
    }
    if (!res.ok) {
      const text = await safeText(res);
      throw new RpcError(res.status, `RPC ${req.method} HTTP ${res.status}: ${text}`);
    }
    let parsed: RpcResponse<T>;
    try {
      parsed = (await res.json()) as RpcResponse<T>;
    } catch (err) {
      throw new RpcError("DECODE", `RPC ${req.method} bad JSON: ${(err as Error).message}`);
    }
    if ("error" in parsed) {
      throw new RpcError(parsed.error.code, parsed.error.message, parsed.error.data);
    }
    return parsed.result;
  }

  // ---- Convenience typed helpers used by status/doctor flows ----

  async chainId(): Promise<bigint | null> {
    const r = await tryCall<string>(this, "animica_chainId");
    if (r === undefined) {
      const r2 = await tryCall<string>(this, "eth_chainId");
      if (r2 === undefined) return null;
      return toBigInt(r2);
    }
    return toBigInt(r);
  }

  async blockNumber(): Promise<bigint | null> {
    const r = await tryCall<string>(this, "animica_blockNumber");
    if (r === undefined) {
      const r2 = await tryCall<string>(this, "eth_blockNumber");
      if (r2 === undefined) return null;
      return toBigInt(r2);
    }
    return toBigInt(r);
  }

  async syncing(): Promise<unknown> {
    const r = await tryCall<unknown>(this, "animica_syncing");
    if (r !== undefined) return r;
    return tryCall<unknown>(this, "eth_syncing");
  }

  async clientVersion(): Promise<string | null> {
    const r = await tryCall<string>(this, "animica_clientVersion");
    if (typeof r === "string") return r;
    const r2 = await tryCall<string>(this, "web3_clientVersion");
    return typeof r2 === "string" ? r2 : null;
  }

  async ping(): Promise<boolean> {
    const r = await tryCall<unknown>(this, "animica_chainId");
    return r !== undefined;
  }
}

async function tryCall<T>(client: RpcClient, method: string, params: unknown[] = []): Promise<T | undefined> {
  try {
    return await client.call<T>({ method, params });
  } catch (err) {
    if (err instanceof RpcError) {
      // Methods that don't exist surface as RPC errors; swallow only the
      // method-not-found case so probing remains cheap.
      const code = err.code;
      if (code === "RPC_-32601" || code === "RPC_404" || code === "RPC_NETWORK") return undefined;
    }
    throw err;
  }
}

async function safeText(res: Response): Promise<string> {
  try {
    return (await res.text()).slice(0, 512);
  } catch {
    return "<unreadable body>";
  }
}

/**
 * Animica bech32-like address validator.
 *
 * We deliberately keep this conservative: addresses must start with one of the
 * known Animica prefixes ("anm", "animica", or "anmt" for testnet), be all
 * lowercase, and use the bech32 charset. We do NOT validate the checksum here
 * — full bech32 decoding lives in @animica/sdk and would couple the agent to
 * native bindings. Use isLikelyAnimicaAddress in CLI flows and call the SDK
 * when the user is about to perform a chain action.
 */
const BECH32_CHARSET = /^[02-9ac-hj-np-z]+$/;
const ADDRESS_PREFIXES = ["anm1", "animica1", "anmt1"];

export function isLikelyAnimicaAddress(address: string): boolean {
  if (typeof address !== "string") return false;
  const a = address.trim();
  if (a !== a.toLowerCase()) return false;
  if (a.length < 12 || a.length > 110) return false;
  const prefix = ADDRESS_PREFIXES.find((p) => a.startsWith(p));
  if (!prefix) return false;
  const payload = a.slice(prefix.length);
  return BECH32_CHARSET.test(payload);
}

export interface NodeStatus {
  reachable: boolean;
  chainId: bigint | null;
  blockNumber: bigint | null;
  syncing: unknown;
  clientVersion: string | null;
  error?: string;
}

export async function probeNode(rpcUrl: string, timeoutMs = 4000): Promise<NodeStatus> {
  const client = new RpcClient({ url: rpcUrl, timeoutMs });
  try {
    const [cid, bn, sync, cv] = await Promise.all([
      client.chainId().catch(() => null),
      client.blockNumber().catch(() => null),
      client.syncing().catch(() => null),
      client.clientVersion().catch(() => null),
    ]);
    return {
      reachable: cid !== null || bn !== null || cv !== null,
      chainId: cid,
      blockNumber: bn,
      syncing: sync,
      clientVersion: cv,
    };
  } catch (err) {
    return {
      reachable: false,
      chainId: null,
      blockNumber: null,
      syncing: null,
      clientVersion: null,
      error: (err as Error).message,
    };
  }
}
