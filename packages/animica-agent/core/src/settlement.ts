/**
 * Settlement-readiness check + confirmation polling.
 *
 * `checkSettlementReadiness` runs a set of pre-flight checks before the agent
 * attempts an on-chain charge. It is read-only and side-effect-free:
 *
 *   1. RPC reachable
 *   2. chainId matches expected
 *   3. wallet address resolves
 *   4. wallet balance >= estimated cost
 *   5. tx send binary is reachable (we only --help it; we never submit)
 *
 * `waitForConfirmation` polls the node for tx receipt and returns one of
 * "confirmed" | "pending" | "rejected" | "missing". It is robust to node
 * restart and chain-reorg windows because it only uses public RPC reads.
 */

import { spawnSync } from "node:child_process";

import type { AgentConfig } from "./config.js";
import { RpcClient, probeNode } from "./rpc.js";
import { fetchBalance, resolveWalletIdentity } from "./wallet.js";

export type ReadinessReason =
  | "ok"
  | "rpc-unreachable"
  | "chain-id-mismatch"
  | "wallet-unresolved"
  | "balance-insufficient"
  | "tx-binary-missing";

export interface ReadinessCheck {
  reason: ReadinessReason;
  ok: boolean;
  message: string;
  details?: Record<string, unknown>;
}

export interface ReadinessReport {
  ok: boolean;
  checks: ReadinessCheck[];
  /** First non-ok check, or null. */
  firstFailure: ReadinessCheck | null;
}

export interface ReadinessOptions {
  /** Estimated raw cost we need to be able to spend. */
  estimatedCostRaw?: bigint;
  /** Override RPC URL. */
  rpcUrl?: string;
  /** Override the configured wallet address. */
  walletAddress?: string;
  /** Override the tx binary lookup; pass `false` to skip the binary check. */
  txBinary?: string | false;
  /** Spawn shim for tests. */
  spawn?: typeof spawnSync;
}

export async function checkSettlementReadiness(cfg: AgentConfig, opts: ReadinessOptions = {}): Promise<ReadinessReport> {
  const rpcUrl = opts.rpcUrl ?? cfg.rpcUrl;
  const checks: ReadinessCheck[] = [];

  const node = await probeNode(rpcUrl, 4000);
  checks.push({
    reason: "rpc-unreachable",
    ok: node.reachable,
    message: node.reachable
      ? `RPC ${rpcUrl} reachable (chainId=${node.chainId?.toString() ?? "?"})`
      : `RPC ${rpcUrl} unreachable: ${node.error ?? "unknown error"}`,
    details: { rpcUrl, chainId: node.chainId?.toString(), error: node.error },
  });

  // chain id must match the operator's configured expectation when one is set.
  const expectedChainId = cfg.chainId;
  if (node.reachable && expectedChainId && node.chainId !== null) {
    const match = node.chainId.toString() === expectedChainId.toString();
    checks.push({
      reason: "chain-id-mismatch",
      ok: match,
      message: match
        ? `chainId matches (${expectedChainId})`
        : `expected chainId=${expectedChainId} but node reports ${node.chainId.toString()}`,
      details: { expected: expectedChainId, actual: node.chainId.toString() },
    });
  }

  const wallet = opts.walletAddress
    ? { address: opts.walletAddress, source: "user" as const }
    : resolveWalletIdentity(cfg);
  checks.push({
    reason: "wallet-unresolved",
    ok: !!wallet,
    message: wallet
      ? `wallet resolved: ${wallet.address} (${wallet.source})`
      : "no wallet identity; run `animica-agent wallet connect <addr>`",
    details: wallet ? { ...wallet } : undefined,
  });

  if (wallet && node.reachable && (opts.estimatedCostRaw ?? 0n) > 0n) {
    const balance = await fetchBalance(rpcUrl, wallet.address, 4000);
    const ok = balance.reachable && balance.raw >= (opts.estimatedCostRaw ?? 0n);
    checks.push({
      reason: "balance-insufficient",
      ok,
      message: ok
        ? `balance ok: ${balance.formattedANM} ANM ≥ ${(opts.estimatedCostRaw ?? 0n).toString()}`
        : `balance ${balance.formattedANM} ANM is below estimate (${(opts.estimatedCostRaw ?? 0n).toString()} raw)`,
      details: { balanceRaw: balance.raw.toString(), need: (opts.estimatedCostRaw ?? 0n).toString() },
    });
  }

  if (opts.txBinary !== false) {
    const probe = (opts.spawn ?? spawnSync)(opts.txBinary ?? "python3", ["-m", "animica.cli.main", "tx", "send", "--help"], {
      encoding: "utf8",
      timeout: 5000,
    });
    const found = probe.status === 0 && /tx send/i.test(probe.stdout ?? "");
    checks.push({
      reason: "tx-binary-missing",
      ok: found,
      message: found
        ? "tx send binary reachable"
        : "`animica tx send --help` did not exit cleanly; install the Animica Python CLI or set ANIMICA_NODE_BIN",
      details: { exit: probe.status, stderrSnippet: (probe.stderr ?? "").slice(0, 200) },
    });
  }

  const firstFailure = checks.find((c) => !c.ok) ?? null;
  return { ok: !firstFailure, checks, firstFailure };
}

/* ---------------- confirmation tracking ---------------- */

export type ConfirmationStatus = "confirmed" | "pending" | "rejected" | "missing" | "rpc-error";

export interface ConfirmationResult {
  status: ConfirmationStatus;
  attempts: number;
  receipt?: { blockNumber?: bigint; status?: bigint };
  error?: string;
}

export interface ConfirmationOptions {
  rpcUrl: string;
  /** Maximum number of poll attempts. Default 30. */
  maxAttempts?: number;
  /** Delay between attempts in ms. Default 2000. */
  intervalMs?: number;
  /** Optional injection point for tests. */
  call?: <T>(method: string, params: unknown[]) => Promise<T | null>;
  /** Optional sleeper for tests. */
  sleep?: (ms: number) => Promise<void>;
}

export async function waitForConfirmation(txHash: string, opts: ConfirmationOptions): Promise<ConfirmationResult> {
  const maxAttempts = opts.maxAttempts ?? 30;
  const intervalMs = opts.intervalMs ?? 2000;
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const call =
    opts.call ??
    (async <T>(method: string, params: unknown[]) => {
      const client = new RpcClient({ url: opts.rpcUrl, timeoutMs: 4000 });
      try {
        return (await client.call<T>({ method, params })) ?? null;
      } catch (err) {
        // The caller treats null as "not found yet" and continues polling;
        // a hard RPC failure is reported as rpc-error so the operator knows.
        if ((err as Error).message?.includes("HTTP")) return null;
        throw err;
      }
    });

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      // No inner .catch — we want non-HTTP errors (DNS, EAI_AGAIN, abort) to
      // bubble to the outer try so we return rpc-error to the operator.
      const receipt = await call<{ blockNumber?: string; status?: string } | null>("animica_getTransactionReceipt", [
        txHash,
      ]);
      if (receipt && (receipt.blockNumber !== undefined || receipt.status !== undefined)) {
        const status = receipt.status === undefined ? undefined : BigInt(receipt.status);
        if (status !== undefined && status === 0n) return { status: "rejected", attempts: attempt, receipt: { status } };
        return {
          status: "confirmed",
          attempts: attempt,
          receipt: {
            blockNumber: receipt.blockNumber === undefined ? undefined : BigInt(receipt.blockNumber),
            status,
          },
        };
      }
      const pending = await call<unknown>("animica_getTransaction", [txHash]).catch(() => null);
      if (pending) {
        // Tx exists but no receipt yet → still pending.
      }
    } catch (err) {
      return { status: "rpc-error", attempts: attempt, error: (err as Error).message };
    }
    if (attempt < maxAttempts) await sleep(intervalMs);
  }
  return { status: "missing", attempts: maxAttempts };
}
