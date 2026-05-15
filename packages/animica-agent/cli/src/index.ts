/**
 * animica-agent CLI router.
 *
 * Top-level commands are dispatched here. Each command lives in its own
 * module under `./commands/` so the surface is easy to audit and test.
 */

import { parseArgs } from "./args.js";
import { c, info } from "./output.js";

import { parseInit, runInit } from "./commands/init.js";
import { runDoctor } from "./commands/doctor.js";
import { runStatus } from "./commands/status.js";
import { runChat, runApply, runCode, runDiff, runPatches, runRollback } from "./commands/code.js";
import { runRpc } from "./commands/rpc.js";
import {
  runBalance,
  runWalletAddress,
  runWalletConnect,
  runWalletCreate,
  runWalletFundHelp,
  runWalletList,
} from "./commands/wallet.js";
import { runMinerConnect, runMinerStatus } from "./commands/miner.js";
import { runAllowance, runBudget, runEstimate, runPricing, runReceipts } from "./commands/billing.js";
import { runScaffold } from "./commands/scaffold.js";
import { runAdapters, runJobs, runLeaderboard, runRewards } from "./commands/jobs.js";
import { runRelease } from "./commands/release.js";
import { runUI } from "./commands/ui.js";
import { runSettlementCheck, runWaitConfirm } from "./commands/settlement.js";
import { runMinerStart, runMinerStop, runMinerRuntimeStatus } from "./commands/miner-runtime.js";
import { runReceiptShow, runRewardsRollup, runSettlementReady } from "./commands/receipts-show.js";
import {
  runCoordinatorDoctor,
  runDoctorUsefulWork,
  runHybridPlan,
  runJournalArchive,
  runJournalCompact,
  runJournalInspect,
  runMetrics,
  runPayoutAudit,
  runSettlementList,
  runSettlementResume,
  runSettlementShow,
} from "./commands/useful-work-ops.js";
import {
  runSettlementInspect,
  runSettlementPending,
  runSettlementReconcile,
  runSettlementSubmitLive,
  runSettlementVerifyLive,
  runSettlementWatch,
} from "./commands/live-settlement.js";
import {
  runCoordinatorFetchSample,
  runCoordinatorFreshness,
  runCoordinatorLatest,
  runCoordinatorQueue,
  runCoordinatorQueueReplay,
  runCoordinatorSubmitFixture,
  runCoordinatorVerifyHistory,
  runCoordinatorVerifyLive,
} from "./commands/coordinator-verify.js";
import { runUsefulWorkReadiness } from "./commands/readiness.js";
import { runGoLive, runUsefulWorkSnapshot } from "./commands/go-live.js";
import { runLaunch, runOpen } from "./commands/launch.js";
import { runSetup, runSetupStatus } from "./commands/setup.js";
import {
  runMineStart,
  runMineStatus,
  runNodeLogs,
  runNodeSetup,
  runNodeStart,
  runNodeStatus,
} from "./commands/node-passthrough.js";

const VERSION = "0.1.11";

const HELP = `animica-agent ${VERSION} — Animica Coding Agent CLI

USAGE
  animica-agent                       Launch the coding agent dashboard (UI; falls back to chat)
  animica-agent setup                 Guided first-run flow (prereqs → node → wallet → launch)
  animica-agent <command> [args] [options]

COMMANDS
  (none)                     Same as 'animica-agent start': open the dashboard
  start                      Start the dashboard (alias of running with no args)
  open                       Open an already-running dashboard in the browser
  setup                      Guided first-run: prereqs, node, wallet, funding, launch
  setup status               Show persisted setup progress

  init                       Create or refresh project config
  doctor                     Diagnose environment, RPC, miner, wallet
  status                     Show current context (project, network, wallet, miner)
  chat                       Interactive coding session (TTY)
  code "<task>"              One-shot task; produces a plan and a pending patch
  diff                       Show the pending patch
  apply                      Apply the pending patch (after preview)
  rollback                   Restore files from the most recent applied patch
  patches                    List applied patches

  rpc call <method> [param…] Call any JSON-RPC method on the configured node
  wallet connect [addr]      Configure / inspect wallet identity
  wallet create <label>      Create a wallet (delegates to Python CLI)
  wallet address [label]     Show the wallet address (default label: main)
  wallet list                List wallet labels known to the Python CLI
  wallet fund-help [label]   Funding instructions + balance check
  balance [addr]             Read ANM balance from the configured RPC

  node setup                 Initialize a local node (delegates to animica-node)
  node start                 Start the local node (no-op if already reachable)
  node status                Show local node connectivity + chain id
  node logs [-f]             Tail node logs

  mine start [--once]        Start useful-work mining (alias of 'miner start')
  mine status                Show useful-work miner status

  miner connect [addr]       Connect or configure miner identity
  miner status               Show miner-linked identity + resource plan
  miner start [--once]       Run the useful-work miner (daemon by default)
  miner stop                 Signal a running miner to drain and exit
  miner runtime              Show useful-work job lifecycle + rewards rollup

  pricing                    Show current pricing table
  budget [show|set|reset-session]
                             Manage session/daily/monthly ANM caps
  estimate <kind>            Estimate cost for an action
  receipts [list|export|show <id>]
                             Receipt history (and per-receipt inspection)
  allowance [list|grant|revoke]
                             Manage delegated spending allowances

  jobs list|accept|submit    Useful-work job board (AICF/local)
  rewards                    Show miner rewards
  leaderboard                Show miner leaderboard
  adapters                   Show available adapters

  contract scaffold <name>   Generate a contract project
  dapp     scaffold <name>   Generate a dapp project
  token    scaffold <name>   Generate a token issuance config
  aicf-agent scaffold <name> Generate an AICF agent template

  release                    Build + test + npm pack --dry-run
  ui [--port 4720]           Start the local browser dashboard
  settlement [check]         Pre-flight checks before on-chain charging
  settlement list|show|resume|ready
                             Inspect or resume the hardened settlement queue
  settlement verify-live <receiptId>
                             Operator-safe dry-run; never broadcasts
  settlement submit-live <receiptId> --i-understand-this-spends-real-funds
                             Persist + broadcast + drive the engine forward
  settlement watch [<id>…]   Drive in-flight settlements one step; safe to repeat
  settlement pending         List non-terminal settlement attempts
  settlement inspect <id>    Full state-transition history for one receipt
  settlement reconcile [<id>…] [--rebroadcast]
                             Walk journal, drive every in-flight attempt to terminal-or-stall
  confirm <txHash>           Wait for an on-chain confirmation

  journal compact|archive|inspect
                             Operator journal maintenance
  metrics                    Snapshot of counters, jobs, settlements, revenue
  doctor useful-work         Go/no-go report for the useful-work miner
  hybrid plan                Show the hybrid mining decision
  coordinator doctor --url   Validate a remote coordinator
  coordinator verify-live --url
                             Full schema + queue self-test, persisted report
  coordinator fetch-sample --url
                             Read-only smoke check (list a few jobs)
  coordinator submit-fixture --url --job-id
                             Run a fixture submission against a real coordinator
  coordinator queue          Inspect the offline submission queue
  coordinator queue-replay --url
                             Drain the offline submission queue
  coordinator history        Show recent verify-live reports
  coordinator latest [--url] Show the most recent verify-live verdict
  coordinator freshness [--url] [--window-ms N]
                             Pass/fail check on the freshness of the latest verify-live
  payout audit               Show recent policy decisions
  useful-work readiness      Aggregate go/no-go gate (wallet, balance, coordinator, ...)
  useful-work go-live        Strict pre-live-payout checklist (exit !=0 on any blocker)
  useful-work snapshot       Compact JSON-safe status snapshot for dashboards

GLOBAL OPTIONS
  --json                Output structured JSON where supported
  --verbose             Log debug-level info
  --rpc-url <url>       Override RPC URL
  --resource-mode <m>   balanced | miner-priority | agent-priority
  --version             Print version
  --help                Show this help

ENV
  ANIMICA_AGENT_RPC_URL, ANIMICA_AGENT_PROFILE, ANIMICA_AGENT_PROVIDER,
  ANIMICA_AGENT_APPROVAL_MODE, ANIMICA_AGENT_RESOURCE_MODE,
  ANIMICA_MINER_*, ANIMICA_POOL_*  (read; never written)
`;

/** Known multi-token command prefixes. Any tokens beyond these become positionals. */
const TWO_TOKEN_PREFIXES = new Set([
  "rpc call",
  "wallet connect",
  "wallet create",
  "wallet address",
  "wallet list",
  "wallet fund-help",
  "node setup",
  "node start",
  "node status",
  "node logs",
  "mine start",
  "mine status",
  "setup status",
  "miner connect",
  "miner status",
  "contract scaffold",
  "dapp scaffold",
  "token scaffold",
  "aicf-agent scaffold",
  "settlement check",
  "settlement ready",
  "settlement submit",
  "settlement list",
  "settlement show",
  "settlement resume",
  "settlement verify-live",
  "settlement submit-live",
  "settlement watch",
  "settlement dry-run",
  "settlement pending",
  "settlement inspect",
  "settlement reconcile",
  "miner start",
  "miner stop",
  "miner runtime",
  "receipts show",
  "receipts list",
  "journal compact",
  "journal archive",
  "journal inspect",
  "doctor useful-work",
  "hybrid plan",
  "coordinator doctor",
  "coordinator verify-live",
  "coordinator fetch-sample",
  "coordinator submit-fixture",
  "coordinator queue",
  "coordinator queue-replay",
  "coordinator history",
  "coordinator latest",
  "coordinator freshness",
  "payout audit",
  "useful-work readiness",
  "useful-work go-live",
  "useful-work snapshot",
]);

function splitCommandAndPositionals(command: string[], positionals: string[]): { command: string[]; positionals: string[] } {
  // Try a 2-token prefix first (e.g. "rpc call"), then fall back to 1-token.
  if (command.length >= 2 && TWO_TOKEN_PREFIXES.has(`${command[0]} ${command[1]}`)) {
    const rest = command.slice(2);
    return { command: command.slice(0, 2), positionals: [...rest, ...positionals] };
  }
  if (command.length >= 1) {
    const rest = command.slice(1);
    return { command: command.slice(0, 1), positionals: [...rest, ...positionals] };
  }
  return { command, positionals };
}

function pathMatches(command: string[], ...want: string[]): boolean {
  if (command.length !== want.length) return false;
  for (let i = 0; i < want.length; i++) if (command[i] !== want[i]) return false;
  return true;
}

export async function run(argv: string[]): Promise<number> {
  const parsed = parseArgs(argv);
  const split = splitCommandAndPositionals(parsed.command, parsed.positionals);
  const { options } = parsed;
  const { command, positionals } = split;

  if (options.version === true || command[0] === "version") {
    info(VERSION);
    return 0;
  }
  if (options.help === true || command[0] === "help") {
    info(HELP);
    return 0;
  }
  // No subcommand → launch UI/chat happy path. Use --help to print HELP.
  if (command.length === 0) {
    if (options["print-help"] === true) {
      info(HELP);
      return 0;
    }
    return await runLaunch(options);
  }
  // Allow `--rpc-url` to flow into config via env shim for the duration of this run.
  if (typeof options["rpc-url"] === "string") process.env.ANIMICA_AGENT_RPC_URL = options["rpc-url"];
  if (typeof options["resource-mode"] === "string")
    process.env.ANIMICA_AGENT_RESOURCE_MODE = options["resource-mode"];

  try {
    // ----- top-level -----
    if (pathMatches(command, "init")) return await runInit(parseInit(options));
    if (pathMatches(command, "doctor")) return await runDoctor();
    if (pathMatches(command, "status")) return await runStatus(options);
    if (pathMatches(command, "chat")) return await runChat(options);
    if (pathMatches(command, "code")) return await runCode(positionals, options);
    if (pathMatches(command, "diff")) return runDiff();
    if (pathMatches(command, "apply")) return await runApply(options);
    if (pathMatches(command, "rollback")) return runRollback();
    if (pathMatches(command, "patches")) return runPatches();

    if (pathMatches(command, "rpc", "call")) return await runRpc(["call", ...positionals], options);
    if (pathMatches(command, "rpc")) return await runRpc(positionals, options);

    if (pathMatches(command, "wallet", "connect")) return await runWalletConnect(positionals, options);
    if (pathMatches(command, "wallet", "create")) return await runWalletCreate(positionals, options);
    if (pathMatches(command, "wallet", "address")) return await runWalletAddress(positionals, options);
    if (pathMatches(command, "wallet", "list")) return await runWalletList(options);
    if (pathMatches(command, "wallet", "fund-help")) return await runWalletFundHelp(positionals, options);
    if (pathMatches(command, "balance")) return await runBalance(positionals, options);

    if (pathMatches(command, "setup")) return await runSetup(options);
    if (pathMatches(command, "setup", "status")) return runSetupStatus(options);
    if (pathMatches(command, "start")) return await runLaunch(options); // alias of no-args launch
    if (pathMatches(command, "open")) return await runOpen(options);

    if (pathMatches(command, "node", "setup")) return runNodeSetup(options);
    if (pathMatches(command, "node", "start")) return await runNodeStart(options);
    if (pathMatches(command, "node", "status")) return await runNodeStatus(options);
    if (pathMatches(command, "node", "logs")) return runNodeLogs(options);
    if (pathMatches(command, "node")) return await runNodeStatus(options);

    if (pathMatches(command, "mine", "start")) return runMineStart(options);
    if (pathMatches(command, "mine", "status")) return runMineStatus(options);
    if (pathMatches(command, "mine")) return runMineStatus(options);

    if (pathMatches(command, "miner", "connect")) return await runMinerConnect(positionals, options);
    if (pathMatches(command, "miner", "status")) return await runMinerStatus();
    if (pathMatches(command, "miner", "start")) return await runMinerStart(positionals, options);
    if (pathMatches(command, "miner", "stop")) return runMinerStop();
    if (pathMatches(command, "miner", "runtime")) return runMinerRuntimeStatus(options);
    if (pathMatches(command, "miner")) return await runMinerStatus();

    if (pathMatches(command, "pricing")) return runPricing();
    if (pathMatches(command, "budget")) return runBudget(positionals, options);
    if (pathMatches(command, "estimate")) return runEstimate(positionals, options);
    if (pathMatches(command, "receipts", "show")) return runReceiptShow(positionals);
    if (pathMatches(command, "receipts", "list")) return runReceipts([], options);
    if (pathMatches(command, "receipts")) return runReceipts(positionals, options);
    if (pathMatches(command, "allowance")) return runAllowance(positionals, options);

    if (pathMatches(command, "jobs")) return await runJobs(positionals, options);
    if (pathMatches(command, "rewards")) {
      // If a rollup mode flag is set, use the local aggregator; otherwise
      // fall through to the coordinator-backed rewards command.
      if (options["by-worker"] !== undefined || options["by-address"] !== undefined || options.local === true) {
        return runRewardsRollup(options);
      }
      return await runRewards(options);
    }
    if (pathMatches(command, "leaderboard")) return await runLeaderboard(options);
    if (pathMatches(command, "adapters")) return await runAdapters(options);

    if (pathMatches(command, "contract", "scaffold")) return runScaffold("contract", positionals, options);
    if (pathMatches(command, "dapp", "scaffold")) return runScaffold("dapp", positionals, options);
    if (pathMatches(command, "token", "scaffold")) return runScaffold("token", positionals, options);
    if (pathMatches(command, "aicf-agent", "scaffold")) return runScaffold("aicf-agent", positionals, options);

    if (pathMatches(command, "release")) return runRelease(options);
    if (pathMatches(command, "ui")) return await runUI(options);

    if (pathMatches(command, "settlement", "check")) return await runSettlementCheck(positionals, options);
    if (pathMatches(command, "settlement", "ready")) return runSettlementReady(options);
    if (pathMatches(command, "settlement", "submit")) return await runSettlementCheck(positionals, options); // alias: dry-run readiness
    if (pathMatches(command, "settlement", "list")) return await runSettlementList(options);
    if (pathMatches(command, "settlement", "show")) return await runSettlementShow(positionals);
    if (pathMatches(command, "settlement", "resume")) return await runSettlementResume(positionals, options);
    if (pathMatches(command, "settlement", "verify-live")) return await runSettlementVerifyLive(positionals, options);
    if (pathMatches(command, "settlement", "dry-run")) return await runSettlementVerifyLive(positionals, options); // alias
    if (pathMatches(command, "settlement", "submit-live")) return await runSettlementSubmitLive(positionals, options);
    if (pathMatches(command, "settlement", "watch")) return await runSettlementWatch(positionals, options);
    if (pathMatches(command, "settlement", "pending")) return await runSettlementPending(options);
    if (pathMatches(command, "settlement", "inspect")) return await runSettlementInspect(positionals, options);
    if (pathMatches(command, "settlement", "reconcile")) return await runSettlementReconcile(positionals, options);
    if (pathMatches(command, "settlement")) return await runSettlementCheck(positionals, options);
    if (pathMatches(command, "confirm")) return await runWaitConfirm(positionals, options);

    if (pathMatches(command, "journal", "compact")) return runJournalCompact(options);
    if (pathMatches(command, "journal", "archive")) return runJournalArchive(options);
    if (pathMatches(command, "journal", "inspect")) return runJournalInspect(options);
    if (pathMatches(command, "metrics")) return runMetrics(options);
    if (pathMatches(command, "doctor", "useful-work")) return await runDoctorUsefulWork(options);
    if (pathMatches(command, "hybrid", "plan")) return await runHybridPlan(options);
    if (pathMatches(command, "coordinator", "doctor")) return await runCoordinatorDoctor(options);
    if (pathMatches(command, "coordinator", "verify-live")) return await runCoordinatorVerifyLive(options);
    if (pathMatches(command, "coordinator", "fetch-sample")) return await runCoordinatorFetchSample(options);
    if (pathMatches(command, "coordinator", "submit-fixture")) return await runCoordinatorSubmitFixture(options);
    if (pathMatches(command, "coordinator", "queue")) return runCoordinatorQueue(options);
    if (pathMatches(command, "coordinator", "queue-replay")) return await runCoordinatorQueueReplay(options);
    if (pathMatches(command, "coordinator", "history")) return runCoordinatorVerifyHistory(options);
    if (pathMatches(command, "coordinator", "latest")) return runCoordinatorLatest(options);
    if (pathMatches(command, "coordinator", "freshness")) return runCoordinatorFreshness(options);
    if (pathMatches(command, "payout", "audit")) return runPayoutAudit(options);
    if (pathMatches(command, "useful-work", "readiness")) return await runUsefulWorkReadiness(options);
    if (pathMatches(command, "useful-work", "go-live")) return await runGoLive(options);
    if (pathMatches(command, "useful-work", "snapshot")) return await runUsefulWorkSnapshot(options);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    process.stderr.write(`${c.red("error")}: ${msg}\n`);
    if (typeof options.verbose === "boolean" && options.verbose) {
      process.stderr.write(String((err as Error)?.stack ?? "") + "\n");
    }
    return 1;
  }

  info(`Unknown command: ${command.join(" ")}`);
  info(`Try: ${c.cyan("animica-agent --help")}`);
  return 64;
}
