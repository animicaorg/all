/**
 * Operator go-live checklist.
 *
 * Aggregates the strictest set of gates that must all be `ok` before live
 * payouts are safe. Returns a structured report that:
 *   - exits non-zero on any error-level failure
 *   - includes a remediation hint per failure
 *   - never silently downgrades to `offline`
 *
 * This is the command an operator should run last, after `doctor useful-work`
 * and `coordinator verify-live`, before flipping the daemon to
 * settlementMode=live.
 */

import { accessSync, constants } from "node:fs";

import {
  type BalanceProvider,
  RpcBalanceProvider,
} from "./balance-provider.js";
import type { AgentConfig } from "./config.js";
import {
  checkCoordinatorFreshness,
  DEFAULT_FRESHNESS_WINDOW_MS,
} from "./coordinator-verify.js";
import {
  DEFAULT_PAYOUT_POLICY,
  type PayoutPolicyConfig,
} from "./payout-policy.js";
import { isLikelyAnimicaAddress, probeNode } from "./rpc.js";
import { resolveWalletIdentity } from "./wallet.js";

export interface GoLiveCheck {
  name: string;
  ok: boolean;
  level: "info" | "warning" | "error";
  message: string;
  fix?: string;
  detail?: Record<string, unknown>;
}

export interface GoLiveReport {
  ok: boolean;
  generatedAt: string;
  summary: string;
  checks: GoLiveCheck[];
}

export interface GoLiveOptions {
  stateDir: string;
  /** Optional balance provider override (tests). */
  balanceProvider?: BalanceProvider;
  /** Optional baseUrl to scope coordinator freshness. */
  coordinatorBaseUrl?: string;
  /** Freshness window in ms. Defaults to DEFAULT_FRESHNESS_WINDOW_MS. */
  coordinatorFreshnessWindowMs?: number;
  /** Effective payout policy. Defaults to DEFAULT_PAYOUT_POLICY. */
  policy?: PayoutPolicyConfig;
  /** When false, skip the network-touching RPC probe (tests). */
  network?: boolean;
  /** Optional signer-availability check (tests). Defaults to the existence of
   *  ANIMICA_AGENT_HOME / minerAddress as a proxy. */
  signerAvailable?: () => boolean;
}

export async function goLive(cfg: AgentConfig, opts: GoLiveOptions): Promise<GoLiveReport> {
  const checks: GoLiveCheck[] = [];
  const policy = { ...DEFAULT_PAYOUT_POLICY, ...(opts.policy ?? {}) };
  const network = opts.network !== false;

  // 1. Coordinator verification freshness.
  const fresh = checkCoordinatorFreshness({
    stateDir: opts.stateDir,
    baseUrl: opts.coordinatorBaseUrl,
    windowMs: opts.coordinatorFreshnessWindowMs ?? DEFAULT_FRESHNESS_WINDOW_MS,
  });
  checks.push({
    name: "coordinator-fresh",
    ok: fresh.fresh,
    level: fresh.fresh ? "info" : "error",
    message: fresh.fresh
      ? `coordinator verification is fresh (age ${(fresh.ageMs ?? 0) / 60_000}m)`
      : `coordinator verification is ${fresh.reason ?? "stale"}`,
    fix: fresh.fresh ? undefined : "run `animica-agent coordinator verify-live --url <url>` first",
    detail: { reason: fresh.reason, latestAt: fresh.latest?.generatedAt },
  });

  // 2. Signer address.
  const signerAddress = cfg.minerAddress ?? resolveWalletIdentity(cfg)?.address ?? "";
  checks.push({
    name: "wallet-signer-available",
    ok: !!signerAddress && isLikelyAnimicaAddress(signerAddress),
    level: signerAddress && isLikelyAnimicaAddress(signerAddress) ? "info" : "error",
    message: signerAddress
      ? isLikelyAnimicaAddress(signerAddress)
        ? `signer ready: ${signerAddress}`
        : `signer address fails prefix check: ${signerAddress}`
      : "no signer/payout address configured",
    fix: "set minerAddress in .animica/agent.json or run `animica-agent wallet connect <addr>`",
  });

  // 3. Reserve policy is configured.
  const reservePolicy = cfg.reservePolicy ?? (cfg.settlementMode === "live" ? "strict" : "off");
  if (cfg.settlementMode === "live" && reservePolicy === "off") {
    checks.push({
      name: "reserve-policy",
      ok: false,
      level: "error",
      message: "settlementMode=live but reservePolicy=off — reserve enforcement DISABLED",
      fix: "set reservePolicy=strict in .animica/agent.json (or omit so the default applies)",
    });
  } else {
    checks.push({
      name: "reserve-policy",
      ok: true,
      level: "info",
      message: `reservePolicy=${reservePolicy} (reserveBalanceRaw=${policy.reserveBalanceRaw})`,
    });
  }

  // 4. Settlement backend is live (not offline).
  const liveMode = cfg.settlementMode === "live";
  checks.push({
    name: "settlement-backend",
    ok: liveMode,
    level: liveMode ? "info" : "error",
    message: `settlementMode=${cfg.settlementMode ?? "offline"}`,
    fix: liveMode ? undefined : "set settlementMode=live in .animica/agent.json",
  });

  // 5. Chain RPC reachable + chain id matches.
  if (network) {
    const node = await probeNode(cfg.rpcUrl).catch((err) => ({
      reachable: false,
      chainId: null,
      blockNumber: null,
      syncing: null,
      clientVersion: null,
      error: (err as Error).message,
    }));
    checks.push({
      name: "chain-rpc",
      ok: node.reachable,
      level: node.reachable ? "info" : "error",
      message: node.reachable
        ? `RPC ${cfg.rpcUrl} reachable (chainId=${node.chainId?.toString() ?? "?"})`
        : `RPC ${cfg.rpcUrl} unreachable: ${node.error ?? "unknown"}`,
      fix: "check rpcUrl and that the node is running",
    });
    if (node.reachable && node.chainId !== null && node.chainId.toString() !== cfg.chainId) {
      checks.push({
        name: "chain-id",
        ok: false,
        level: "error",
        message: `expected chainId=${cfg.chainId} but node reports ${node.chainId.toString()}`,
        fix: "align chainId in .animica/agent.json with the actual network",
      });
    }
  }

  // 6. Signer balance is sufficient (above reserve).
  if (signerAddress && isLikelyAnimicaAddress(signerAddress)) {
    const provider =
      opts.balanceProvider ??
      new RpcBalanceProvider({
        rpcUrl: cfg.rpcUrl,
        expectedChainId: cfg.chainId,
      });
    const lookup = await provider.lookup(signerAddress);
    if (!lookup.ok) {
      checks.push({
        name: "signer-balance",
        ok: false,
        level: "error",
        message: `balance lookup failed (${lookup.failureReason}): ${lookup.message}`,
        fix: "check rpcUrl + chainId match the configured network",
      });
    } else {
      const above = !policy.reserveBalanceRaw || lookup.balance.raw >= policy.reserveBalanceRaw;
      checks.push({
        name: "signer-balance",
        ok: above,
        level: above ? "info" : "error",
        message: above
          ? `signer balance ≥ reserve: ${lookup.balance.formattedANM} ANM`
          : `signer balance ${lookup.balance.formattedANM} ANM below reserve`,
        fix: above ? undefined : "top up the signer wallet or lower reserveBalanceRaw",
      });
    }
  }

  // 7. Journals writable.
  let journalsWritable = false;
  try {
    accessSync(opts.stateDir, constants.W_OK);
    journalsWritable = true;
  } catch {
    journalsWritable = false;
  }
  checks.push({
    name: "journals-writable",
    ok: journalsWritable,
    level: journalsWritable ? "info" : "error",
    message: journalsWritable ? `state dir is writable: ${opts.stateDir}` : `state dir not writable: ${opts.stateDir}`,
    fix: journalsWritable ? undefined : "ensure the daemon user can write to stateDir",
  });

  const blockers = checks.filter((c) => !c.ok && c.level === "error");
  const ok = blockers.length === 0;
  const summary = ok
    ? "go-live: GO — all gates passed; live payouts are safe to run"
    : `go-live: NO-GO — ${blockers.length} blocker${blockers.length === 1 ? "" : "s"} (see fix hints)`;
  return {
    ok,
    generatedAt: new Date().toISOString(),
    summary,
    checks,
  };
}
