/**
 * Useful-work readiness aggregator.
 *
 * One entry point that an operator can run before pointing the daemon at a
 * funded wallet. It rolls up every production-critical gate into a single
 * report:
 *
 *   - wallet identity is resolvable
 *   - signer balance lookup succeeds and is above reserve
 *   - coordinator (when configured) passes verify-live's auth + schema checks
 *   - settlement queues have no abandoned in-flight attempts
 *   - journal health (file sizes, ages) is within bounds
 *   - hybrid scheduler config produces a sane plan
 *
 * Fails closed: any production-critical failure causes `ok=false` so a
 * dashboard can wire `exit code !== 0` to an alert without inspecting
 * individual checks. Each check carries a `level` ("error" / "warning" /
 * "info") so operators can spot soft regressions vs blocking issues.
 */

import { existsSync } from "node:fs";
import { join } from "node:path";

import {
  RpcBalanceProvider,
  type BalanceProvider,
} from "./balance-provider.js";
import type { AgentConfig } from "./config.js";
import {
  coordinatorDoctor,
  type HardenedCoordinatorOptions,
} from "./coordinator-hardened.js";
import { planFromConfig } from "./hybrid-scheduler.js";
import { inspectQueues } from "./journal-admin.js";
import { detectMinerIdentity, evaluateEligibility } from "./miner.js";
import {
  DEFAULT_PAYOUT_POLICY,
  type PayoutPolicyConfig,
} from "./payout-policy.js";
import { isLikelyAnimicaAddress, probeNode } from "./rpc.js";
import { resolveWalletIdentity } from "./wallet.js";

export type ReadinessLevel = "info" | "warning" | "error";

export interface ReadinessAggregateCheck {
  name: string;
  ok: boolean;
  level: ReadinessLevel;
  message: string;
  /** Best-effort detail; safe to JSON-serialize. */
  detail?: Record<string, unknown>;
}

export interface ReadinessAggregateReport {
  ok: boolean;
  generatedAt: string;
  summary: string;
  checks: ReadinessAggregateCheck[];
  /** True only when every error-level check passed. Warnings do NOT downgrade ok. */
  blockers: ReadinessAggregateCheck[];
  /** Soft regressions that an operator should still see. */
  warnings: ReadinessAggregateCheck[];
}

export interface ReadinessAggregateOptions {
  stateDir: string;
  /** When set, runs the coordinator doctor against this base URL. */
  coordinator?: HardenedCoordinatorOptions;
  /** Override signer address (else cfg.minerAddress). */
  signerAddress?: string;
  /** Override the balance provider (tests). */
  balanceProvider?: BalanceProvider;
  /** Effective payout policy. Defaults to DEFAULT_PAYOUT_POLICY. */
  policy?: PayoutPolicyConfig;
  /** When true (default), runs the network-touching checks. Tests pass false
   *  to skip the slow paths. */
  network?: boolean;
}

export async function usefulWorkReadiness(
  cfg: AgentConfig,
  opts: ReadinessAggregateOptions,
): Promise<ReadinessAggregateReport> {
  const checks: ReadinessAggregateCheck[] = [];
  const policy = { ...DEFAULT_PAYOUT_POLICY, ...(opts.policy ?? {}) };
  const network = opts.network !== false;

  // 1. Wallet / signer identity.
  const signerAddress = opts.signerAddress ?? cfg.minerAddress ?? resolveWalletIdentity(cfg)?.address ?? "";
  checks.push({
    name: "wallet.identity",
    ok: !!signerAddress && isLikelyAnimicaAddress(signerAddress),
    level: signerAddress && isLikelyAnimicaAddress(signerAddress) ? "info" : "error",
    message: signerAddress
      ? isLikelyAnimicaAddress(signerAddress)
        ? `signer/payout address: ${signerAddress}`
        : `signer address fails Animica prefix check: ${signerAddress}`
      : "no signer/payout address configured (set minerAddress)",
  });

  // 2. Miner eligibility (so resourceMode + miner-mode aren't quietly broken).
  const id = detectMinerIdentity(cfg);
  const elig = evaluateEligibility(cfg, id);
  checks.push({
    name: "miner.eligibility",
    ok: elig.allowed,
    level: elig.allowed ? "info" : "warning",
    message: elig.reason,
    detail: { mode: elig.mode, walletConnected: elig.walletConnected, minerConnected: elig.minerConnected },
  });

  // 3. RPC reachability + chain id.
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
      name: "rpc.reachable",
      ok: node.reachable,
      level: node.reachable ? "info" : "error",
      message: node.reachable
        ? `RPC ${cfg.rpcUrl} reachable, chainId=${node.chainId?.toString() ?? "?"}`
        : `RPC ${cfg.rpcUrl} unreachable: ${node.error ?? "unknown"}`,
    });
    if (node.reachable && node.chainId !== null && node.chainId.toString() !== cfg.chainId) {
      checks.push({
        name: "rpc.chain-id",
        ok: false,
        level: "error",
        message: `expected chainId=${cfg.chainId} but node reports ${node.chainId.toString()}`,
      });
    } else if (node.reachable) {
      checks.push({
        name: "rpc.chain-id",
        ok: true,
        level: "info",
        message: `chainId matches (${cfg.chainId})`,
      });
    }
  }

  // 4. Balance + reserve. We always try this when a signer address is set,
  //    even when network=false (the injected provider handles fixtures).
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
        name: "balance.lookup",
        ok: false,
        level: "error",
        message: `balance lookup failed (${lookup.failureReason}): ${lookup.message}`,
      });
    } else {
      checks.push({
        name: "balance.lookup",
        ok: true,
        level: "info",
        message: `signer balance: ${lookup.balance.formattedANM} ANM (chainId=${lookup.observedChainId})`,
        detail: { raw: lookup.balance.raw.toString() },
      });
      // Reserve enforcement: must be above policy.reserveBalanceRaw.
      if (policy.reserveBalanceRaw && policy.reserveBalanceRaw > 0n) {
        const above = lookup.balance.raw >= policy.reserveBalanceRaw;
        checks.push({
          name: "balance.reserve",
          ok: above,
          level: above ? "info" : "error",
          message: above
            ? `balance ≥ reserve (${policy.reserveBalanceRaw.toString()} raw)`
            : `balance ${lookup.balance.raw.toString()} raw < reserve ${policy.reserveBalanceRaw.toString()} raw`,
        });
      }
    }
  }

  // 5. Coordinator (optional).
  if (opts.coordinator && network) {
    try {
      const r = await coordinatorDoctor(opts.coordinator);
      checks.push({
        name: "coordinator.health",
        ok: r.ok,
        level: r.ok ? "info" : "error",
        message: r.ok
          ? `coordinator ${r.baseUrl} healthy`
          : `coordinator unhealthy (${r.healthEndpoint.error ?? r.jobsEndpoint.error ?? "unknown"})`,
      });
      checks.push({
        name: "coordinator.auth",
        ok: r.authConfigured,
        level: r.authConfigured ? "info" : "warning",
        message: r.authConfigured ? "coordinator auth token present" : "no coordinator auth token configured (calls will be unauthenticated)",
      });
    } catch (err) {
      checks.push({
        name: "coordinator.health",
        ok: false,
        level: "error",
        message: `coordinator doctor errored: ${(err as Error).message?.slice(0, 200)}`,
      });
    }
  } else if (!opts.coordinator) {
    checks.push({
      name: "coordinator.health",
      ok: true,
      level: "info",
      message: "no remote coordinator configured; LocalCoordinator will be used",
    });
  }

  // 6. Journal health.
  const queues = inspectQueues(opts.stateDir);
  const stale = queues.jobs.oldestInFlightAgeMs && queues.jobs.oldestInFlightAgeMs > 30 * 60_000;
  checks.push({
    name: "journal.health",
    ok: !stale,
    level: stale ? "warning" : "info",
    message: stale
      ? `oldest in-flight job is ${(queues.jobs.oldestInFlightAgeMs! / 60_000).toFixed(1)}m old; consider 'animica-agent miner runtime' to inspect`
      : `jobs.inFlight=${queues.jobs.inFlight} settlements.inFlight=${queues.settlements.inFlight}`,
  });
  const tooBig = queues.jobs.journalSizeBytes > 5 * 1024 * 1024;
  if (tooBig) {
    checks.push({
      name: "journal.size",
      ok: false,
      level: "warning",
      message: `jobs journal is ${(queues.jobs.journalSizeBytes / 1024).toFixed(0)} KiB; run 'animica-agent journal compact'`,
    });
  }

  // 7. Settlement queue: any non-terminal attempts older than 6h?
  const sStale = queues.settlements.oldestInFlightAgeMs && queues.settlements.oldestInFlightAgeMs > 6 * 60 * 60_000;
  if (sStale) {
    checks.push({
      name: "settlement.queue-stale",
      ok: false,
      level: "warning",
      message: `oldest in-flight settlement is ${(queues.settlements.oldestInFlightAgeMs! / 3_600_000).toFixed(1)}h old; run 'animica-agent settlement watch'`,
    });
  }

  // 8. Hybrid plan sanity.
  try {
    const plan = await planFromConfig(cfg);
    const chainOnly = plan.mode === "chain-only";
    checks.push({
      name: "hybrid.plan",
      ok: !chainOnly,
      level: chainOnly ? "warning" : "info",
      message: `hybrid mode=${plan.mode} chainWorkers=${plan.chainWorkers} usefulWorkers=${plan.usefulWorkers}`,
      detail: { rationale: plan.rationale },
    });
  } catch (err) {
    checks.push({
      name: "hybrid.plan",
      ok: false,
      level: "warning",
      message: `hybrid plan errored: ${(err as Error).message?.slice(0, 200)}`,
    });
  }

  // 9. State dir.
  checks.push({
    name: "state.dir",
    ok: existsSync(opts.stateDir),
    level: existsSync(opts.stateDir) ? "info" : "error",
    message: `state dir: ${opts.stateDir}`,
    detail: { exists: existsSync(opts.stateDir) },
  });

  // 10. Operator-grade summary.
  const blockers = checks.filter((c) => !c.ok && c.level === "error");
  const warnings = checks.filter((c) => !c.ok && c.level === "warning");
  const ok = blockers.length === 0;
  const summary = ok
    ? warnings.length === 0
      ? "useful-work readiness: GO"
      : `useful-work readiness: GO (with ${warnings.length} warning${warnings.length === 1 ? "" : "s"})`
    : `useful-work readiness: NO-GO (${blockers.length} blocker${blockers.length === 1 ? "" : "s"}, ${warnings.length} warning${warnings.length === 1 ? "" : "s"})`;
  return {
    ok,
    generatedAt: new Date().toISOString(),
    checks,
    blockers,
    warnings,
    summary,
  };
}

/**
 * Static descriptor of every readiness check the aggregator can emit, plus a
 * one-line explanation of what a failure means and the recommended fix. Used
 * by `animica-agent useful-work readiness --explain`.
 */
export const READINESS_FAILURE_GUIDE: Record<
  string,
  { what: string; fix: string }
> = {
  "wallet.identity": {
    what: "the daemon has no usable signer/payout address",
    fix: "set minerAddress in .animica/agent.json or pass --signer / wallet connect <addr>",
  },
  "miner.eligibility": {
    what: "the miner integration is disabled or pointing at the wrong wallet",
    fix: "run `animica-agent miner connect <addr>` or set minerMode=local in .animica/agent.json",
  },
  "rpc.reachable": {
    what: "the daemon cannot reach the configured Animica RPC node",
    fix: "verify rpcUrl in .animica/agent.json and that the node is running",
  },
  "rpc.chain-id": {
    what: "the configured chainId does not match what the node reports",
    fix: "align chainId in .animica/agent.json with the actual network",
  },
  "balance.lookup": {
    what: "the daemon cannot read the signer's balance from RPC",
    fix: "check rpcUrl, network connectivity, and signer address shape; rerun `animica-agent doctor useful-work`",
  },
  "balance.reserve": {
    what: "signer balance is below the policy reserve threshold",
    fix: "top up the signer wallet or lower reserveBalanceRaw in the payout policy",
  },
  "coordinator.health": {
    what: "the configured AICF coordinator is unreachable or returns an unexpected shape",
    fix: "run `animica-agent coordinator verify-live --url <url>` to see the full report",
  },
  "coordinator.auth": {
    what: "no coordinator auth token is present in the environment",
    fix: "set ANIMICA_AICF_KEY (or the env var configured via authEnv)",
  },
  "journal.health": {
    what: "an in-flight job has been parked for more than 30 minutes",
    fix: "inspect with `animica-agent miner runtime` and `animica-agent settlement list`",
  },
  "journal.size": {
    what: "the jobs journal has grown past the recommended threshold",
    fix: "run `animica-agent journal compact` while the daemon is idle",
  },
  "settlement.queue-stale": {
    what: "a settlement attempt has been in-flight for more than 6 hours",
    fix: "run `animica-agent settlement watch` to advance it or inspect with `settlement show`",
  },
  "hybrid.plan": {
    what: "the hybrid scheduler resolves to chain-only or fails to plan",
    fix: "set resourceMode=miner-priority|agent-priority|balanced and verify minerMode != off",
  },
  "state.dir": {
    what: "the state dir does not exist or is not accessible",
    fix: "ensure the daemon user can read/write the configured stateDir",
  },
};
