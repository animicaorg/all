#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { existsSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const PKG_DIR = resolve(SCRIPT_DIR, "..");
const IS_WIN = process.platform === "win32";

const NODE_SUBCOMMANDS = new Set([
  "install-runtime",
  "start",
  "stop",
  "status",
  "doctor",
  "sync",
  "peers",
  "rpc",
  "init",
  "restart",
  "logs",
  "reset",
  "runtime",
  "config",
]);

const AGENT_SUBCOMMANDS = new Set([
  "code",
  "scaffold",
  "wallet",
  "miner",
  "mine",
  "ui",
  "setup",
  "chat",
  "diff",
  "apply",
  "rollback",
  "patches",
  "balance",
  "pricing",
  "budget",
  "estimate",
  "receipts",
  "allowance",
  "jobs",
  "rewards",
  "leaderboard",
  "adapters",
]);

function printHelp() {
  process.stdout.write(`animica 0.1.12 — Animica unified CLI

Installs and exposes the full Animica toolchain through a single command.

USAGE
  animica <command> [args]

NODE COMMANDS  (forwarded to animica-node)
  animica node <subcmd>           Pass-through to animica-node
  animica install-runtime [args]  Install the managed Animica runtime
  animica start [args]            Start the local full node
  animica stop                    Stop the running node
  animica status                  Show pidfile, RPC, sync state
  animica doctor                  Health checks
  animica sync status             Sync state
  animica peers                   List connected peers
  animica rpc call <method> ...   JSON-RPC pass-through

AGENT COMMANDS  (forwarded to animica-agent)
  animica agent <subcmd>          Pass-through to animica-agent
  animica setup                   Guided first-run flow
  animica code "<task>"           One-shot coding task
  animica scaffold ...            Project scaffolding
  animica wallet ...              Wallet management
  animica miner ...               Useful-work miner
  animica ui                      Open the local agent dashboard

OTHER
  animica --help, -h              Show this help
  animica --version, -v           Show version

INCLUDED PACKAGES
  animica-node           Full-node operator
  animica-agent          Coding agent CLI
  @animica/agent-core    Shared core library
  @animica/agent-sdk     Typed SDK
  @animica/agent-ui      Local browser dashboard

After install, the underlying binaries are also available directly:
  animica-node --help
  animica-agent --help
`);
}

function printVersion() {
  process.stdout.write("animica 0.1.12\n");
}

function resolveBin(name) {
  const localCandidates = IS_WIN
    ? [
        resolve(PKG_DIR, "node_modules", ".bin", `${name}.cmd`),
        resolve(PKG_DIR, "node_modules", ".bin", `${name}.ps1`),
        resolve(PKG_DIR, "node_modules", ".bin", name),
      ]
    : [resolve(PKG_DIR, "node_modules", ".bin", name)];

  for (const candidate of localCandidates) {
    try {
      if (existsSync(candidate) && statSync(candidate).isFile()) {
        return { command: candidate, useShell: IS_WIN && candidate.endsWith(".cmd") };
      }
    } catch {
      // ignore
    }
  }

  return { command: name, useShell: IS_WIN };
}

function forward(targetBin, args) {
  const { command, useShell } = resolveBin(targetBin);
  const result = spawnSync(command, args, {
    stdio: "inherit",
    shell: useShell,
  });

  if (result.error) {
    if (result.error.code === "ENOENT") {
      process.stderr.write(
        `[animica] cannot find '${targetBin}'. Try: npm install -g animica\n`,
      );
      return 127;
    }
    process.stderr.write(`[animica] failed to launch '${targetBin}': ${result.error.message}\n`);
    return 1;
  }

  if (typeof result.status === "number") return result.status;
  if (result.signal) {
    process.stderr.write(`[animica] '${targetBin}' terminated by signal ${result.signal}\n`);
    return 1;
  }
  return 0;
}

function main(argv) {
  if (argv.length === 0) {
    printHelp();
    return 0;
  }

  const [first, ...rest] = argv;

  if (first === "--help" || first === "-h" || first === "help") {
    printHelp();
    return 0;
  }
  if (first === "--version" || first === "-v") {
    printVersion();
    return 0;
  }

  if (first === "node") {
    return forward("animica-node", rest);
  }
  if (first === "agent") {
    return forward("animica-agent", rest);
  }

  if (NODE_SUBCOMMANDS.has(first)) {
    return forward("animica-node", [first, ...rest]);
  }
  if (AGENT_SUBCOMMANDS.has(first)) {
    return forward("animica-agent", [first, ...rest]);
  }

  process.stderr.write(`[animica] unknown command: ${first}\n\n`);
  printHelp();
  return 1;
}

const code = main(process.argv.slice(2));
process.exit(code ?? 0);
