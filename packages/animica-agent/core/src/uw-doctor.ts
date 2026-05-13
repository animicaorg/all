/**
 * `doctor useful-work` go/no-go report.
 *
 * Single entry point that aggregates everything an operator needs to know
 * before starting (or trusting) the useful-work runtime. Returns a strict
 * `ok` boolean plus a per-check breakdown. Designed for both human display
 * and `--json` consumption by ops dashboards.
 */

import { existsSync, statSync } from "node:fs";

import type { AgentConfig } from "./config.js";
import { detectMinerIdentity, evaluateEligibility, probeMinerLive } from "./miner.js";
import { isLikelyAnimicaAddress, probeNode } from "./rpc.js";
import { checkSettlementReadiness } from "./settlement.js";
import { coordinatorDoctor, type HardenedCoordinatorOptions } from "./coordinator-hardened.js";
import { inspectQueues } from "./journal-admin.js";
import { planFromConfig } from "./hybrid-scheduler.js";

export interface DoctorCheck {
  name: string;
  ok: boolean;
  level: "info" | "warning" | "error";
  message: string;
  detail?: Record<string, unknown>;
}

export interface DoctorReport {
  ok: boolean;
  generatedAt: string;
  checks: DoctorCheck[];
  /** Operator summary string. */
  summary: string;
}

export interface DoctorOptions {
  stateDir: string;
  coordinator?: HardenedCoordinatorOptions;
  estimatedCostRaw?: bigint;
  walletAddress?: string;
}

export async function doctorUsefulWork(cfg: AgentConfig, opts: DoctorOptions): Promise<DoctorReport> {
  const checks: DoctorCheck[] = [];

  // 1. Node + chain id.
  const node = await probeNode(cfg.rpcUrl).catch(() => null);
  checks.push({
    name: "node",
    ok: !!node?.reachable,
    level: node?.reachable ? "info" : "error",
    message: node?.reachable
      ? `RPC reachable at ${cfg.rpcUrl}, chainId=${node.chainId?.toString() ?? "?"}`
      : `RPC unreachable at ${cfg.rpcUrl}`,
    detail: node ? { chainId: node.chainId?.toString(), blockNumber: node.blockNumber?.toString() } : undefined,
  });
  if (node?.chainId !== null && node?.chainId !== undefined && node.chainId.toString() !== cfg.chainId) {
    checks.push({
      name: "chain-id",
      ok: false,
      level: "error",
      message: `configured chainId=${cfg.chainId} but node reports ${node.chainId.toString()}`,
    });
  } else {
    checks.push({
      name: "chain-id",
      ok: true,
      level: "info",
      message: `chainId matches (${cfg.chainId})`,
    });
  }

  // 2. Wallet identity.
  const address = opts.walletAddress ?? cfg.minerAddress;
  checks.push({
    name: "wallet",
    ok: !!address && isLikelyAnimicaAddress(address),
    level: address && isLikelyAnimicaAddress(address) ? "info" : "error",
    message: address
      ? isLikelyAnimicaAddress(address)
        ? `wallet/payout address: ${address}`
        : `address fails bech32 prefix check: ${address}`
      : `no wallet address configured`,
  });

  // 3. Miner identity & eligibility.
  const id = detectMinerIdentity(cfg);
  const elig = evaluateEligibility(cfg, id);
  checks.push({
    name: "miner-eligibility",
    ok: elig.allowed,
    level: elig.allowed ? "info" : "warning",
    message: elig.reason,
    detail: { mode: elig.mode, walletConnected: elig.walletConnected, minerConnected: elig.minerConnected },
  });
  const live = await probeMinerLive(id).catch(() => null);
  checks.push({
    name: "miner-metrics",
    ok: true,
    level: live?.metricsReachable ? "info" : "warning",
    message: live?.metricsReachable
      ? `miner metrics reachable at ${live.metricsUrl} (hashrate=${live.hashrate ?? "?"})`
      : `miner metrics not reachable; useful-work will run in standalone mode`,
  });

  // 4. Settlement readiness.
  const settle = await checkSettlementReadiness(cfg, {
    estimatedCostRaw: opts.estimatedCostRaw ?? 0n,
    walletAddress: address,
    txBinary: false, // we only ask the higher-level doctor to be RPC-bound
  });
  for (const c of settle.checks) {
    checks.push({
      name: `settlement.${c.reason}`,
      ok: c.ok,
      level: c.ok ? "info" : c.reason === "rpc-unreachable" || c.reason === "chain-id-mismatch" ? "error" : "warning",
      message: c.message,
      detail: c.details,
    });
  }

  // 5. Coordinator doctor if configured.
  if (opts.coordinator) {
    const r = await coordinatorDoctor(opts.coordinator).catch((err) => ({
      ok: false,
      baseUrl: opts.coordinator!.baseUrl,
      authConfigured: false,
      healthEndpoint: { ok: false, error: (err as Error).message },
      jobsEndpoint: { ok: false, error: (err as Error).message },
      notes: [],
    }));
    checks.push({
      name: "coordinator",
      ok: r.ok,
      level: r.ok ? "info" : "error",
      message: r.ok ? `coordinator ${r.baseUrl} healthy` : `coordinator unhealthy: ${r.healthEndpoint.error ?? r.jobsEndpoint.error ?? "unknown"}`,
      detail: { authConfigured: r.authConfigured, notes: r.notes },
    });
  } else {
    checks.push({
      name: "coordinator",
      ok: true,
      level: "info",
      message: "no remote coordinator configured; LocalCoordinator will be used",
    });
  }

  // 6. Hybrid plan.
  const plan = await planFromConfig(cfg);
  checks.push({
    name: "hybrid-plan",
    ok: true,
    level: plan.mode === "chain-only" ? "warning" : "info",
    message: `hybrid mode=${plan.mode} chainWorkers=${plan.chainWorkers} usefulWorkers=${plan.usefulWorkers}`,
    detail: { rationale: plan.rationale, inputs: plan.inputs },
  });

  // 7. Journal health.
  const queues = inspectQueues(opts.stateDir);
  if (queues.jobs.oldestInFlightAgeMs && queues.jobs.oldestInFlightAgeMs > 30 * 60_000) {
    checks.push({
      name: "journal-staleness",
      ok: false,
      level: "warning",
      message: `oldest in-flight job is ${(queues.jobs.oldestInFlightAgeMs / 60_000).toFixed(1)}m old`,
      detail: { ...queues.jobs },
    });
  } else {
    checks.push({
      name: "journal-health",
      ok: true,
      level: "info",
      message: `jobs.inFlight=${queues.jobs.inFlight} settlements.inFlight=${queues.settlements.inFlight}`,
    });
  }
  if (queues.jobs.journalSizeBytes > 5 * 1024 * 1024) {
    checks.push({
      name: "journal-size",
      ok: false,
      level: "warning",
      message: `jobs journal is ${(queues.jobs.journalSizeBytes / 1024).toFixed(0)} KiB; consider compaction`,
    });
  }

  // 8. Settlement mode + reserve policy visibility.
  const settlementMode = cfg.settlementMode ?? "offline";
  const reservePolicy = cfg.reservePolicy ?? (settlementMode === "live" ? "strict" : "off");
  checks.push({
    name: "settlement-mode",
    ok: true,
    level: settlementMode === "live" ? "warning" : "info",
    message: `settlementMode=${settlementMode}, reservePolicy=${reservePolicy}`,
    detail: { settlementMode, reservePolicy },
  });
  if (settlementMode === "live" && reservePolicy === "off") {
    checks.push({
      name: "settlement-reserve-escape",
      ok: true,
      level: "warning",
      message: "settlementMode=live with reservePolicy=off — reserve enforcement DISABLED; suitable for dev only",
    });
  }

  // 9. State dir presence and writability.
  checks.push({
    name: "state-dir",
    ok: existsSync(opts.stateDir),
    level: existsSync(opts.stateDir) ? "info" : "error",
    message: `state dir: ${opts.stateDir}`,
    detail: existsSync(opts.stateDir) ? { sizeBytes: statSync(opts.stateDir).size } : undefined,
  });

  const ok = checks.every((c) => c.ok || c.level !== "error");
  return {
    ok,
    generatedAt: new Date().toISOString(),
    checks,
    summary: ok ? "useful-work runtime is go" : `useful-work runtime has ${checks.filter((c) => !c.ok && c.level === "error").length} blocker(s)`,
  };
}
