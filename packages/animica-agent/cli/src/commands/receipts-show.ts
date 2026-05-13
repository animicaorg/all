/**
 * `animica-agent receipts show <id>` — inspect one receipt.
 * `animica-agent rewards --by-worker / --by-address` — operator rollups.
 */

import {
  BillingEngine,
  buildRollup,
  loadConfig,
  safeStringify,
} from "@animica/agent-core";

import { boolFlag, stringFlag } from "../args.js";
import { c, fail, header, info, kv, table } from "../output.js";

export function runReceiptShow(positionals: string[]): number {
  const id = positionals[0];
  if (!id) {
    fail("usage: animica-agent receipts show <id>");
    return 64;
  }
  const { config, paths } = loadConfig();
  const billing = new BillingEngine(paths.stateDir, config);
  const receipts = billing.listReceipts(10_000);
  const hit = receipts.find((r) => r.id === id || r.id.startsWith(id));
  if (!hit) {
    fail(`no receipt matches id prefix '${id}'`);
    return 1;
  }
  process.stdout.write(safeStringify(hit, { indent: 2 }) + "\n");
  return 0;
}

export function runRewardsRollup(options: Record<string, string | boolean>): number {
  const { config, paths } = loadConfig();
  const roll = buildRollup(paths.stateDir, config);
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(roll, { indent: 2 }) + "\n");
    return 0;
  }
  const filter = stringFlag(options, "miner");
  if (boolFlag(options, "by-worker", false)) {
    header("Rewards by worker");
    table(
      ["worker", "addresses", "jobsTotal", "jobsPaid", "rewardANM"],
      roll.byWorker
        .filter((w) => !filter || w.addresses.includes(filter))
        .map((w) => [w.worker, w.addresses.join(","), w.jobsTotal, w.jobsPaid, w.rewardsFormatted]),
    );
    return 0;
  }
  header("Rewards by address");
  table(
    ["address", "workers", "jobsPaid", "rewardANM", "lastReceipt"],
    roll.byAddress
      .filter((a) => !filter || a.address === filter)
      .map((a) => [
        a.address.length > 32 ? a.address.slice(0, 30) + "…" : a.address,
        a.workers.join(","),
        a.jobsPaid,
        a.rewardsFormatted,
        a.lastReceiptAt ?? "—",
      ]),
  );
  info("");
  kv([
    ["settlement.ready", roll.settlement.ready.length],
    ["settlement.totalANM", roll.settlement.totalFormatted],
  ]);
  void c;
  return 0;
}

export function runSettlementReady(options: Record<string, string | boolean>): number {
  const { config, paths } = loadConfig();
  const roll = buildRollup(paths.stateDir, config);
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(roll.settlement, { indent: 2 }) + "\n");
    return 0;
  }
  header(`Settlement ready — ${roll.settlement.ready.length} job(s), ${roll.settlement.totalFormatted} ANM`);
  table(
    ["jobId", "receiptId", "address", "worker", "ANM"],
    roll.settlement.ready.map((r) => [
      r.jobId.slice(0, 12),
      r.receiptId?.slice(0, 8) ?? "—",
      r.minerAddress?.slice(0, 16) ?? "—",
      r.worker ?? "—",
      r.estimatedRewardFormatted,
    ]),
  );
  if (roll.settlement.pendingReceipts.length) {
    header(`Pending receipts (${roll.settlement.pendingReceipts.length})`);
    table(
      ["id", "wallet", "worker", "ANM", "at"],
      roll.settlement.pendingReceipts.map((r) => [
        r.id.slice(0, 8),
        r.wallet?.slice(0, 16) ?? "—",
        r.worker ?? "—",
        r.formatted,
        r.at,
      ]),
    );
  }
  return 0;
}
