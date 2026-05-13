/**
 * animica-node CLI entry.
 *
 * Each top-level verb here corresponds to a real operator workflow. We do
 * not implement node behavior — we control the Python runtime that already
 * ships with the Animica repo. That keeps the npm package thin, reliable,
 * and free of behavioral regressions.
 */

import {
  balanceLink,
  configSet,
  configShow,
  doctor,
  exposedDiscovery,
  init,
  logs,
  minerStatus,
  peers,
  reset,
  restart,
  rpcPassthrough,
  start,
  status,
  stop,
  syncStatus,
  walletPath,
} from "./commands.js";
import {
  ensureRuntimeOrInstall,
  runInstallRuntime,
  runRuntimeDoctor,
  runRuntimePrune,
  runRuntimeRemove,
  runRuntimeRepair,
  runRuntimeStatus,
} from "./runtime-commands.js";

const VERSION = "0.1.0";

const HELP = `animica-node ${VERSION} — Animica full-node operator CLI

USAGE
  animica-node <command> [args]

CORE
  init                          Generate / refresh node.json
  start [-f|--foreground] [--no-install]
                                Start the node (detached by default; auto-installs runtime)
  stop                          Stop the running node
  restart                       Stop + start
  status                        Show pidfile, RPC, sync state
  logs [-f]                     Tail node logs
  doctor                        Health checks (rpc, backend, miner-safety)
  reset --yes                   Wipe the configured data dir

RUNTIME
  install-runtime [--manifest-url <url>] [--force]
                                Install the managed Animica node runtime
  runtime status                Where the runtime lives, which version is active
  runtime repair                Verify all installs have the marker + entry
  runtime remove --all | --channel <c> --version <v>
                                Delete one or all installed runtimes
  runtime prune [--keep N]      Drop old installs, keep most recent N + active
  runtime doctor                Manifest reachability + backend resolution

CHAIN
  rpc call <method> [params…]   Pass-through JSON-RPC call
  sync status                   One-liner sync state
  peers                         List connected peers

CONFIG
  config show                   Print effective node.json
  config set <k=v> [k=v…]       Update node.json
  wallet path                   Print the wallet directory used by the node

INTEGRATION
  miner status                  Show miner-aware node hints
  balance [addr]                Suggested agent flow for balance lookup
  discovery                     JSON used by animica-agent to align RPC

ENV
  ANIMICA_NODE_BIN              Override the backend binary
  ANIMICA_NODE_HOME             Override the daemon state dir
  ANIMICA_NODE_CONFIG           Override the config file path
  ANIMICA_RUNTIME_HOME          Override ~/.animica/runtime/ install root
  ANIMICA_RUNTIME_MANIFEST_URL  Override the manifest URL for install-runtime
`;

export async function run(argv: string[]): Promise<number> {
  const args = argv.slice(0);
  if (args.length === 0 || args[0] === "--help" || args[0] === "-h" || args[0] === "help") {
    process.stdout.write(HELP);
    return 0;
  }
  if (args[0] === "--version" || args[0] === "version") {
    process.stdout.write(VERSION + "\n");
    return 0;
  }
  const head = args[0];
  const rest = args.slice(1);
  let r: { exit: number; output: string };
  try {
    if (head === "init") r = init();
    else if (head === "install-runtime") r = await runInstallRuntime(rest);
    else if (head === "runtime" && rest[0] === "status") r = runRuntimeStatus(rest.slice(1));
    else if (head === "runtime" && rest[0] === "repair") r = runRuntimeRepair(rest.slice(1));
    else if (head === "runtime" && rest[0] === "remove") r = runRuntimeRemove(rest.slice(1));
    else if (head === "runtime" && rest[0] === "prune") r = runRuntimePrune(rest.slice(1));
    else if (head === "runtime" && rest[0] === "doctor") r = await runRuntimeDoctor(rest.slice(1));
    else if (head === "start") {
      // Auto-install the runtime if no managed/env/PATH backend resolves.
      const noInstall = rest.includes("--no-install");
      const ensured = await ensureRuntimeOrInstall({ autoInstall: !noInstall });
      if (!ensured.ok) {
        r = { exit: 1, output: `error: ${ensured.message ?? "no backend available"}\n` };
      } else {
        r = await start(rest);
      }
    }
    else if (head === "stop") r = stop();
    else if (head === "restart") r = await restart(rest);
    else if (head === "status") r = await status();
    else if (head === "doctor") r = await doctor();
    else if (head === "logs") r = logs(rest);
    else if (head === "rpc") r = await rpcPassthrough(rest);
    else if (head === "sync" && rest[0] === "status") r = await syncStatus();
    else if (head === "peers") r = await peers();
    else if (head === "reset") r = reset(rest);
    else if (head === "config" && rest[0] === "show") r = configShow();
    else if (head === "config" && rest[0] === "set") r = configSet(rest.slice(1));
    else if (head === "wallet" && rest[0] === "path") r = walletPath();
    else if (head === "miner" && rest[0] === "status") r = await minerStatus();
    else if (head === "balance") r = balanceLink(rest[0]);
    else if (head === "discovery") {
      const d = exposedDiscovery();
      r = { exit: 0, output: JSON.stringify(d, null, 2) + "\n" };
    } else {
      process.stderr.write(`unknown command: ${head}\n${HELP}`);
      return 64;
    }
  } catch (e) {
    process.stderr.write(`error: ${(e as Error).message}\n`);
    return 1;
  }
  if (r.output) process.stdout.write(r.output);
  return r.exit;
}

export { exposedDiscovery };
