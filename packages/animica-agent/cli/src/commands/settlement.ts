import {
  checkSettlementReadiness,
  estimate as estimateFn,
  loadConfig,
  parseANM,
  safeStringify,
  waitForConfirmation,
} from "@animica/agent-core";

import { boolFlag, stringFlag } from "../args.js";
import { c, fail, header, info, kv } from "../output.js";

export async function runSettlementCheck(positionals: string[], options: Record<string, string | boolean>): Promise<number> {
  const { config } = loadConfig();
  const json = boolFlag(options, "json", false);
  // Default estimate: a premium chat-turn so the check is realistic.
  const estRaw = stringFlag(options, "estimate")
    ? parseANM(stringFlag(options, "estimate") as string)
    : estimateFn({ kind: "chat-turn", premium: true }).raw;
  const report = await checkSettlementReadiness(config, {
    estimatedCostRaw: estRaw,
    walletAddress: stringFlag(options, "wallet"),
  });
  if (json) {
    process.stdout.write(safeStringify(report, { indent: 2 }) + "\n");
    return report.ok ? 0 : 1;
  }
  header(`Settlement readiness — ${report.ok ? c.green("OK") : c.yellow("FAIL")}`);
  for (const check of report.checks) {
    info(`  ${check.ok ? c.green("✓") : c.red("✗")} ${check.message}`);
  }
  if (!report.ok) {
    info("");
    fail(`first blocker: ${report.firstFailure?.reason} — ${report.firstFailure?.message}`);
  }
  void kv;
  return report.ok ? 0 : 1;
}

export async function runWaitConfirm(positionals: string[], options: Record<string, string | boolean>): Promise<number> {
  const txHash = positionals[0] ?? stringFlag(options, "tx");
  if (!txHash) {
    fail("usage: animica-agent confirm <txHash> [--max-attempts N] [--interval-ms MS]");
    return 64;
  }
  const { config } = loadConfig();
  const result = await waitForConfirmation(txHash, {
    rpcUrl: config.rpcUrl,
    maxAttempts: Number.parseInt(stringFlag(options, "max-attempts", "30") as string, 10),
    intervalMs: Number.parseInt(stringFlag(options, "interval-ms", "2000") as string, 10),
  });
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(result, { indent: 2 }) + "\n");
  } else {
    header(`Confirmation result for ${txHash}`);
    info(`  status:   ${result.status}`);
    info(`  attempts: ${result.attempts}`);
    if (result.receipt) info(`  block:    ${result.receipt.blockNumber?.toString() ?? "?"}`);
    if (result.error) info(`  error:    ${result.error}`);
  }
  return result.status === "confirmed" ? 0 : result.status === "pending" || result.status === "missing" ? 2 : 1;
}
