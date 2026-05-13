/**
 * `animica-agent setup` — guided happy-path.
 *
 * Walks an end user from a blank machine to an open coding-agent prompt:
 *   1. ensure prerequisites
 *   2. ensure local node config / running node (best-effort)
 *   3. ensure a wallet label exists (default: main)
 *   4. show funding instructions if the wallet is unfunded
 *   5. verify RPC + wallet readiness
 *   6. launch the dashboard if everything is ready
 *
 * Progress is persisted to <stateDir>/setup-state.json so an interrupted
 * setup can be resumed without re-prompting the user for choices.
 *
 * Never spends. Never silently flips settlementMode. Live payouts still
 * require the explicit ack flag elsewhere.
 */

import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import {
  fetchBalance,
  isLikelyAnimicaAddress,
  loadConfig,
  probeNode,
  resolveWalletIdentity,
  safeParse,
  safeStringify,
  writeProjectConfig,
} from "@animica/agent-core";

import { boolFlag, stringFlag } from "../args.js";
import { c, fail, header, info, kv, ok, warn } from "../output.js";
import { runLaunch } from "./launch.js";

const SETUP_STATE_VERSION = 1;

export interface SetupState {
  version: number;
  startedAt: string;
  completedSteps: string[];
  walletLabel?: string;
  walletAddress?: string;
  lastError?: { step: string; message: string };
}

function setupStatePath(stateDir: string): string {
  return join(stateDir, "setup-state.json");
}

function loadSetupState(stateDir: string): SetupState {
  const p = setupStatePath(stateDir);
  if (!existsSync(p)) {
    return { version: SETUP_STATE_VERSION, startedAt: new Date().toISOString(), completedSteps: [] };
  }
  try {
    return safeParse<SetupState>(readFileSync(p, "utf8"));
  } catch {
    return { version: SETUP_STATE_VERSION, startedAt: new Date().toISOString(), completedSteps: [] };
  }
}

function saveSetupState(stateDir: string, state: SetupState): void {
  mkdirSync(stateDir, { recursive: true });
  writeFileSync(setupStatePath(stateDir), safeStringify(state, { indent: 2 }) + "\n", "utf8");
}

function markStep(state: SetupState, name: string): void {
  if (!state.completedSteps.includes(name)) state.completedSteps.push(name);
}

function pythonAvailable(): boolean {
  const python = process.env.ANIMICA_AGENT_PYTHON ?? "python3";
  const r = spawnSync(python, ["-c", "import sys; sys.exit(0)"], { encoding: "utf8" });
  return r.status === 0;
}

function runPythonWallet(args: string[]) {
  const python = process.env.ANIMICA_AGENT_PYTHON ?? "python3";
  return spawnSync(python, ["-m", "animica.cli.main", "wallet", ...args], { encoding: "utf8" });
}

function extractAddress(text: string): string | undefined {
  const m = text.match(/(anm1[0-9ac-hj-np-z]{8,})/i);
  return m ? m[1].toLowerCase() : undefined;
}

export interface SetupOptions {
  /** Skip auto-launch at the end (just exit when ready). */
  noLaunch?: boolean;
  /** Wallet label to provision. Defaults to `main`. */
  label?: string;
  /** Skip Python wallet provisioning (advanced; assumes external setup). */
  skipWallet?: boolean;
  /** Continue from the last persisted state if one exists. */
  resume?: boolean;
  /** Force re-running every step even if previously completed. */
  reset?: boolean;
  /** Suppress browser auto-open at the end. */
  noBrowser?: boolean;
}

export async function runSetup(options: Record<string, string | boolean>): Promise<number> {
  const opts: SetupOptions = {
    noLaunch: boolFlag(options, "no-launch", false),
    label: (stringFlag(options, "label") ?? "main") as string,
    skipWallet: boolFlag(options, "skip-wallet", false),
    resume: !boolFlag(options, "reset", false),
    reset: boolFlag(options, "reset", false),
    noBrowser: boolFlag(options, "no-browser", false),
  };
  const { config, paths } = loadConfig();
  const state = opts.reset
    ? { version: SETUP_STATE_VERSION, startedAt: new Date().toISOString(), completedSteps: [] }
    : loadSetupState(paths.stateDir);
  state.walletLabel = state.walletLabel ?? opts.label ?? "main";
  saveSetupState(paths.stateDir, state);

  header("Animica setup");
  info(c.dim(`progress will resume from ${setupStatePath(paths.stateDir)}`));
  info("");

  // Step 1 — prerequisites.
  if (!state.completedSteps.includes("prereq") || opts.reset) {
    info(c.bold("Step 1/6  ") + "checking prerequisites");
    const pyOk = pythonAvailable();
    kv([
      ["node.js", process.version],
      ["python", pyOk ? "ok" : "missing"],
      ["platform", `${process.platform}-${process.arch}`],
    ]);
    if (!pyOk && !opts.skipWallet) {
      state.lastError = { step: "prereq", message: "python interpreter not found" };
      saveSetupState(paths.stateDir, state);
      fail("Python 3 is required for wallet operations.");
      info(c.dim("  Install python 3.10+ and re-run `animica-agent setup`, or pass --skip-wallet to defer."));
      return 1;
    }
    markStep(state, "prereq");
    saveSetupState(paths.stateDir, state);
    ok("prerequisites look good");
    info("");
  }

  // Step 2 — node reachability.
  if (!state.completedSteps.includes("node") || opts.reset) {
    info(c.bold("Step 2/6  ") + "checking local node");
    const node = await probeNode(config.rpcUrl).catch(() => null);
    if (!node?.reachable) {
      warn(`RPC ${config.rpcUrl} is not reachable.`);
      info(c.dim("  Start a local node with:"));
      info(c.dim("    npx animica-node init && npx animica-node start"));
      info(c.dim("  Then re-run `animica-agent setup` to resume."));
      state.lastError = { step: "node", message: `RPC unreachable at ${config.rpcUrl}` };
      saveSetupState(paths.stateDir, state);
      return 1;
    }
    kv([
      ["rpcUrl", config.rpcUrl],
      ["chainId", node.chainId?.toString() ?? "—"],
      ["blockNumber", node.blockNumber?.toString() ?? "—"],
    ]);
    markStep(state, "node");
    saveSetupState(paths.stateDir, state);
    ok("local node reachable");
    info("");
  }

  // Step 3 — wallet provisioning.
  if (!state.completedSteps.includes("wallet") && !opts.skipWallet) {
    info(c.bold("Step 3/6  ") + "ensuring wallet");
    const label = state.walletLabel ?? "main";
    // Address probe (if exists).
    const probe = runPythonWallet(["address", "--label", label]);
    let address = probe.status === 0 ? extractAddress(probe.stdout + "\n" + probe.stderr) : undefined;
    if (!address) {
      info(c.dim(`creating wallet label "${label}" via the Python CLI`));
      const create = runPythonWallet(["new", "--label", label]);
      if (create.status !== 0) {
        state.lastError = { step: "wallet", message: `wallet new --label ${label} failed (exit ${create.status})` };
        saveSetupState(paths.stateDir, state);
        fail(`wallet creation failed (exit ${create.status}).`);
        info(c.dim((create.stderr || create.stdout).trim()));
        return 1;
      }
      address = extractAddress(create.stdout + "\n" + create.stderr);
      if (!address) {
        // Re-probe.
        const probe2 = runPythonWallet(["address", "--label", label]);
        address = probe2.status === 0 ? extractAddress(probe2.stdout + "\n" + probe2.stderr) : undefined;
      }
    }
    if (!address || !isLikelyAnimicaAddress(address)) {
      state.lastError = { step: "wallet", message: "wallet address could not be parsed" };
      saveSetupState(paths.stateDir, state);
      fail("Could not resolve wallet address. Try `animica-agent wallet address main`.");
      return 1;
    }
    state.walletAddress = address;
    state.walletLabel = label;
    writeProjectConfig(paths, { minerAddress: address, walletLabel: label } as never);
    kv([
      ["label", label],
      ["address", address],
    ]);
    markStep(state, "wallet");
    saveSetupState(paths.stateDir, state);
    ok("wallet ready");
    info("");
  }

  // Step 4 — funding visibility (advisory only).
  if (!state.completedSteps.includes("funding-check") || opts.reset) {
    info(c.bold("Step 4/6  ") + "checking wallet balance");
    const cfgNow = loadConfig().config;
    const wallet = resolveWalletIdentity(cfgNow);
    if (wallet?.address) {
      const b = await fetchBalance(cfgNow.rpcUrl, wallet.address).catch(() => null);
      kv([
        ["address", wallet.address],
        ["balance", b ? `${b.formattedANM} ANM` : "—"],
        ["raw", b ? b.raw.toString() : "—"],
      ]);
      if (!b || b.raw === 0n) {
        warn("wallet is unfunded.");
        info("  To enable paid actions:");
        info(`    1. Send ANM to ${c.cyan(wallet.address)} from any funded source.`);
        info("    2. Re-run `animica-agent setup` to verify funds landed.");
        info(c.dim("    The agent works for read-only flows even while unfunded."));
        // Funding is NOT a hard blocker — we mark the step done so the user
        // can still proceed to the dashboard.
      } else {
        ok("wallet has funds.");
      }
    }
    markStep(state, "funding-check");
    saveSetupState(paths.stateDir, state);
    info("");
  }

  // Step 5 — readiness summary.
  if (!state.completedSteps.includes("readiness") || opts.reset) {
    info(c.bold("Step 5/6  ") + "readiness summary");
    const cfgNow = loadConfig().config;
    const node = await probeNode(cfgNow.rpcUrl).catch(() => null);
    const wallet = resolveWalletIdentity(cfgNow);
    const balance = wallet?.address && node?.reachable
      ? await fetchBalance(cfgNow.rpcUrl, wallet.address).catch(() => null)
      : null;
    kv([
      ["rpcReachable", node?.reachable ? "yes" : "no"],
      ["chainIdMatches", node?.chainId?.toString() === cfgNow.chainId ? "yes" : "no"],
      ["walletConfigured", wallet ? "yes" : "no"],
      ["walletFunded", balance ? (balance.raw > 0n ? "yes" : "no") : "unknown"],
      ["settlementMode", cfgNow.settlementMode ?? "offline"],
    ]);
    markStep(state, "readiness");
    saveSetupState(paths.stateDir, state);
    info("");
  }

  // Step 6 — launch (or print next steps).
  markStep(state, "setup-complete");
  saveSetupState(paths.stateDir, state);
  ok("setup complete");
  if (opts.noLaunch) {
    info(c.dim("Run `animica-agent` to open the dashboard when you're ready."));
    return 0;
  }
  info("");
  info(c.bold("Step 6/6  ") + "launching dashboard…");
  return runLaunch({ "no-browser": opts.noBrowser ?? false });
}

export function runSetupStatus(options: Record<string, string | boolean>): number {
  const { paths } = loadConfig();
  const state = loadSetupState(paths.stateDir);
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(state, { indent: 2 }) + "\n");
    return 0;
  }
  header("Setup state");
  kv([
    ["startedAt", state.startedAt],
    ["walletLabel", state.walletLabel ?? "—"],
    ["walletAddress", state.walletAddress ?? "—"],
    ["completedSteps", state.completedSteps.join(",") || "(none)"],
    ["lastError", state.lastError ? `${state.lastError.step}: ${state.lastError.message}` : "—"],
  ]);
  return 0;
}
