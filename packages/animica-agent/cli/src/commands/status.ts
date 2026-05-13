import {
  detectMinerIdentity,
  fetchBalance,
  formatANM,
  gitInfo,
  journalStats,
  loadConfig,
  probeMinerLive,
  probeNode,
  resolveWalletIdentity,
  Repo,
} from "@animica/agent-core";
import { join } from "node:path";

import { boolFlag } from "../args.js";
import { header, info, kv } from "../output.js";
import { safeStringify } from "@animica/agent-core/safe-json";

export async function runStatus(options: Record<string, string | boolean>): Promise<number> {
  const json = boolFlag(options, "json", false);
  const { config, paths } = loadConfig();
  const git = gitInfo(paths.projectRoot);
  const node = await probeNode(config.rpcUrl).catch(() => null);
  const wallet = resolveWalletIdentity(config);
  const balance = wallet && node?.reachable
    ? await fetchBalance(config.rpcUrl, wallet.address).catch(() => null)
    : null;
  const identity = detectMinerIdentity(config);
  const minerLive = await probeMinerLive(identity).catch(() => null);
  const repoSummary = new Repo(paths.projectRoot).summary();
  const patches = journalStats(join(paths.stateDir, "patches"));

  if (json) {
    const payload = {
      config,
      paths,
      git,
      node,
      wallet: wallet
        ? {
            ...wallet,
            balanceRaw: balance?.raw,
            balanceFormatted: balance?.formattedANM,
          }
        : null,
      miner: { identity, live: minerLive },
      repo: repoSummary,
      patches,
    };
    process.stdout.write(safeStringify(payload, { indent: 2 }) + "\n");
    return 0;
  }

  header("Project");
  kv([
    ["root", paths.projectRoot],
    ["gitRepo", git.isRepo],
    ["branch", git.branch],
    ["dirty", git.dirty],
    ["files", repoSummary.totalFiles],
  ]);
  header("Network");
  kv([
    ["rpcUrl", config.rpcUrl],
    ["chainId(node)", node?.chainId?.toString() ?? "—"],
    ["blockNumber", node?.blockNumber?.toString() ?? "—"],
    ["client", node?.clientVersion ?? "—"],
  ]);
  header("Wallet");
  kv([
    ["mode", config.walletMode],
    ["address", wallet?.address],
    ["source", wallet?.source],
    ["balance", balance ? `${balance.formattedANM} ANM` : "—"],
  ]);
  header("Miner");
  kv([
    ["source", identity.source],
    ["payoutAddress", identity.payoutAddress],
    ["worker", identity.worker],
    ["poolUrl", identity.poolUrl],
    ["metricsReachable", minerLive?.metricsReachable],
    ["hashrate", minerLive?.hashrate],
  ]);
  header("Patches");
  kv([
    ["count", patches.count],
    ["latest", patches.latest],
  ]);
  void formatANM;
  return 0;
}
