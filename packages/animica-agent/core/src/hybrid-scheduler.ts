/**
 * Hybrid mining scheduler.
 *
 * Decides how much capacity the useful-work runtime is allowed to consume
 * when block mining is also active on the same machine. The scheduler is
 * deterministic given its inputs so two callers observe the same plan,
 * and every decision is logged for operator audit.
 *
 * Modes:
 *   - chain-only           : useful-work disabled; scheduler returns 0 capacity
 *   - useful-only          : block mining ignored; full capacity to useful-work
 *   - hybrid-balanced      : even split, with min-floor reserved for chain mining
 *   - hybrid-miner-priority: useful-work gets remaining slots only when chain
 *                            mining is below a target hashrate, capped low
 *   - hybrid-useful-priority: useful-work gets majority share, chain mining
 *                            keeps a hard floor (default 1 worker)
 *
 * Slots are abstract: callers pass total available CPU threads (and an
 * optional GPU-slot count when a job supports GPU). The scheduler returns
 * (chainWorkers, usefulWorkers, gpuSlotsForUseful) along with safe defaults
 * and the configured/observed inputs that produced the plan.
 */

import { appendFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";

import type { AgentConfig, ResourceMode } from "./config.js";
import { detectMinerIdentity, probeMinerLive } from "./miner.js";
import { safeStringify } from "./safe-json.js";

export type HybridMode =
  | "chain-only"
  | "useful-only"
  | "hybrid-balanced"
  | "hybrid-miner-priority"
  | "hybrid-useful-priority";

export interface HybridInputs {
  /** Total CPU threads the operator allowed to be scheduled. Default = os.cpus() */
  totalThreads: number;
  /** Total GPU slots the operator allowed (0 if no GPU jobs supported). */
  totalGpuSlots: number;
  /** Is chain mining process active on this machine? */
  chainMinerActive: boolean;
  /** Observed chain miner hashrate (0 if unknown). */
  observedHashrate: number;
  /** Operator-configured target hashrate to reserve for chain mining. */
  targetHashrate: number;
}

export interface HybridDecision {
  mode: HybridMode;
  chainWorkers: number;
  usefulWorkers: number;
  /** GPU slots allocated to useful-work. Chain mining is presumed to manage GPU separately. */
  usefulGpuSlots: number;
  /** Why the scheduler chose these numbers. */
  rationale: string;
  inputs: HybridInputs;
  decidedAt: string;
}

const SAFE_FLOOR = 1;

/** Convert AgentConfig.resourceMode + miner state into a HybridMode. */
export function resolveHybridMode(cfg: AgentConfig, chainMinerActive: boolean): HybridMode {
  // Explicit overrides win.
  if (cfg.minerMode === "off") return "useful-only";
  if ((cfg as { hybridMode?: HybridMode }).hybridMode) {
    return (cfg as { hybridMode?: HybridMode }).hybridMode!;
  }
  const r: ResourceMode = cfg.resourceMode;
  if (!chainMinerActive) return "useful-only";
  if (r === "miner-priority") return "hybrid-miner-priority";
  if (r === "agent-priority") return "hybrid-useful-priority";
  return "hybrid-balanced";
}

export function plan(mode: HybridMode, inputs: HybridInputs): HybridDecision {
  const total = Math.max(SAFE_FLOOR, Math.floor(inputs.totalThreads));
  const decidedAt = new Date().toISOString();
  let chainWorkers = 0;
  let usefulWorkers = 0;
  let usefulGpuSlots = 0;
  let rationale = "";

  switch (mode) {
    case "chain-only":
      chainWorkers = total;
      usefulWorkers = 0;
      rationale = "chain-only mode: useful-work disabled";
      break;
    case "useful-only":
      chainWorkers = 0;
      usefulWorkers = total;
      usefulGpuSlots = Math.max(0, Math.floor(inputs.totalGpuSlots));
      rationale = "useful-only mode: block mining not running";
      break;
    case "hybrid-balanced": {
      // 50/50 split with a hard floor of 1 for each side.
      chainWorkers = Math.max(SAFE_FLOOR, Math.floor(total / 2));
      usefulWorkers = Math.max(SAFE_FLOOR, total - chainWorkers);
      usefulGpuSlots = Math.max(0, Math.floor(inputs.totalGpuSlots / 2));
      rationale = "balanced split between chain mining and useful-work";
      break;
    }
    case "hybrid-miner-priority": {
      // Chain mining gets the lion's share; useful-work gets ≤ 25% capped at
      // (total - SAFE_FLOOR). If observed hashrate is materially below target,
      // we cede usefulWorkers back to chain mining.
      const minerShare = Math.max(SAFE_FLOOR, Math.floor((total * 3) / 4));
      chainWorkers = minerShare;
      usefulWorkers = Math.max(0, total - chainWorkers);
      if (inputs.observedHashrate > 0 && inputs.targetHashrate > 0) {
        const ratio = inputs.observedHashrate / inputs.targetHashrate;
        if (ratio < 0.8) {
          // Chain mining is missing target — give it everything back.
          chainWorkers = total;
          usefulWorkers = 0;
          rationale = `miner-priority: observed ${inputs.observedHashrate} < 80% of target ${inputs.targetHashrate}; useful-work paused`;
          break;
        }
      }
      usefulGpuSlots = Math.max(0, Math.floor(inputs.totalGpuSlots / 4));
      rationale = "miner-priority: chain mining gets ¾, useful-work limited to ¼";
      break;
    }
    case "hybrid-useful-priority": {
      // Useful-work majority; chain mining keeps SAFE_FLOOR.
      chainWorkers = SAFE_FLOOR;
      usefulWorkers = Math.max(SAFE_FLOOR, total - chainWorkers);
      usefulGpuSlots = Math.max(0, inputs.totalGpuSlots);
      rationale = "useful-priority: chain mining keeps 1 worker; useful-work takes the rest";
      break;
    }
  }

  return {
    mode,
    chainWorkers,
    usefulWorkers,
    usefulGpuSlots,
    rationale,
    inputs,
    decidedAt,
  };
}

/** Higher-level helper: probe env + config and produce a decision. */
export async function planFromConfig(cfg: AgentConfig, overrides: Partial<HybridInputs> = {}): Promise<HybridDecision> {
  const identity = detectMinerIdentity(cfg);
  const live = identity.source !== "none" ? await probeMinerLive(identity).catch(() => null) : null;
  const cpus = overrides.totalThreads ?? cpuCount();
  const gpus = overrides.totalGpuSlots ?? 0;
  const chainMinerActive = overrides.chainMinerActive ?? (!!live?.metricsReachable || identity.source !== "none");
  const observed = overrides.observedHashrate ?? live?.hashrate ?? 0;
  const target = overrides.targetHashrate ?? Number(process.env.ANIMICA_MINER_TARGET_HASHRATE ?? "0") ?? 0;
  const mode = resolveHybridMode(cfg, chainMinerActive);
  return plan(mode, {
    totalThreads: cpus,
    totalGpuSlots: gpus,
    chainMinerActive,
    observedHashrate: Number.isFinite(observed) ? observed : 0,
    targetHashrate: Number.isFinite(target) ? target : 0,
  });
}

function cpuCount(): number {
  try {
    const os = require("node:os") as typeof import("node:os");
    return os.cpus()?.length ?? 1;
  } catch {
    return 1;
  }
}

/** Decision audit log so operators can correlate runtime behavior with policy changes. */
export class HybridDecisionLog {
  private readonly file: string;
  constructor(stateDir: string) {
    mkdirSync(stateDir, { recursive: true });
    this.file = join(stateDir, "hybrid-decisions.jsonl");
  }
  path(): string {
    return this.file;
  }
  record(decision: HybridDecision): void {
    appendFileSync(this.file, safeStringify(decision) + "\n", "utf8");
  }
}
