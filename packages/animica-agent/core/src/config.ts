/**
 * Layered configuration for the Animica Coding Agent.
 *
 * Precedence (highest wins):
 *   1. Explicit overrides (function arguments)
 *   2. Environment variables (ANIMICA_AGENT_* and a few well-known ANIMICA_* names)
 *   3. Project config:  <repoRoot>/.animica/agent.json
 *   4. User config:     ${ANIMICA_AGENT_HOME or $HOME/.animica}/agent.json
 *   5. Built-in defaults below
 *
 * We deliberately use JSON, not YAML, to avoid adding a runtime dep. The
 * config file is human-edited so we keep it small and rounded.
 */

import { existsSync, readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve, dirname } from "node:path";

import { ConfigError } from "./errors.js";
import { safeStringify, safeParse } from "./safe-json.js";

export type ApprovalMode = "auto" | "ask" | "manual" | "never";
export type WalletMode = "off" | "readonly" | "extension" | "file";
export type MinerMode = "off" | "auto" | "local" | "pool";
export type ResourceMode = "balanced" | "miner-priority" | "agent-priority";
export type AicfMode = "off" | "enabled";
export type CreditsMode = "off" | "wallet" | "miner" | "aicf" | "hybrid";
/**
 * Useful-work settlement mode.
 *   - "offline": receipts settle locally without an on-chain transfer (dev/test).
 *   - "live":    receipts settle via a real on-chain transfer through the
 *                configured signer; reserve enforcement and balance-aware
 *                payout guard are active by default.
 */
export type SettlementMode = "offline" | "live";
/**
 * Reserve enforcement policy.
 *   - "strict":  fetch signer balance and refuse payouts that would breach
 *                reserveBalanceRaw. Fails closed if balance lookup fails.
 *   - "off":     skip balance lookup entirely. ONLY for offline/dev mode.
 */
export type ReservePolicy = "strict" | "off";

export interface AgentConfig {
  /** JSON-RPC endpoint for the Animica node. */
  rpcUrl: string;
  /** Decimal or hex chain ID. Use a string to preserve large values; coerce to bigint on demand. */
  chainId: string;
  /** Public explorer URL used for "open explorer" actions. */
  explorerUrl: string;
  /** Wallet integration mode. */
  walletMode: WalletMode;
  /** Path to an on-disk wallet vault, ONLY used when walletMode === "file". */
  walletFile?: string;
  /** Active miner integration mode. */
  minerMode: MinerMode;
  /** Resolved or user-configured payout address; never auto-spent. */
  minerAddress?: string;
  /** Optional worker tag attributed to agent sessions for analytics. */
  workerName?: string;
  /** Stratum/pool URL when applicable. */
  poolUrl?: string;
  /** Workspace root (defaults to detected git root or cwd). */
  workspacePath: string;
  /** Default LLM model identifier; opaque to the agent core. */
  defaultModel: string;
  /** Patch/apply approval policy. */
  approvalMode: ApprovalMode;
  /** Whether the agent may execute shell commands. */
  allowShell: boolean;
  /** Whether the agent may stage/commit git changes. */
  allowGitWrite: boolean;
  /** UI theme hint. */
  agentTheme: "light" | "dark" | "auto";
  /** Mining-safe resource mode. */
  resourceMode: ResourceMode;
  /** Soft CPU cap (1-100 percent) used by resource-bound tasks. */
  cpuLimit: number;
  /** AICF integration mode. */
  aicfMode: AicfMode;
  /** Credits/eligibility model for agent jobs. */
  creditsMode: CreditsMode;
  /** Optional provider name (anthropic|openai|aicf|local|offline). */
  provider?: string;
  /** Provider-specific base URL (e.g., AICF endpoint). */
  providerBaseUrl?: string;
  /** Optional active profile label. */
  profile?: string;
  /** Useful-work settlement mode. Defaults to `offline`. */
  settlementMode?: SettlementMode;
  /** Reserve enforcement policy. Defaults to `strict` when settlementMode is
   *  `live`, `off` otherwise. */
  reservePolicy?: ReservePolicy;
}

export const DEFAULT_CONFIG: AgentConfig = {
  rpcUrl: "http://127.0.0.1:8545/rpc",
  chainId: "1",
  explorerUrl: "https://explorer.animica.org",
  walletMode: "readonly",
  minerMode: "auto",
  workspacePath: "",
  defaultModel: "claude-opus-4-7",
  approvalMode: "ask",
  allowShell: false,
  allowGitWrite: false,
  agentTheme: "auto",
  resourceMode: "balanced",
  cpuLimit: 75,
  aicfMode: "off",
  creditsMode: "off",
  provider: "offline",
  settlementMode: "offline",
  reservePolicy: "off",
};

export interface ConfigPaths {
  /** Resolved repo/project root, or cwd if no .git was found. */
  projectRoot: string;
  /** Per-project config directory: <projectRoot>/.animica */
  projectDir: string;
  /** Per-project agent config file. */
  projectFile: string;
  /** Per-user config directory. */
  userDir: string;
  /** Per-user agent config file. */
  userFile: string;
  /** Persistent agent state dir (sessions, patches, audit log). */
  stateDir: string;
}

export function getConfigPaths(cwd: string = process.cwd()): ConfigPaths {
  const projectRoot = findRepoRoot(cwd);
  const projectDir = join(projectRoot, ".animica");
  const projectFile = join(projectDir, "agent.json");
  const userDir = process.env.ANIMICA_AGENT_HOME
    ? resolve(process.env.ANIMICA_AGENT_HOME)
    : join(homedir(), ".animica");
  const userFile = join(userDir, "agent.json");
  const stateDir = join(projectDir, "agent-state");
  return { projectRoot, projectDir, projectFile, userDir, userFile, stateDir };
}

export function findRepoRoot(start: string): string {
  let cur = resolve(start);
  // Walk upward looking for .git or .animica markers; bail at filesystem root.
  for (let i = 0; i < 32; i++) {
    if (existsSync(join(cur, ".git")) || existsSync(join(cur, ".animica"))) return cur;
    const parent = dirname(cur);
    if (parent === cur) return resolve(start);
    cur = parent;
  }
  return resolve(start);
}

function readJsonIfExists(path: string): Partial<AgentConfig> | undefined {
  if (!existsSync(path)) return undefined;
  try {
    return safeParse<Partial<AgentConfig>>(readFileSync(path, "utf8"));
  } catch (err) {
    throw new ConfigError(`Failed to parse config at ${path}: ${(err as Error).message}`);
  }
}

/**
 * Best-effort discovery of an `animica-node` install on the same machine.
 * Read-only; never throws; returns null when no node.json is present.
 */
export function discoverLocalNode(): { rpcUrl: string; chainId: string } | null {
  const path = process.env.ANIMICA_NODE_CONFIG ?? join(homedir(), ".animica", "node", "node.json");
  if (!existsSync(path)) return null;
  try {
    const j = safeParse<{ rpcPort?: number; chainId?: number }>(readFileSync(path, "utf8"));
    const port = typeof j.rpcPort === "number" ? j.rpcPort : 8545;
    const chainId = typeof j.chainId === "number" ? String(j.chainId) : "1";
    return { rpcUrl: `http://127.0.0.1:${port}/rpc`, chainId };
  } catch {
    return null;
  }
}

function applyEnvOverrides(base: AgentConfig): AgentConfig {
  const env = process.env;
  const out = { ...base };
  if (env.ANIMICA_AGENT_RPC_URL) out.rpcUrl = env.ANIMICA_AGENT_RPC_URL;
  else if (env.ANIMICA_RPC_URL) out.rpcUrl = env.ANIMICA_RPC_URL;
  else if (env.ANIMICA_MINER_RPC_HTTP) out.rpcUrl = env.ANIMICA_MINER_RPC_HTTP;

  if (env.ANIMICA_AGENT_CHAIN_ID) out.chainId = env.ANIMICA_AGENT_CHAIN_ID;
  else if (env.ANIMICA_CHAIN_ID) out.chainId = env.ANIMICA_CHAIN_ID;
  else if (env.ANIMICA_MINER_CHAIN_ID) out.chainId = env.ANIMICA_MINER_CHAIN_ID;

  if (env.ANIMICA_AGENT_EXPLORER_URL) out.explorerUrl = env.ANIMICA_AGENT_EXPLORER_URL;

  if (env.ANIMICA_AGENT_WALLET_MODE)
    out.walletMode = env.ANIMICA_AGENT_WALLET_MODE as WalletMode;
  if (env.ANIMICA_AGENT_WALLET_FILE) out.walletFile = env.ANIMICA_AGENT_WALLET_FILE;

  if (env.ANIMICA_AGENT_MINER_MODE)
    out.minerMode = env.ANIMICA_AGENT_MINER_MODE as MinerMode;
  if (env.ANIMICA_AGENT_MINER_ADDRESS) out.minerAddress = env.ANIMICA_AGENT_MINER_ADDRESS;
  else if (env.ANIMICA_POOL_ADDRESS) out.minerAddress = env.ANIMICA_POOL_ADDRESS;
  if (env.ANIMICA_AGENT_WORKER_NAME) out.workerName = env.ANIMICA_AGENT_WORKER_NAME;
  if (env.ANIMICA_AGENT_POOL_URL) out.poolUrl = env.ANIMICA_AGENT_POOL_URL;

  if (env.ANIMICA_AGENT_WORKSPACE) out.workspacePath = env.ANIMICA_AGENT_WORKSPACE;
  if (env.ANIMICA_AGENT_MODEL) out.defaultModel = env.ANIMICA_AGENT_MODEL;
  if (env.ANIMICA_AGENT_APPROVAL_MODE)
    out.approvalMode = env.ANIMICA_AGENT_APPROVAL_MODE as ApprovalMode;
  if (env.ANIMICA_AGENT_ALLOW_SHELL) out.allowShell = parseBool(env.ANIMICA_AGENT_ALLOW_SHELL);
  if (env.ANIMICA_AGENT_ALLOW_GIT_WRITE)
    out.allowGitWrite = parseBool(env.ANIMICA_AGENT_ALLOW_GIT_WRITE);
  if (env.ANIMICA_AGENT_THEME)
    out.agentTheme = env.ANIMICA_AGENT_THEME as AgentConfig["agentTheme"];
  if (env.ANIMICA_AGENT_RESOURCE_MODE)
    out.resourceMode = env.ANIMICA_AGENT_RESOURCE_MODE as ResourceMode;
  if (env.ANIMICA_AGENT_CPU_LIMIT)
    out.cpuLimit = clampPercent(Number.parseInt(env.ANIMICA_AGENT_CPU_LIMIT, 10));
  if (env.ANIMICA_AGENT_AICF_MODE)
    out.aicfMode = env.ANIMICA_AGENT_AICF_MODE as AicfMode;
  if (env.ANIMICA_AGENT_CREDITS_MODE)
    out.creditsMode = env.ANIMICA_AGENT_CREDITS_MODE as CreditsMode;
  if (env.ANIMICA_AGENT_PROVIDER) out.provider = env.ANIMICA_AGENT_PROVIDER;
  if (env.ANIMICA_AGENT_PROVIDER_BASE_URL)
    out.providerBaseUrl = env.ANIMICA_AGENT_PROVIDER_BASE_URL;
  if (env.ANIMICA_AGENT_PROFILE) out.profile = env.ANIMICA_AGENT_PROFILE;
  if (env.ANIMICA_AGENT_SETTLEMENT_MODE)
    out.settlementMode = env.ANIMICA_AGENT_SETTLEMENT_MODE as SettlementMode;
  if (env.ANIMICA_AGENT_RESERVE_POLICY)
    out.reservePolicy = env.ANIMICA_AGENT_RESERVE_POLICY as ReservePolicy;
  return out;
}

function parseBool(v: string): boolean {
  const s = v.trim().toLowerCase();
  return s === "1" || s === "true" || s === "yes" || s === "on";
}

function clampPercent(n: number): number {
  if (!Number.isFinite(n)) return 75;
  return Math.max(1, Math.min(100, Math.round(n)));
}

export interface LoadConfigOptions {
  cwd?: string;
  overrides?: Partial<AgentConfig>;
}

export interface LoadedConfig {
  config: AgentConfig;
  paths: ConfigPaths;
  sources: {
    user: boolean;
    project: boolean;
    env: boolean;
    overrides: boolean;
  };
}

export function loadConfig(options: LoadConfigOptions = {}): LoadedConfig {
  const cwd = options.cwd ?? process.cwd();
  const paths = getConfigPaths(cwd);
  const userPartial = readJsonIfExists(paths.userFile);
  const projectPartial = readJsonIfExists(paths.projectFile);
  // Layer order: built-in defaults < animica-node discovery < user < project.
  const nodeDiscovery = discoverLocalNode();
  let cfg: AgentConfig = {
    ...DEFAULT_CONFIG,
    workspacePath: paths.projectRoot,
    ...(nodeDiscovery ? { rpcUrl: nodeDiscovery.rpcUrl, chainId: nodeDiscovery.chainId } : {}),
    ...(userPartial ?? {}),
    ...(projectPartial ?? {}),
  };
  const beforeEnv = JSON.stringify(cfg);
  cfg = applyEnvOverrides(cfg);
  const envChanged = JSON.stringify(cfg) !== beforeEnv;
  if (options.overrides) cfg = { ...cfg, ...options.overrides };
  if (!cfg.workspacePath) cfg.workspacePath = paths.projectRoot;
  return {
    config: cfg,
    paths,
    sources: {
      user: !!userPartial,
      project: !!projectPartial,
      env: envChanged,
      overrides: !!options.overrides,
    },
  };
}

export function writeProjectConfig(paths: ConfigPaths, patch: Partial<AgentConfig>): AgentConfig {
  mkdirSync(paths.projectDir, { recursive: true });
  const existing = readJsonIfExists(paths.projectFile) ?? {};
  const merged: AgentConfig = { ...DEFAULT_CONFIG, ...existing, ...patch };
  writeFileSync(paths.projectFile, safeStringify(merged, { indent: 2 }) + "\n", "utf8");
  return merged;
}

export function writeUserConfig(paths: ConfigPaths, patch: Partial<AgentConfig>): AgentConfig {
  mkdirSync(paths.userDir, { recursive: true });
  const existing = readJsonIfExists(paths.userFile) ?? {};
  const merged: AgentConfig = { ...DEFAULT_CONFIG, ...existing, ...patch };
  writeFileSync(paths.userFile, safeStringify(merged, { indent: 2 }) + "\n", "utf8");
  return merged;
}
