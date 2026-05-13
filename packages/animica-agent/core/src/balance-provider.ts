/**
 * Signer balance provider.
 *
 * Fetches the signer's on-chain ANM balance from the configured Animica RPC.
 * Used by the payout policy guard so reserve-balance enforcement works
 * without callers having to manually thread `signerBalance` through every
 * payout decision.
 *
 * Two safety properties:
 *
 *  - Fail closed. A missing config field, a failed RPC, a chain-id mismatch,
 *    or a malformed balance reply all result in an explicit failure object
 *    whose `failureReason` matches the payout-policy rejection vocabulary.
 *    The runtime treats any non-`ok` lookup as a refusal — it never spends.
 *
 *  - Caching with a hard TTL. Repeated payouts inside the TTL window reuse
 *    the cached result so we don't spam RPC, but the TTL is short (default
 *    10s) and any failed lookup is never cached. Operators tightening
 *    `reserveBalanceRaw` will see the new gate applied within at most one
 *    TTL window.
 *
 * All BigInts are preserved end-to-end via safe-json.toBigInt; no number
 * truncation ever happens on the balance path.
 */

import { isLikelyAnimicaAddress, RpcClient } from "./rpc.js";
import { toBigInt } from "./safe-json.js";
import { formatANM, type WalletBalance } from "./wallet.js";

export type BalanceLookupFailure =
  | "signer-address-missing"
  | "signer-address-invalid"
  | "rpc-url-missing"
  | "rpc-unavailable"
  | "chain-id-mismatch"
  | "balance-malformed"
  | "unknown";

export interface BalanceLookupOk {
  ok: true;
  /** Wallet balance details (BigInt-safe). */
  balance: WalletBalance;
  /** Chain id reported by the RPC at lookup time, as a string for parity with config. */
  observedChainId: string;
  /** ISO timestamp when the lookup completed. */
  fetchedAt: string;
  /** True if served from cache. */
  cached: boolean;
}

export interface BalanceLookupErr {
  ok: false;
  failureReason: BalanceLookupFailure;
  message: string;
  /** Best-effort error detail (truncated). */
  detail?: string;
  fetchedAt: string;
}

export type BalanceLookup = BalanceLookupOk | BalanceLookupErr;

export interface BalanceProviderConfig {
  rpcUrl: string;
  /** Expected chain id (string for parity with AgentConfig.chainId). */
  expectedChainId: string;
  /** TTL for cached successful lookups in ms. Defaults to 10_000. */
  cacheTtlMs?: number;
  /** Per-request timeout in ms. Defaults to 4_000. */
  timeoutMs?: number;
  /** Optional injection point for tests. */
  fetchImpl?: typeof fetch;
  /** Clock injection point for tests. */
  now?: () => number;
}

/**
 * Pluggable interface so callers can supply a fixture provider in tests
 * without monkey-patching.
 */
export interface BalanceProvider {
  /** Look up the balance for `address`. Returns a discriminated union. */
  lookup(address: string): Promise<BalanceLookup>;
  /** Invalidate any cached state (e.g. after a confirmed payout). */
  invalidate(): void;
}

interface CacheEntry {
  at: number;
  ok: BalanceLookupOk;
}

export class RpcBalanceProvider implements BalanceProvider {
  private readonly cfg: Required<Omit<BalanceProviderConfig, "fetchImpl" | "now">> & {
    fetchImpl?: typeof fetch;
    now: () => number;
  };
  private cache: Map<string, CacheEntry> = new Map();

  constructor(cfg: BalanceProviderConfig) {
    this.cfg = {
      rpcUrl: cfg.rpcUrl,
      expectedChainId: cfg.expectedChainId,
      cacheTtlMs: cfg.cacheTtlMs ?? 10_000,
      timeoutMs: cfg.timeoutMs ?? 4_000,
      fetchImpl: cfg.fetchImpl,
      now: cfg.now ?? (() => Date.now()),
    };
  }

  invalidate(): void {
    this.cache.clear();
  }

  async lookup(address: string): Promise<BalanceLookup> {
    const fetchedAt = new Date(this.cfg.now()).toISOString();
    if (!this.cfg.rpcUrl) {
      return {
        ok: false,
        failureReason: "rpc-url-missing",
        message: "no RPC URL configured",
        fetchedAt,
      };
    }
    if (!this.cfg.expectedChainId) {
      return {
        ok: false,
        failureReason: "chain-id-mismatch",
        message: "no expected chain id configured",
        fetchedAt,
      };
    }
    if (!address || typeof address !== "string") {
      return {
        ok: false,
        failureReason: "signer-address-missing",
        message: "signer address is empty",
        fetchedAt,
      };
    }
    if (!isLikelyAnimicaAddress(address)) {
      return {
        ok: false,
        failureReason: "signer-address-invalid",
        message: `signer address fails Animica prefix/charset check: ${address}`,
        fetchedAt,
      };
    }

    // Cache hit?
    const cached = this.cache.get(address);
    if (cached && this.cfg.now() - cached.at < this.cfg.cacheTtlMs) {
      return { ...cached.ok, cached: true };
    }

    // Verify chain id via the same RpcClient (we pass through the same
    // fetchImpl so tests can shim a single transport). Animica clients may
    // serve eth_getBalance on every chain, so we must compare chain id
    // ourselves before trusting the balance.
    const client = new RpcClient({
      url: this.cfg.rpcUrl,
      timeoutMs: this.cfg.timeoutMs,
      fetchImpl: this.cfg.fetchImpl,
    });
    let observedChainId: string;
    try {
      let chainHex: string | undefined;
      try {
        chainHex = await client.call<string>({ method: "animica_chainId" });
      } catch (errInner) {
        // The inner RpcClient throws RpcError with codes RPC_NETWORK (transport
        // failure) or RPC_-32601 (method missing). Only the missing-method case
        // is recoverable by trying eth_chainId; transport errors must surface.
        const code = (errInner as { code?: string | number }).code;
        if (code === "RPC_-32601" || code === "RPC_404") {
          chainHex = await client.call<string>({ method: "eth_chainId" });
        } else {
          throw errInner;
        }
      }
      if (typeof chainHex !== "string" || chainHex.length === 0) {
        return {
          ok: false,
          failureReason: "chain-id-mismatch",
          message: `RPC at ${this.cfg.rpcUrl} did not return a chain id`,
          fetchedAt,
        };
      }
      observedChainId = toBigInt(chainHex).toString();
    } catch (err) {
      const code = (err as { code?: string | number }).code;
      if (code === "RPC_-32601" || code === "RPC_404") {
        return {
          ok: false,
          failureReason: "chain-id-mismatch",
          message: `RPC does not implement chain id methods`,
          fetchedAt,
        };
      }
      return {
        ok: false,
        failureReason: "rpc-unavailable",
        message: `chain id RPC failed: ${(err as Error).message.slice(0, 200)}`,
        detail: (err as Error).message?.slice(0, 200),
        fetchedAt,
      };
    }
    if (observedChainId !== this.cfg.expectedChainId) {
      return {
        ok: false,
        failureReason: "chain-id-mismatch",
        message: `expected chainId=${this.cfg.expectedChainId} but RPC reports ${observedChainId}`,
        fetchedAt,
      };
    }

    // Now actually fetch the balance.
    let rawHex: string;
    try {
      try {
        rawHex = await client.call<string>({
          method: "animica_getBalance",
          params: [address, "latest"],
        });
      } catch {
        rawHex = await client.call<string>({
          method: "eth_getBalance",
          params: [address, "latest"],
        });
      }
    } catch (err) {
      return {
        ok: false,
        failureReason: "rpc-unavailable",
        message: `balance RPC failed: ${(err as Error).message.slice(0, 200)}`,
        detail: (err as Error).message?.slice(0, 200),
        fetchedAt,
      };
    }
    if (typeof rawHex !== "string" || rawHex.length === 0) {
      return {
        ok: false,
        failureReason: "balance-malformed",
        message: `balance RPC returned non-string body (${typeof rawHex})`,
        fetchedAt,
      };
    }
    let raw: bigint;
    try {
      raw = toBigInt(rawHex);
    } catch (err) {
      return {
        ok: false,
        failureReason: "balance-malformed",
        message: `balance RPC returned unparseable bigint: ${rawHex.slice(0, 64)}`,
        detail: (err as Error).message?.slice(0, 200),
        fetchedAt,
      };
    }
    if (raw < 0n) {
      return {
        ok: false,
        failureReason: "balance-malformed",
        message: `balance RPC returned negative balance ${raw.toString()}`,
        fetchedAt,
      };
    }
    const balance: WalletBalance = {
      address,
      raw,
      decimal: raw.toString(10),
      formattedANM: formatANM(raw),
      reachable: true,
    };
    const okEntry: BalanceLookupOk = {
      ok: true,
      balance,
      observedChainId,
      fetchedAt,
      cached: false,
    };
    this.cache.set(address, { at: this.cfg.now(), ok: okEntry });
    return okEntry;
  }
}

/**
 * Map a `BalanceLookupFailure` to the equivalent `PayoutRejectionReason`.
 * The payout-policy module imports this so a balance lookup that fails
 * closed still produces an audited, operator-actionable rejection.
 */
export function balanceFailureToPayoutReason(
  failure: BalanceLookupFailure,
):
  | "reserve-balance-violation"
  | "config-missing"
  | "tampered-attempt"
  | "unknown" {
  switch (failure) {
    case "signer-address-missing":
    case "rpc-url-missing":
      return "config-missing";
    case "signer-address-invalid":
    case "chain-id-mismatch":
      return "tampered-attempt";
    case "balance-malformed":
      return "tampered-attempt";
    case "rpc-unavailable":
      // Treat as reserve-balance-violation so the runtime refuses to spend
      // when it cannot prove safety. Operators see the verbose `message`.
      return "reserve-balance-violation";
    default:
      return "unknown";
  }
}
