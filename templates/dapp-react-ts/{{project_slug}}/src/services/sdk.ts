/**
 * sdk.ts — tiny client helpers for Animica RPC and wallet-provider flows.
 *
 * This file intentionally avoids hard dependency on @animica/sdk so the
 * scaffold works out-of-the-box. If @animica/sdk is present, we can bridge to
 * it via dynamic import in the future; for now we stick to minimal JSON-RPC.
 *
 * Pairs with ./provider.ts which exposes:
 *   - httpRpc(method, params, rpcUrl?)
 *   - getAnimicaProvider(), installAnimicaShim()
 *   - onNewHeads(cb)
 */

import { httpRpc, getAnimicaProvider, onNewHeads } from "./provider";

/* ---------------------------------- Types ---------------------------------- */

export type Hex = `0x${string}`;

export type Head = {
  number: number;        // block height
  hash: Hex;             // 0x…32-byte
  timestamp?: number;    // optional, seconds since epoch
};

export type TxHash = Hex;

export type Address = string; // bech32m "anim1…" (or hex if your node is configured so)

export type Balance = {
  address: Address;
  balance: string;       // decimal string (small units)
};

export type ReceiptStatus = "SUCCESS" | "REVERT" | "OOG";

export type LogEvent = {
  address: Address;
  topics: Hex[];         // topic hashes (ABI-dependent)
  data: Hex;             // raw event payload
};

export type Receipt = {
  transactionHash: TxHash;
  status: ReceiptStatus;
  gasUsed: number;
  logs: LogEvent[];
  blockNumber?: number;
  blockHash?: Hex;
};

export interface AnimicaClient {
  /** Raw JSON-RPC call. Rarely needed by app code directly. */
  rpc<T = unknown>(method: string, params?: unknown): Promise<T>;

  /** Chain/head */
  getHead(): Promise<Head>;
  getBlockByNumber(n: number, includeTxs?: boolean): Promise<unknown>;

  /** Accounts/state */
  getBalance(address: Address): Promise<Balance>;
  getNonce(address: Address): Promise<number>;

  /** Transaction flow */
  sendRawTransaction(rawTxCborHex: Hex): Promise<TxHash>;
  waitForReceipt(hash: TxHash, opts?: { timeoutMs?: number; pollMs?: number }): Promise<Receipt | null>;

  /** Subscriptions (newHeads) with an unsubscribe handle */
  subscribeHeads(cb: (h: Head) => void): () => void;
}

/* -------------------------------- Utilities -------------------------------- */

function ensureHex(input: string, label: string): Hex {
  if (!/^0x[0-9a-fA-F]*$/.test(input)) {
    throw new Error(`${label} must be hex-prefixed with 0x`);
  }
  return input as Hex;
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/* ------------------------------- Client Factory ---------------------------- */

export function createClient(opts?: { rpcUrl?: string }): AnimicaClient {
  const rpc = async <T = unknown>(method: string, params?: unknown) =>
    httpRpc<T>(method, params, opts?.rpcUrl);

  return {
    rpc,

    /* --------------------------------- Chain -------------------------------- */
    async getHead(): Promise<Head> {
      return rpc<Head>("chain.getHead");
    },

    async getBlockByNumber(n: number, includeTxs = false): Promise<unknown> {
      return rpc("chain.getBlockByNumber", [{ number: n, includeTransactions: includeTxs }]);
    },

    /* -------------------------------- Accounts ------------------------------- */
    async getBalance(address: Address): Promise<Balance> {
      const addr = String(address);
      const balance = await rpc<string>("state.getBalance", [addr]);
      return { address: addr, balance };
    },

    async getNonce(address: Address): Promise<number> {
      const addr = String(address);
      const nonce = await rpc<number>("state.getNonce", [addr]);
      return Number(nonce);
    },

    /* ----------------------------- Transaction I/O --------------------------- */
    async sendRawTransaction(rawTxCborHex: Hex): Promise<TxHash> {
      const raw = ensureHex(rawTxCborHex, "rawTxCborHex");
      // Node RPC expects hex CBOR bytes for the signed Tx envelope
      const hash = await rpc<TxHash>("tx.sendRawTransaction", [raw]);
      return ensureHex(hash, "txHash");
    },

    async waitForReceipt(
      hash: TxHash,
      opts?: { timeoutMs?: number; pollMs?: number }
    ): Promise<Receipt | null> {
      const txHash = ensureHex(hash, "txHash");
      const timeoutMs = Math.max(1000, Math.floor(opts?.timeoutMs ?? 60_000));
      const pollMs = Math.max(500, Math.floor(opts?.pollMs ?? 2_000));

      const started = Date.now();
      while (Date.now() - started < timeoutMs) {
        const r = await rpc<Receipt | null>("tx.getTransactionReceipt", [txHash]).catch(() => null);
        if (r && r.status) return r;
        await sleep(pollMs);
      }
      return null;
    },

    /* ------------------------------ Subscriptions ---------------------------- */
    subscribeHeads(cb: (h: Head) => void): () => void {
      // Prefer provider-driven stream (shim will poll under the hood)
      const p = getAnimicaProvider();
      return onNewHeads((h) => cb(h), p);
    },
  };
}

/* --------------------------- High-level conveniences ----------------------- */

/**
 * transferViaWallet — builds a minimal transfer transaction skeleton, asks the
 * injected wallet to sign+submit, and returns { hash, receipt } once mined.
 *
 * This uses a generic AIP-1193-style custom method name:
 *   animica_sendTransaction
 *
 * Wallets (like the Animica extension) are expected to:
 *  - fill in gas / nonce
 *  - domain-separate and sign with PQ keys
 *  - submit via node RPC
 *  - return transaction hash
 *
 * If the provider doesn’t support it, this function throws.
 */
export async function transferViaWallet(args: {
  from?: Address;          // optional (wallet default)
  to: Address;
  value: string;           // decimal in smallest units
  memo?: string;           // optional bytes hex or UTF-8 (wallet may encode)
  chainId?: number;        // optional chain id hint
  waitFor?: { timeoutMs?: number; pollMs?: number }; // optional wait settings
  rpcUrl?: string;         // override RPC for receipt checks
}) {
  const provider = getAnimicaProvider();
  const client = createClient({ rpcUrl: args.rpcUrl });

  // Ask the wallet to sign & send. The exact shape is wallet-defined; we keep it simple.
  const txHash = await provider.request<TxHash>({
    method: "animica_sendTransaction",
    params: [
      {
        from: args.from,
        to: args.to,
        value: String(args.value),
        memo: args.memo,
        chainId: args.chainId,
      },
    ],
  });

  const receipt = await client.waitForReceipt(txHash, args.waitFor);
  return { hash: txHash, receipt };
}

/**
 * callContract — convenience wrapper to call a read-only contract method via RPC.
 * Requires your node to expose a simulation/call entrypoint (many do).
 *
 * If your node does not offer simulation, you can route via studio-services'
 * /simulate endpoint from app code instead of this helper.
 */
export async function callContract<T = unknown>(args: {
  address: Address;
  abi: unknown;               // JSON ABI (functions/events/errors)
  method: string;             // function name
  params?: unknown[] | object;
  rpcUrl?: string;
}): Promise<T> {
  // Many nodes offer a "state.call" or "simulate.call". We try "state.call" first.
  try {
    return await httpRpc<T>("state.call", [{ to: args.address, abi: args.abi, method: args.method, params: args.params }], args.rpcUrl);
  } catch (e) {
    // Fallback to "simulate.call" if available
    return await httpRpc<T>("simulate.call", [{ to: args.address, abi: args.abi, method: args.method, params: args.params }], args.rpcUrl);
  }
}

/**
 * estimateFees — optional helper that asks the node (or services) for fee estimates.
 * If unsupported, returns null gracefully.
 */
export async function estimateFees(args: {
  txKind: "transfer" | "deploy" | "call";
  payload: unknown;        // node-specific; e.g., { to, value } or ABI call
  rpcUrl?: string;
}): Promise<{ baseFee: string; tipSuggestion: string } | null> {
  try {
    return await httpRpc<{ baseFee: string; tipSuggestion: string }>("tx.estimateFees", [args.txKind, args.payload], args.rpcUrl);
  } catch {
    return null;
  }
}
