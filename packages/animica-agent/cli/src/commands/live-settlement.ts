/**
 * CLI surface for live settlement:
 *   - animica-agent settlement verify-live <receiptId>
 *   - animica-agent settlement submit-live <receiptId> --i-understand-this-spends-real-funds
 *   - animica-agent settlement watch [<receiptId> ...]
 */

import { existsSync, readFileSync } from "node:fs";

import {
  BillingEngine,
  inspectAttempt,
  LIVE_SUBMIT_ACK,
  listPending,
  LiveSubmitRefused,
  loadConfig,
  NodeWalletSigner,
  payloadFromReceipt,
  reconcilePending,
  safeStringify,
  submitLive,
  summarizeReconcile,
  summarizeWatch,
  verifyLive,
  watchLive,
  type Receipt,
} from "@animica/agent-core";

import { boolFlag, stringFlag } from "../args.js";
import { c, fail, header, info, kv, table } from "../output.js";

function findReceiptById(receiptsFile: string, id: string): Receipt | null {
  if (!existsSync(receiptsFile)) return null;
  const lines = readFileSync(receiptsFile, "utf8").split(/\r?\n/).filter(Boolean);
  for (let i = lines.length - 1; i >= 0; i--) {
    try {
      const r = JSON.parse(lines[i]) as Receipt;
      if (r.id === id) return r;
    } catch {
      /* skip */
    }
  }
  return null;
}

function payloadFromCli(
  options: Record<string, string | boolean>,
  positionals: string[],
  paths: { stateDir: string },
  cfg: { minerAddress?: string },
): { receiptId: string; recipient: string; amountRaw: bigint; artifactHash?: string } | null {
  const id = positionals[0] ?? stringFlag(options, "receipt");
  if (!id) {
    fail("usage: settlement verify-live <receiptId> [--recipient anm1...] [--amount-raw N] [--artifact-hash HEX]");
    return null;
  }
  const receipt = findReceiptById(`${paths.stateDir}/receipts.jsonl`, id);
  const recipientFlag = stringFlag(options, "recipient");
  const recipient = recipientFlag ?? receipt?.wallet ?? cfg.minerAddress ?? "";
  if (!recipient) {
    fail(`receipt ${id} not found and no --recipient supplied`);
    return null;
  }
  const amtRaw = stringFlag(options, "amount-raw");
  const amountRaw = amtRaw
    ? BigInt(amtRaw)
    : receipt?.actualCostRaw ?? receipt?.estimate?.raw ?? 0n;
  const artifactHash =
    stringFlag(options, "artifact-hash") ??
    receipt?.idempotencyKey?.split(":").pop();
  return { receiptId: id, recipient, amountRaw, artifactHash };
}

export async function runSettlementVerifyLive(
  positionals: string[],
  options: Record<string, string | boolean>,
): Promise<number> {
  const { config, paths } = loadConfig();
  const fields = payloadFromCli(options, positionals, paths, config);
  if (!fields) return 64;
  const payload = payloadFromReceipt(fields.receiptId, fields.recipient, fields.amountRaw, fields.artifactHash);
  const report = await verifyLive(config, payload, { stateDir: paths.stateDir });
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(report, { indent: 2 }) + "\n");
    return report.ok ? 0 : 1;
  }
  header(`verify-live — ${report.ok ? c.green("GO") : c.red("NO-GO")}`);
  kv([
    ["receipt", payload.receiptId],
    ["recipient", payload.recipient],
    ["amount", `${payload.formattedAmount} ANM`],
    ["amountRaw", payload.amountRaw.toString()],
    ["artifactHash", payload.artifactHash ?? "—"],
  ]);
  header("Checks");
  for (const check of report.checks) {
    const mark = check.ok ? c.green("✓") : check.level === "error" ? c.red("✗") : c.yellow("!");
    info(`  ${mark} ${check.name}: ${check.message}`);
  }
  header("Risks");
  for (const r of report.risks) info(`  • ${r}`);
  info("");
  info(report.summary);
  return report.ok ? 0 : 1;
}

export async function runSettlementSubmitLive(
  positionals: string[],
  options: Record<string, string | boolean>,
): Promise<number> {
  const { config, paths } = loadConfig();
  const fields = payloadFromCli(options, positionals, paths, config);
  if (!fields) return 64;
  const payload = payloadFromReceipt(fields.receiptId, fields.recipient, fields.amountRaw, fields.artifactHash);
  const ackFlag = stringFlag(options, "i-understand-this-spends-real-funds");
  const acknowledgement = ackFlag === undefined ? "" : ackFlag === "" ? LIVE_SUBMIT_ACK : ackFlag;
  const signer = new NodeWalletSigner();
  const requireFresh = boolFlag(options, "require-fresh-coordinator", false);
  const freshWindowFlag = stringFlag(options, "freshness-window-ms");
  try {
    const r = await submitLive(config, payload, {
      stateDir: paths.stateDir,
      signer,
      acknowledgement,
      confirmationDepth: Number.parseInt((stringFlag(options, "depth") ?? "1") as string, 10),
      maxAttempts: Number.parseInt((stringFlag(options, "max-attempts") ?? "5") as string, 10),
      requireFreshCoordinator: requireFresh
        ? { windowMs: freshWindowFlag ? Number.parseInt(freshWindowFlag, 10) : undefined }
        : undefined,
    });
    if (boolFlag(options, "json", false)) {
      process.stdout.write(safeStringify(r, { indent: 2 }) + "\n");
      return r.attempt.status === "paid" || r.attempt.status === "confirmed" || r.attempt.status === "confirming" ? 0 : 1;
    }
    header(`submit-live — status=${r.attempt.status}`);
    kv([
      ["receipt", r.attempt.receiptId],
      ["txHash", r.attempt.txHash ?? "—"],
      ["confirmations", r.attempt.confirmations ?? 0],
      ["attempts", r.attempt.attempts],
      ["updatedAt", r.attempt.updatedAt],
    ]);
    if (r.attempt.status === "paid") {
      info(c.green("settlement is paid"));
    } else if (r.attempt.status === "confirming" || r.attempt.status === "submitted") {
      info(c.yellow(`settlement is ${r.attempt.status}; run 'animica-agent settlement watch ${r.attempt.receiptId}' to track it`));
    } else if (r.attempt.status === "failed_permanent" || r.attempt.status === "rejected") {
      info(c.red(`settlement is ${r.attempt.status}: ${r.attempt.reason ?? ""}`));
      return 1;
    }
    return 0;
  } catch (err: unknown) {
    if (err instanceof LiveSubmitRefused) {
      fail(`refused (${err.reason}): ${err.message}`);
      if (err.verifyReport) {
        info("");
        info("verify-live blockers:");
        for (const ch of err.verifyReport.checks) {
          if (!ch.ok && ch.level === "error") info(`  ${c.red("✗")} ${ch.name}: ${ch.message}`);
        }
      }
      return 1;
    }
    throw err;
  }
}

export async function runSettlementPending(options: Record<string, string | boolean>): Promise<number> {
  const { paths } = loadConfig();
  const list = listPending(paths.stateDir);
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(list, { indent: 2 }) + "\n");
    return 0;
  }
  header(`Pending settlements (${list.length})`);
  if (list.length === 0) {
    info(c.dim("  (none)"));
    return 0;
  }
  table(
    ["receiptId", "status", "amountRaw", "txHash", "attempts", "updatedAt"],
    list.map((a: { receiptId: string; status: string; amountRaw: bigint; txHash?: string; attempts: number; updatedAt: string }) => [
      a.receiptId.slice(0, 12),
      a.status,
      a.amountRaw.toString(),
      a.txHash?.slice(0, 16) ?? "—",
      a.attempts,
      a.updatedAt,
    ]),
  );
  return 0;
}

export async function runSettlementInspect(
  positionals: string[],
  options: Record<string, string | boolean>,
): Promise<number> {
  const id = positionals[0];
  if (!id) {
    fail("usage: animica-agent settlement inspect <receiptId>");
    return 64;
  }
  const { paths } = loadConfig();
  const r = inspectAttempt(paths.stateDir, id);
  if (!r) {
    fail(`no settlement records for receiptId=${id}`);
    return 1;
  }
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(r, { indent: 2 }) + "\n");
    return 0;
  }
  header(`Settlement ${id}`);
  kv([
    ["status", r.latest.status],
    ["classification", r.classification],
    ["txHash", r.latest.txHash ?? "—"],
    ["confirmations", r.latest.confirmations ?? 0],
    ["amountRaw", r.latest.amountRaw.toString()],
    ["attempts", r.latest.attempts],
    ["createdAt", r.latest.createdAt],
    ["updatedAt", r.latest.updatedAt],
  ]);
  header("Transition history");
  table(
    ["at", "from", "to", "reason"],
    r.history.map((h: { at: string; from: string; to: string; reason?: string }) => [
      h.at,
      h.from,
      h.to,
      (h.reason ?? "").slice(0, 60),
    ]),
  );
  return 0;
}

export async function runSettlementReconcile(
  positionals: string[],
  options: Record<string, string | boolean>,
): Promise<number> {
  const { config, paths } = loadConfig();
  const signer = boolFlag(options, "rebroadcast", false) ? new NodeWalletSigner() : undefined;
  const entries = await reconcilePending({
    stateDir: paths.stateDir,
    rpcUrl: config.rpcUrl,
    confirmationDepth: Number.parseInt((stringFlag(options, "depth") ?? "1") as string, 10),
    receiptIds: positionals.length > 0 ? positionals : undefined,
    signer,
  });
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(entries, { indent: 2 }) + "\n");
    return entries.some((e: { classification: string }) => e.classification === "failed" || e.classification === "rejected") ? 1 : 0;
  }
  header(`reconcile (${entries.length})`);
  if (entries.length === 0) {
    info(c.dim("  (no in-flight settlements)"));
    return 0;
  }
  table(
    ["receipt", "before", "after", "class", "txHash", "conf"],
    entries.map((e: { receiptId: string; before: string; after: string; classification: string; txHash?: string; confirmations?: number }) => [
      e.receiptId.slice(0, 12),
      e.before,
      e.after,
      e.classification,
      e.txHash?.slice(0, 16) ?? "—",
      e.confirmations ?? 0,
    ]),
  );
  info("");
  info(summarizeReconcile(entries));
  return 0;
}

export async function runSettlementWatch(
  positionals: string[],
  options: Record<string, string | boolean>,
): Promise<number> {
  const { config, paths } = loadConfig();
  const entries = await watchLive({
    stateDir: paths.stateDir,
    rpcUrl: config.rpcUrl,
    confirmationDepth: Number.parseInt((stringFlag(options, "depth") ?? "1") as string, 10),
    receiptIds: positionals.length > 0 ? positionals : undefined,
  });
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(entries, { indent: 2 }) + "\n");
    return 0;
  }
  header(`watch-live (${entries.length})`);
  table(
    ["receipt", "before", "after", "class", "txHash", "conf"],
    entries.map((e: { receiptId: string; before: string; after: string; classification: string; txHash?: string; confirmations?: number }) => [
      e.receiptId.slice(0, 12),
      e.before,
      e.after,
      e.classification,
      e.txHash?.slice(0, 16) ?? "—",
      e.confirmations ?? 0,
    ]),
  );
  info("");
  info(summarizeWatch(entries));
  return 0;
}
