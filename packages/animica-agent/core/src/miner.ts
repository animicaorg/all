/**
 * Miner-aware adapter.
 *
 * Pure read-only. The Animica miner runtime lives in python/mining and is
 * driven by ANIMICA_MINER_* / ANIMICA_POOL_* environment variables. We never
 * touch miner state: we only read env + optional config files + probe the
 * miner metrics endpoint to surface identity, resource hints, and eligibility
 * for agent jobs.
 *
 * The metrics probe is best-effort and bounded by a short timeout so it
 * never delays interactive CLI commands when a miner is offline.
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import type { AgentConfig, MinerMode, ResourceMode } from "./config.js";
import { isLikelyAnimicaAddress } from "./rpc.js";
import { safeParse } from "./safe-json.js";

export interface MinerIdentity {
  /** Source of truth that produced this identity. */
  source: "env" | "config" | "wallet" | "user" | "none";
  /** Payout address if detected (validated as a likely Animica address). */
  payoutAddress?: string;
  /** Worker tag if configured. */
  worker?: string;
  /** Pool URL (stratum/HTTP) if available. */
  poolUrl?: string;
  /** Stratum host/port hint (informational only). */
  stratumHost?: string;
  stratumPort?: number;
  /** Metrics HTTP host/port hint. */
  metricsHost?: string;
  metricsPort?: number;
  /** Mining device hint from env (cpu/cuda/...). */
  device?: string;
  /** Chain id seen in miner env. */
  chainId?: string;
}

export interface MinerLiveStatus {
  metricsReachable: boolean;
  metricsUrl: string;
  /** Raw text body of the metrics endpoint, length-capped. */
  metricsSnippet?: string;
  /** Best-effort hashrate parsed from a Prometheus-style metrics body. */
  hashrate?: number;
  /** Whether a stratum server appears bound. */
  stratumOpen?: boolean;
  error?: string;
}

export interface MinerEligibility {
  mode: "free-local" | "wallet" | "miner" | "aicf" | "hybrid";
  /** Has the user got a payout address recognized as Animica-shaped? */
  walletConnected: boolean;
  /** Has the local miner identity been resolved? */
  minerConnected: boolean;
  /** Are AICF credits considered for advanced jobs? */
  aicfConsidered: boolean;
  /** Final yes/no with a one-line reason for the CLI. */
  allowed: boolean;
  reason: string;
}

export interface MinerSnapshot {
  identity: MinerIdentity;
  live: MinerLiveStatus | null;
  eligibility: MinerEligibility;
  resource: {
    mode: ResourceMode;
    cpuLimitPercent: number;
    safeToRunDuringMining: boolean;
    backgroundOnly: boolean;
  };
}

const ADDRESS_HEURISTICS = [
  // We accept Ethereum-style 0x addresses for compatibility with EVM bridge flows,
  // but we DO NOT enable signing/broadcast against them. They're just identity.
  /^0x[0-9a-fA-F]{40}$/,
];

function looksLikeAnyAddress(s: string): boolean {
  return isLikelyAnimicaAddress(s) || ADDRESS_HEURISTICS.some((re) => re.test(s));
}

/** Detect miner identity from env + optional miner config files. Never throws. */
export function detectMinerIdentity(cfg: AgentConfig): MinerIdentity {
  const env = process.env;
  const ident: MinerIdentity = { source: "none" };

  // 1) User-supplied wins (explicit user intent).
  if (cfg.minerAddress && looksLikeAnyAddress(cfg.minerAddress)) {
    ident.source = "user";
    ident.payoutAddress = cfg.minerAddress;
  }
  if (cfg.workerName) ident.worker = cfg.workerName;
  if (cfg.poolUrl) ident.poolUrl = cfg.poolUrl;

  // 2) Environment from miner/pool runtime.
  const envAddress =
    env.ANIMICA_POOL_ADDRESS ||
    env.ANIMICA_MINER_PAYOUT_ADDRESS ||
    env.ANIMICA_PAYOUT_ADDRESS;
  if (!ident.payoutAddress && envAddress && looksLikeAnyAddress(envAddress)) {
    ident.source = "env";
    ident.payoutAddress = envAddress;
  }
  if (!ident.worker && env.ANIMICA_MINER_WORKER) ident.worker = env.ANIMICA_MINER_WORKER;
  if (!ident.poolUrl && env.ANIMICA_POOL_URL) ident.poolUrl = env.ANIMICA_POOL_URL;
  ident.stratumHost = env.ANIMICA_MINER_STRATUM_HOST ?? "127.0.0.1";
  const sp = env.ANIMICA_MINER_STRATUM_PORT;
  ident.stratumPort = sp ? Number.parseInt(sp, 10) || 23454 : 23454;
  ident.metricsHost = env.ANIMICA_MINER_METRICS_HOST ?? "127.0.0.1";
  const mp = env.ANIMICA_MINER_METRICS_PORT;
  ident.metricsPort = mp ? Number.parseInt(mp, 10) || 9106 : 9106;
  if (env.ANIMICA_MINER_DEVICE) ident.device = env.ANIMICA_MINER_DEVICE;
  if (env.ANIMICA_MINER_CHAIN_ID) ident.chainId = env.ANIMICA_MINER_CHAIN_ID;

  // 3) Project config files dropped by tooling.
  const candidates = [
    join(cfg.workspacePath, ".animica", "miner.json"),
    join(cfg.workspacePath, ".animica", "pool.json"),
  ];
  for (const path of candidates) {
    if (!existsSync(path)) continue;
    try {
      const j = safeParse<Record<string, unknown>>(readFileSync(path, "utf8"));
      const addr = (j.payoutAddress ?? j.address) as string | undefined;
      if (!ident.payoutAddress && typeof addr === "string" && looksLikeAnyAddress(addr)) {
        ident.source = ident.source === "none" ? "config" : ident.source;
        ident.payoutAddress = addr;
      }
      if (!ident.worker && typeof j.worker === "string") ident.worker = j.worker;
      if (!ident.poolUrl && typeof j.poolUrl === "string") ident.poolUrl = j.poolUrl;
    } catch {
      /* tolerate malformed miner config files */
    }
  }

  if (ident.payoutAddress && ident.source === "none") ident.source = "config";
  return ident;
}

export async function probeMinerLive(identity: MinerIdentity, timeoutMs = 1500): Promise<MinerLiveStatus | null> {
  if (!identity.metricsHost || !identity.metricsPort) return null;
  const url = `http://${identity.metricsHost}:${identity.metricsPort}/metrics`;
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: controller.signal });
    if (!res.ok) {
      return { metricsReachable: false, metricsUrl: url, error: `HTTP ${res.status}` };
    }
    const body = await res.text();
    const snippet = body.slice(0, 2048);
    return {
      metricsReachable: true,
      metricsUrl: url,
      metricsSnippet: snippet,
      hashrate: parseHashrate(snippet),
      stratumOpen: undefined,
    };
  } catch (err) {
    return {
      metricsReachable: false,
      metricsUrl: url,
      error: (err as Error).message,
    };
  } finally {
    clearTimeout(t);
  }
}

/** Lightweight Prometheus parser for the well-known animica miner hashrate metric. */
function parseHashrate(body: string): number | undefined {
  const lines = body.split(/\r?\n/);
  for (const line of lines) {
    if (line.startsWith("#")) continue;
    const m = line.match(/^(animica_miner_hashrate(?:_total)?(?:\{[^}]*\})?)\s+([0-9.+eE-]+)/);
    if (m) {
      const v = Number.parseFloat(m[2]);
      if (Number.isFinite(v)) return v;
    }
  }
  return undefined;
}

export function evaluateEligibility(cfg: AgentConfig, identity: MinerIdentity): MinerEligibility {
  const walletConnected = !!cfg.minerAddress && looksLikeAnyAddress(cfg.minerAddress);
  const minerConnected =
    !!identity.payoutAddress && (identity.source === "env" || identity.source === "config" || identity.source === "user");
  const aicfConsidered = cfg.aicfMode === "enabled";
  const credits = cfg.creditsMode;

  let allowed = true;
  let reason = "local-free mode: agent runs without gating";

  switch (credits) {
    case "off":
      allowed = true;
      reason = "credits gating disabled; free-local mode";
      break;
    case "wallet":
      allowed = walletConnected;
      reason = allowed
        ? "wallet-authenticated"
        : "wallet credits required: configure walletMode and connect a wallet";
      break;
    case "miner":
      allowed = minerConnected;
      reason = allowed
        ? "miner-authenticated"
        : "miner credits required: connect a miner identity via `animica-agent miner connect`";
      break;
    case "aicf":
      allowed = aicfConsidered;
      reason = allowed
        ? "AICF credits considered"
        : "AICF mode disabled: enable aicfMode in config";
      break;
    case "hybrid":
      allowed = walletConnected || minerConnected || aicfConsidered;
      reason = allowed
        ? "hybrid eligibility met"
        : "hybrid mode requires at least one of: wallet, miner, or AICF mode enabled";
      break;
  }

  const mode: MinerEligibility["mode"] =
    credits === "off" ? "free-local" : (credits as MinerEligibility["mode"]);

  return { mode, walletConnected, minerConnected, aicfConsidered, allowed, reason };
}

export interface ResourcePlan {
  mode: ResourceMode;
  cpuLimitPercent: number;
  safeToRunDuringMining: boolean;
  backgroundOnly: boolean;
  workerCount: number;
}

export function planResources(cfg: AgentConfig, minerActive: boolean): ResourcePlan {
  // Miner is considered "active" if metrics endpoint is reachable OR identity is configured.
  const mode = cfg.resourceMode;
  let cpu = cfg.cpuLimit;
  let safe = true;
  let bg = false;
  const cores = Math.max(1, navigatorHardwareConcurrency());

  if (mode === "miner-priority" && minerActive) {
    cpu = Math.min(cpu, 25);
    bg = true;
  } else if (mode === "agent-priority") {
    cpu = Math.max(cpu, 90);
  } else if (mode === "balanced" && minerActive) {
    cpu = Math.min(cpu, 50);
  }
  // Heavy operations should warn the user if the miner is hot.
  if (minerActive && cpu > 60) safe = false;
  const workerCount = Math.max(1, Math.floor((cores * cpu) / 100));
  return { mode, cpuLimitPercent: cpu, safeToRunDuringMining: safe, backgroundOnly: bg, workerCount };
}

function navigatorHardwareConcurrency(): number {
  // Avoid pulling in node:os just to derive a hint. Node exposes os.cpus()
  // but we keep this module fetch-only friendly. Fallback to 4 if unknown.
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const os = require("node:os") as typeof import("node:os");
    return os.cpus()?.length ?? 4;
  } catch {
    return 4;
  }
}

export async function snapshotMiner(cfg: AgentConfig): Promise<MinerSnapshot> {
  const identity = detectMinerIdentity(cfg);
  const minerEffectivelyOff = cfg.minerMode === "off";
  const live = minerEffectivelyOff ? null : await probeMinerLive(identity).catch(() => null);
  const minerActive = !!live?.metricsReachable || identity.source !== "none";
  const eligibility = evaluateEligibility(cfg, identity);
  const plan = planResources(cfg, minerActive);
  return {
    identity,
    live,
    eligibility,
    resource: {
      mode: plan.mode,
      cpuLimitPercent: plan.cpuLimitPercent,
      safeToRunDuringMining: plan.safeToRunDuringMining,
      backgroundOnly: plan.backgroundOnly,
    },
  };
}

export function resolveMinerMode(mode: MinerMode): MinerMode {
  if (mode !== "auto") return mode;
  const env = process.env;
  if (env.ANIMICA_POOL_MODE === "pool" || env.ANIMICA_MINER_STRATUM_ENABLED) return "local";
  if (env.ANIMICA_POOL_URL) return "pool";
  return "off";
}
