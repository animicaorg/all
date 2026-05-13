/**
 * `animica-agent chat | code | apply | diff | rollback`.
 *
 * The "code" flow:
 *   1. authorize via billing engine (estimate)
 *   2. call provider with system+user prompt; provider returns text
 *   3. if text contains a fenced ```json patch block, treat it as a Patch;
 *      otherwise just print the plan
 *   4. journal the pending patch to .animica/agent-state/pending.json
 *   5. user runs `animica-agent diff` and `animica-agent apply` to commit
 */

import {
  applyPatch,
  BillingEngine,
  createLogger,
  createPatch,
  evaluateEligibility,
  findSensitiveTargets,
  detectMinerIdentity,
  listJournal,
  loadConfig,
  NodeSettlement,
  NodeWalletSigner,
  NoopSigner,
  OfflineSettlement,
  pickProvider,
  readLatestJournal,
  renderPatchPreview,
  Repo,
  resolveWalletIdentity,
  rollbackPatch,
  safeParse,
  safeStringify,
  SessionStore,
  UsageJournal,
  type AgentMessage,
  type FileOp,
  type Patch,
  type ReceiptRequest,
} from "@animica/agent-core";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { boolFlag, stringFlag } from "../args.js";
import { ask, confirm } from "../prompt.js";
import { c, fail, header, info, kv, ok, warn } from "../output.js";

function pendingPath(stateDir: string): string {
  return join(stateDir, "pending.json");
}

function patchJournalDir(stateDir: string): string {
  return join(stateDir, "patches");
}

function extractJsonPatch(text: string): Patch | null {
  // Look for the first ```json ... ``` block; expect it to contain Patch JSON.
  const m = text.match(/```json\s*([\s\S]*?)```/);
  if (!m) return null;
  try {
    const parsed = safeParse<unknown>(m[1].trim());
    if (parsed && typeof parsed === "object" && "ops" in (parsed as Record<string, unknown>)) {
      const ops = (parsed as { ops: unknown }).ops as FileOp[];
      const message = ((parsed as { message?: string }).message ?? "agent edit").toString();
      return createPatch(message, ops);
    }
  } catch {
    return null;
  }
  return null;
}

export async function runCode(positionals: string[], options: Record<string, string | boolean>): Promise<number> {
  const { config, paths } = loadConfig();
  const logger = createLogger(boolFlag(options, "verbose", false) ? "debug" : "info");
  const repo = new Repo(paths.projectRoot);
  const sessionStore = new SessionStore(paths.stateDir);
  const usage = new UsageJournal(paths.stateDir);
  const identity = detectMinerIdentity(config);
  const wallet = resolveWalletIdentity(config);
  const minerSubsidized = identity.source !== "none";
  const settlementChoice = stringFlag(options, "settlement") ?? (boolFlag(options, "pay-onchain", false) ? "node" : "offline");
  const settlement =
    settlementChoice === "node"
      ? new NodeSettlement(new NodeWalletSigner())
      : settlementChoice === "extension"
        ? new NodeSettlement(new NoopSigner()) // Browser-only path; in CLI we fall back to refuse rather than silently use offline.
        : new OfflineSettlement();
  const billing = new BillingEngine(paths.stateDir, config, undefined, settlement);

  const eligibility = evaluateEligibility(config, identity);
  if (!eligibility.allowed) {
    fail(`Not allowed to run agent: ${eligibility.reason}`);
    return 4;
  }
  const task = positionals.join(" ").trim() || stringFlag(options, "task");
  if (!task) {
    fail("provide a task: animica-agent code \"<task>\"");
    return 64;
  }
  const premium = stringFlag(options, "premium") !== undefined ? boolFlag(options, "premium") : config.provider !== undefined && config.provider !== "offline";
  const est = billing.authorize({ kind: "code-task", premium, minerSubsidized });
  info(c.dim(`estimated cost: ${est.formattedANM} ANM (${est.tier})`));

  const provider = pickProvider(config);
  const messages: AgentMessage[] = [
    {
      role: "system",
      content: [
        "You are the Animica Coding Agent.",
        "Output a short plan, then a single fenced ```json block with this exact shape:",
        '{ "message": "<one line>", "ops": [ ... FileOp ... ] }',
        "FileOp variants:",
        "  { \"kind\": \"create\",  \"path\": \"...\", \"contents\": \"...\" }",
        "  { \"kind\": \"replace\", \"path\": \"...\", \"contents\": \"...\" }",
        "  { \"kind\": \"delete\",  \"path\": \"...\" }",
        "  { \"kind\": \"edit\",    \"path\": \"...\", \"hunks\": [{ \"oldLines\": [...], \"newLines\": [...], \"anchorLine\": N }] }",
        "Never modify .env, secrets, *.key, *.pem.",
      ].join("\n"),
    },
    { role: "user", content: task },
  ];
  const completion = await provider.complete({ messages, model: config.defaultModel });
  logger.debug("provider response", { provider: completion.provider, model: completion.model });

  process.stdout.write(completion.text + "\n");

  const patch = extractJsonPatch(completion.text);
  let receiptRequest: ReceiptRequest = {
    kind: "code-task",
    estimate: est,
    sessionId: stringFlag(options, "session"),
    wallet: wallet?.address,
    worker: identity.worker,
    inputTokens: completion.usage?.promptTokens,
    outputTokens: completion.usage?.completionTokens,
    toolsUsed: ["provider:" + completion.provider, "patch-extract"],
    status: "estimated",
  };
  const receipt = await billing.charge(receiptRequest);
  usage.record({
    kind: "code-task",
    detail: { provider: completion.provider, premium },
    attribution: { walletAddress: wallet?.address, minerAddress: identity.payoutAddress, worker: identity.worker, creditsMode: config.creditsMode, aicfMode: config.aicfMode },
  });

  info("");
  kv([
    ["receipt.id", receipt.id],
    ["receipt.status", receipt.status],
    ["receipt.hash", receipt.receiptHash.slice(0, 16) + "…"],
    ["cost.actual", `${est.formattedANM} ANM`],
  ]);

  if (!patch) {
    info(c.dim("no machine-readable patch found in response; only a plan was emitted."));
    return 0;
  }

  const sensitive = findSensitiveTargets(patch);
  if (sensitive.length) {
    warn(`patch touches sensitive files: ${sensitive.join(", ")}`);
  }
  writeFileSync(pendingPath(paths.stateDir), safeStringify(patch, { indent: 2 }) + "\n", "utf8");
  ok(`pending patch saved to ${pendingPath(paths.stateDir)}`);
  info("Run `animica-agent diff` to inspect and `animica-agent apply` to write changes.");
  return 0;
}

export function runDiff(): number {
  const { paths } = loadConfig();
  const repo = new Repo(paths.projectRoot);
  const f = pendingPath(paths.stateDir);
  if (!existsSync(f)) {
    info("No pending patch. Run `animica-agent code \"<task>\"` to generate one.");
    return 0;
  }
  const patch = safeParse<Patch>(readFileSync(f, "utf8"));
  process.stdout.write(renderPatchPreview(patch, repo) + "\n");
  return 0;
}

export async function runApply(options: Record<string, string | boolean>): Promise<number> {
  const { paths, config } = loadConfig();
  const repo = new Repo(paths.projectRoot);
  const f = pendingPath(paths.stateDir);
  if (!existsSync(f)) {
    fail("No pending patch.");
    return 1;
  }
  const patch = safeParse<Patch>(readFileSync(f, "utf8"));
  if (config.approvalMode !== "auto" && !boolFlag(options, "yes", false)) {
    process.stdout.write(renderPatchPreview(patch, repo) + "\n");
    if (!(await confirm(`Apply patch ${patch.id}?`, false))) {
      info("Aborted.");
      return 0;
    }
  }
  const applied = applyPatch({
    repo,
    patch,
    dryRun: boolFlag(options, "dry-run", false),
    journalDir: patchJournalDir(paths.stateDir),
  });
  if (boolFlag(options, "dry-run", false)) {
    info(c.dim(`dry-run only; ${applied.before.length} file(s) would have been touched.`));
    return 0;
  }
  require("node:fs").rmSync(f, { force: true });
  ok(`applied ${patch.id} (${patch.ops.length} op(s))`);
  return 0;
}

export function runRollback(): number {
  const { paths } = loadConfig();
  const repo = new Repo(paths.projectRoot);
  const latest = readLatestJournal(patchJournalDir(paths.stateDir));
  if (!latest) {
    info("No applied patch to roll back.");
    return 0;
  }
  rollbackPatch(repo, latest);
  ok(`rolled back ${latest.id}`);
  return 0;
}

export function runPatches(): number {
  const { paths } = loadConfig();
  const list = listJournal(patchJournalDir(paths.stateDir));
  header("Applied patches (most recent first)");
  if (!list.length) {
    info("(none)");
    return 0;
  }
  for (const p of list) {
    info(`${p.appliedAt}  ${p.id}  ${p.message}`);
  }
  return 0;
}

export async function runChat(options: Record<string, string | boolean>): Promise<number> {
  const { config, paths } = loadConfig();
  const provider = pickProvider(config);
  const store = new SessionStore(paths.stateDir);
  const identity = detectMinerIdentity(config);
  const wallet = resolveWalletIdentity(config);
  const session = store.newSession({
    minerAddress: identity.payoutAddress,
    walletAddress: wallet?.address,
    worker: identity.worker,
  });
  info(c.cyan(`Session ${session.id} started.`));
  info(c.dim(`provider: ${provider.name}  approvalMode: ${config.approvalMode}  minerMode: ${config.minerMode}`));
  info(c.dim("Slash commands: /help /status /wallet /node /funding /jobs /receipts /exit"));
  info(c.dim("Type 'exit' or '/exit' to quit."));
  // Non-TTY environments (CI, scripts piping stdin) have no interactive
  // questions; exit cleanly with a hint so the binary stays scriptable.
  if (!process.stdin.isTTY && !boolFlag(options, "force", false)) {
    info(c.dim("(stdin is not a TTY; exiting. Pass --force to keep the loop alive.)"));
    return 0;
  }
  for (;;) {
    const turn = await ask("you");
    if (!turn) continue;
    if (turn === "exit" || turn === "quit" || turn === "/exit" || turn === "/quit") break;
    // Slash commands route to the equivalent subcommands without leaving chat.
    if (turn.startsWith("/")) {
      const handled = await handleSlashCommand(turn);
      if (handled) continue;
    }
    store.append(session, "user", turn);
    const completion = await provider.complete({
      messages: session.turns.map((t) => ({ role: t.role === "tool" ? "assistant" : (t.role as AgentMessage["role"]), content: t.content })),
      model: config.defaultModel,
    });
    store.append(session, "assistant", completion.text);
    process.stdout.write(c.green("agent") + ": " + completion.text + "\n");
  }
  void options;
  return 0;
}

/** Returns true if the line was handled as a slash command. */
async function handleSlashCommand(line: string): Promise<boolean> {
  const cmd = line.split(/\s+/)[0];
  switch (cmd) {
    case "/help":
      info(c.dim("Slash commands:"));
      info("  /help     show this list");
      info("  /status   project + network status");
      info("  /wallet   show wallet identity + balance");
      info("  /node     show local node status");
      info("  /funding  show funding instructions");
      info("  /jobs     list jobs from the coordinator");
      info("  /receipts list recent receipts");
      info("  /exit     quit the chat");
      return true;
    case "/status":
    case "/wallet":
    case "/node":
    case "/funding":
    case "/jobs":
    case "/receipts": {
      const { spawnSync } = await import("node:child_process");
      const map: Record<string, string[]> = {
        "/status": ["status"],
        "/wallet": ["wallet", "connect"],
        "/node": ["node", "status"],
        "/funding": ["wallet", "fund-help"],
        "/jobs": ["jobs", "list"],
        "/receipts": ["receipts", "list"],
      };
      const argv = map[cmd];
      if (!argv) return false;
      const r = spawnSync(process.execPath, [process.argv[1], ...argv], { encoding: "utf8" });
      process.stdout.write(r.stdout ?? "");
      if (r.stderr) process.stderr.write(r.stderr);
      return true;
    }
    default:
      info(c.dim(`unknown slash command: ${cmd}. /help for options.`));
      return true;
  }
}
