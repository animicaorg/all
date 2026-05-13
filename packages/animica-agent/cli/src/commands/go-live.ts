/**
 * CLI:
 *   animica-agent useful-work go-live [--coordinator-url <url>] [--json]
 *   animica-agent useful-work snapshot [--json]
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import {
  checkCoordinatorFreshness,
  goLive,
  HardenedCoordinator,
  inspectQueues,
  latestVerificationReport,
  loadConfig,
  MetricsRegistry,
  safeStringify,
  SettlementJournal,
  SETTLEMENT_TERMINAL_STATES,
  type SettlementAttempt,
} from "@animica/agent-core";

import { boolFlag, stringFlag } from "../args.js";
import { c, fail, header, info, kv, table } from "../output.js";

export async function runGoLive(options: Record<string, string | boolean>): Promise<number> {
  const { config, paths } = loadConfig();
  const report = await goLive(config, {
    stateDir: paths.stateDir,
    coordinatorBaseUrl: stringFlag(options, "coordinator-url"),
  });
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(report, { indent: 2 }) + "\n");
    return report.ok ? 0 : 1;
  }
  header(`Useful-work go-live — ${report.ok ? c.green("GO") : c.red("NO-GO")}`);
  for (const ch of report.checks) {
    const mark = ch.ok ? c.green("✓") : ch.level === "error" ? c.red("✗") : c.yellow("!");
    info(`  ${mark} ${ch.name}: ${ch.message}`);
    if (!ch.ok && ch.fix) info(`    ${c.dim("fix: " + ch.fix)}`);
  }
  info("");
  info(report.summary);
  return report.ok ? 0 : 1;
}

/**
 * Compact status snapshot for dashboards. Aggregates counters, journal state,
 * coordinator freshness, and queue depth into a single JSON-safe object.
 */
export async function runUsefulWorkSnapshot(options: Record<string, string | boolean>): Promise<number> {
  const { config, paths } = loadConfig();
  const registry = new MetricsRegistry();
  const snap = registry.snapshot(paths.stateDir);
  const queues = inspectQueues(paths.stateDir);
  const fresh = checkCoordinatorFreshness({ stateDir: paths.stateDir });
  const latestCoord = latestVerificationReport(paths.stateDir);
  // Pull in-flight settlement summary directly so the snapshot reflects
  // live state, not just counters.
  const journal = new SettlementJournal(paths.stateDir);
  journal.reload();
  const inFlight = journal.list().filter((a: SettlementAttempt) => !SETTLEMENT_TERMINAL_STATES.has(a.status));
  const snapshot = {
    generatedAt: new Date().toISOString(),
    settlementMode: config.settlementMode ?? "offline",
    reservePolicy: config.reservePolicy ?? (config.settlementMode === "live" ? "strict" : "off"),
    counters: snap.counters,
    jobs: snap.jobs,
    settlements: snap.settlements,
    revenue: snap.revenue,
    queue: snap.queue,
    journal: {
      jobs: queues.jobs,
      settlements: queues.settlements,
    },
    inFlightSettlements: inFlight.map((a: SettlementAttempt) => ({
      receiptId: a.receiptId,
      status: a.status,
      txHash: a.txHash,
      confirmations: a.confirmations,
      attempts: a.attempts,
      updatedAt: a.updatedAt,
    })),
    coordinator: {
      fresh: fresh.fresh,
      reason: fresh.reason,
      ageMs: fresh.ageMs,
      latestAt: latestCoord?.generatedAt,
      latestOk: latestCoord?.ok,
      baseUrl: latestCoord?.baseUrl,
    },
  };
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(snapshot, { indent: 2 }) + "\n");
    return 0;
  }
  header(`useful-work snapshot @ ${snapshot.generatedAt}`);
  kv([
    ["settlementMode", snapshot.settlementMode],
    ["reservePolicy", snapshot.reservePolicy],
    ["coordinator.fresh", snapshot.coordinator.fresh ? "yes" : "no"],
    ["coordinator.latestAt", snapshot.coordinator.latestAt ?? "—"],
    ["queue.depth", snapshot.queue.depth],
  ]);
  header("Counters");
  kv(Object.entries(snapshot.counters).map(([k, v]) => [k, v as number]) as [string, number][]);
  header(`In-flight settlements (${snapshot.inFlightSettlements.length})`);
  table(
    ["receipt", "status", "tx", "conf", "attempts", "updatedAt"],
    snapshot.inFlightSettlements.map((a) => [
      a.receiptId.slice(0, 12),
      a.status,
      a.txHash?.slice(0, 16) ?? "—",
      a.confirmations ?? 0,
      a.attempts,
      a.updatedAt,
    ]),
  );
  return 0;
}
