import {
  detectMinerIdentity,
  evaluateEligibility,
  loadConfig,
  planResources,
  probeMinerLive,
  resolveMinerMode,
  writeProjectConfig,
} from "@animica/agent-core";

import { stringFlag } from "../args.js";
import { c, header, info, kv, ok, warn } from "../output.js";

export async function runMinerConnect(positionals: string[], options: Record<string, string | boolean>): Promise<number> {
  const { config, paths } = loadConfig();
  const address = stringFlag(options, "address") ?? positionals[0] ?? config.minerAddress;
  const worker = stringFlag(options, "worker") ?? config.workerName;
  const poolUrl = stringFlag(options, "pool-url") ?? config.poolUrl;
  const minerMode = (stringFlag(options, "miner-mode") as "off" | "auto" | "local" | "pool" | undefined) ?? resolveMinerMode(config.minerMode);
  const next = { ...config, minerAddress: address, workerName: worker, poolUrl, minerMode };
  writeProjectConfig(paths, { minerAddress: address, workerName: worker, poolUrl, minerMode });
  ok(`saved miner identity to ${paths.projectFile}`);
  const identity = detectMinerIdentity(next);
  const live = await probeMinerLive(identity).catch(() => null);
  const elig = evaluateEligibility(next, identity);
  header("Miner identity");
  kv([
    ["source", identity.source],
    ["payoutAddress", identity.payoutAddress],
    ["worker", identity.worker],
    ["poolUrl", identity.poolUrl],
    ["stratum", identity.stratumHost && identity.stratumPort ? `${identity.stratumHost}:${identity.stratumPort}` : "—"],
    ["metrics", live?.metricsUrl],
    ["metricsReachable", live?.metricsReachable],
    ["hashrate", live?.hashrate],
  ]);
  header("Eligibility");
  kv([
    ["mode", elig.mode],
    ["allowed", elig.allowed],
    ["reason", elig.reason],
  ]);
  return 0;
}

export async function runMinerStatus(): Promise<number> {
  const { config } = loadConfig();
  const identity = detectMinerIdentity(config);
  const live = await probeMinerLive(identity).catch(() => null);
  const elig = evaluateEligibility(config, identity);
  const plan = planResources(config, identity.source !== "none" || !!live?.metricsReachable);
  header("Miner status");
  kv([
    ["mode (effective)", resolveMinerMode(config.minerMode)],
    ["source", identity.source],
    ["payoutAddress", identity.payoutAddress],
    ["worker", identity.worker],
    ["poolUrl", identity.poolUrl],
    ["device", identity.device],
    ["chainId(env)", identity.chainId],
    ["metricsReachable", live?.metricsReachable],
    ["hashrate", live?.hashrate],
  ]);
  header("Eligibility & resources");
  kv([
    ["creditsMode", config.creditsMode],
    ["aicfMode", config.aicfMode],
    ["allowed", elig.allowed],
    ["reason", elig.reason],
    ["resourceMode", plan.mode],
    ["cpuLimit", `${plan.cpuLimitPercent}%`],
    ["safeToRunDuringMining", plan.safeToRunDuringMining],
    ["backgroundOnly", plan.backgroundOnly],
  ]);
  if (!plan.safeToRunDuringMining) {
    warn("Agent operations may compete with mining. Use --resource-mode miner-priority for heavy tasks.");
  } else {
    info(c.dim("Safe-to-run-during-mining: yes."));
  }
  return 0;
}
