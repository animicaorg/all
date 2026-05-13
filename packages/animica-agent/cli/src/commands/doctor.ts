import {
  detectMinerIdentity,
  evaluateEligibility,
  gitInfo,
  loadConfig,
  planResources,
  probeMinerLive,
  probeNode,
  resolveWalletIdentity,
} from "@animica/agent-core";

import { c, header, info, kv, warn } from "../output.js";

export async function runDoctor(): Promise<number> {
  let warnings = 0;
  const { config, paths } = loadConfig();

  header("Project");
  kv([
    ["root", paths.projectRoot],
    ["projectConfig", paths.projectFile],
    ["userConfig", paths.userFile],
    ["stateDir", paths.stateDir],
  ]);
  const git = gitInfo(paths.projectRoot);
  kv([
    ["gitRepo", git.isRepo],
    ["branch", git.branch],
    ["dirty", git.dirty],
    ["staged", git.staged ?? 0],
    ["unstaged", git.unstaged ?? 0],
    ["untracked", git.untracked ?? 0],
  ]);

  header("Configuration");
  kv([
    ["rpcUrl", config.rpcUrl],
    ["chainId", config.chainId],
    ["walletMode", config.walletMode],
    ["minerMode", config.minerMode],
    ["approvalMode", config.approvalMode],
    ["resourceMode", config.resourceMode],
    ["provider", config.provider ?? "offline"],
    ["allowShell", config.allowShell],
    ["allowGitWrite", config.allowGitWrite],
  ]);

  header("Node");
  const node = await probeNode(config.rpcUrl);
  kv([
    ["reachable", node.reachable],
    ["clientVersion", node.clientVersion],
    ["chainId", node.chainId === null ? null : node.chainId.toString()],
    ["blockNumber", node.blockNumber === null ? null : node.blockNumber.toString()],
    ["syncing", node.syncing ? JSON.stringify(node.syncing) : false],
    ["error", node.error],
  ]);
  if (!node.reachable) {
    warnings++;
    warn(`RPC ${config.rpcUrl} did not respond. Start a local node with \`npx animica-node start\` or set ANIMICA_AGENT_RPC_URL.`);
  } else if (node.chainId !== null && node.chainId.toString() !== config.chainId) {
    warnings++;
    warn(`Configured chainId=${config.chainId} but node reports ${node.chainId.toString()}.`);
  }

  header("Wallet");
  const wallet = resolveWalletIdentity(config);
  kv([
    ["mode", config.walletMode],
    ["address", wallet?.address],
    ["source", wallet?.source],
  ]);

  header("Miner");
  const identity = detectMinerIdentity(config);
  const live = await probeMinerLive(identity);
  kv([
    ["source", identity.source],
    ["payoutAddress", identity.payoutAddress],
    ["worker", identity.worker],
    ["poolUrl", identity.poolUrl],
    ["device", identity.device],
    ["metricsUrl", live?.metricsUrl],
    ["metricsReachable", live?.metricsReachable],
    ["hashrate", live?.hashrate],
  ]);
  const eligibility = evaluateEligibility(config, identity);
  kv([
    ["creditsMode", eligibility.mode],
    ["walletConnected", eligibility.walletConnected],
    ["minerConnected", eligibility.minerConnected],
    ["aicfConsidered", eligibility.aicfConsidered],
    ["allowed", eligibility.allowed],
    ["reason", eligibility.reason],
  ]);
  if (!eligibility.allowed) warnings++;

  header("Resources");
  const plan = planResources(config, identity.source !== "none" || !!live?.metricsReachable);
  kv([
    ["mode", plan.mode],
    ["cpuLimit", `${plan.cpuLimitPercent}%`],
    ["safeToRunDuringMining", plan.safeToRunDuringMining],
    ["backgroundOnly", plan.backgroundOnly],
    ["workerCount", plan.workerCount],
  ]);
  if (!plan.safeToRunDuringMining) {
    warn(`Resource plan suggests miner contention. Consider \`animica-agent --resource-mode miner-priority …\` or lowering cpuLimit.`);
  }

  info("");
  if (warnings === 0) {
    info(c.green("All checks passed."));
    return 0;
  }
  info(c.yellow(`Doctor finished with ${warnings} warning(s).`));
  return 0;
}
