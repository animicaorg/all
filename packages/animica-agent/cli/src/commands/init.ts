import {
  loadConfig,
  writeProjectConfig,
  type AgentConfig,
} from "@animica/agent-core";
import { existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";

import { boolFlag, stringFlag } from "../args.js";
import { ask, confirm } from "../prompt.js";
import { c, header, info, kv, ok } from "../output.js";

export interface InitOptions {
  cwd?: string;
  interactive?: boolean;
  rpcUrl?: string;
  walletMode?: AgentConfig["walletMode"];
  minerMode?: AgentConfig["minerMode"];
  approvalMode?: AgentConfig["approvalMode"];
  force?: boolean;
}

export async function runInit(opts: InitOptions = {}): Promise<number> {
  const { config: existing, paths } = loadConfig({ cwd: opts.cwd });
  const projectExists = existsSync(paths.projectFile);
  if (projectExists && !opts.force) {
    info(`Existing config: ${paths.projectFile}`);
    info("Use --force to overwrite. Showing current config:");
    showConfig(existing);
    return 0;
  }

  let next: AgentConfig = { ...existing };
  if (opts.rpcUrl) next.rpcUrl = opts.rpcUrl;
  if (opts.walletMode) next.walletMode = opts.walletMode;
  if (opts.minerMode) next.minerMode = opts.minerMode;
  if (opts.approvalMode) next.approvalMode = opts.approvalMode;

  if (opts.interactive) {
    header("Animica Coding Agent — init");
    next.rpcUrl = await ask("RPC URL", next.rpcUrl);
    next.chainId = await ask("Chain ID", next.chainId);
    next.explorerUrl = await ask("Explorer URL", next.explorerUrl);
    const walletMode = await ask("Wallet mode (off|readonly|extension|file)", next.walletMode);
    next.walletMode = (walletMode as AgentConfig["walletMode"]) || next.walletMode;
    const minerMode = await ask("Miner mode (off|auto|local|pool)", next.minerMode);
    next.minerMode = (minerMode as AgentConfig["minerMode"]) || next.minerMode;
    next.approvalMode = ((await ask("Approval mode (auto|ask|manual|never)", next.approvalMode)) as AgentConfig["approvalMode"]) || next.approvalMode;
    const allowShell = await confirm("Allow agent to run shell commands?", next.allowShell);
    next.allowShell = allowShell;
    const allowGit = await confirm("Allow agent to stage/commit git changes?", next.allowGitWrite);
    next.allowGitWrite = allowGit;
  }

  // Create state and project dirs.
  mkdirSync(paths.projectDir, { recursive: true });
  mkdirSync(paths.stateDir, { recursive: true });
  mkdirSync(join(paths.stateDir, "patches"), { recursive: true });

  // Add to .gitignore if a project repo and the path isn't already ignored.
  ensureGitIgnore(paths.projectRoot);

  const saved = writeProjectConfig(paths, next);

  ok(`Wrote ${paths.projectFile}`);
  info("");
  showConfig(saved);
  info("");
  info("Next steps:");
  info("  " + c.cyan("animica-agent doctor") + "    check environment health");
  info("  " + c.cyan("animica-agent status") + "    show current context");
  info("  " + c.cyan("animica-agent chat") + "      start an interactive coding session");
  return 0;
}

function showConfig(cfg: AgentConfig): void {
  kv([
    ["rpcUrl", cfg.rpcUrl],
    ["chainId", cfg.chainId],
    ["explorerUrl", cfg.explorerUrl],
    ["walletMode", cfg.walletMode],
    ["minerMode", cfg.minerMode],
    ["approvalMode", cfg.approvalMode],
    ["allowShell", cfg.allowShell],
    ["allowGitWrite", cfg.allowGitWrite],
    ["resourceMode", cfg.resourceMode],
    ["aicfMode", cfg.aicfMode],
    ["creditsMode", cfg.creditsMode],
    ["provider", cfg.provider ?? "offline"],
  ]);
}

function ensureGitIgnore(root: string): void {
  const path = join(root, ".gitignore");
  const marker = ".animica/agent-state/";
  let body = "";
  try {
    body = existsSync(path) ? require("node:fs").readFileSync(path, "utf8") : "";
  } catch {
    return;
  }
  if (body.includes(marker)) return;
  try {
    require("node:fs").appendFileSync(
      path,
      (body.endsWith("\n") || !body ? "" : "\n") +
        `# animica-agent\n${marker}\n.animica/agent-state\n`,
    );
  } catch {
    /* tolerate */
  }
}

export function parseInit(options: Record<string, string | boolean>): InitOptions {
  return {
    interactive: boolFlag(options, "interactive", false) || boolFlag(options, "i", false),
    rpcUrl: stringFlag(options, "rpc-url"),
    walletMode: stringFlag(options, "wallet-mode") as AgentConfig["walletMode"] | undefined,
    minerMode: stringFlag(options, "miner-mode") as AgentConfig["minerMode"] | undefined,
    approvalMode: stringFlag(options, "approval-mode") as AgentConfig["approvalMode"] | undefined,
    force: boolFlag(options, "force", false),
  };
}
