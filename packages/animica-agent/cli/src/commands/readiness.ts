/**
 * CLI surface for the readiness aggregator.
 *
 *   animica-agent useful-work readiness [--coordinator-url <url>] [--json] [--explain]
 */

import {
  READINESS_FAILURE_GUIDE,
  loadConfig,
  safeStringify,
  usefulWorkReadiness,
} from "@animica/agent-core";

import { boolFlag, stringFlag } from "../args.js";
import { c, fail, header, info, kv } from "../output.js";

export async function runUsefulWorkReadiness(options: Record<string, string | boolean>): Promise<number> {
  const { config, paths } = loadConfig();
  const coordinatorUrl = stringFlag(options, "coordinator-url");
  const report = await usefulWorkReadiness(config, {
    stateDir: paths.stateDir,
    coordinator: coordinatorUrl ? { baseUrl: coordinatorUrl } : undefined,
  });
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(report, { indent: 2 }) + "\n");
    return report.ok ? 0 : 1;
  }
  header(`Useful-work readiness — ${report.ok ? c.green("GO") : c.red("NO-GO")}`);
  for (const check of report.checks) {
    const mark = check.ok ? c.green("✓") : check.level === "error" ? c.red("✗") : c.yellow("!");
    info(`  ${mark} ${check.name}: ${check.message}`);
  }
  if (boolFlag(options, "explain", false)) {
    if (report.blockers.length > 0 || report.warnings.length > 0) {
      header("Failure guide");
      for (const ch of [...report.blockers, ...report.warnings]) {
        const guide = READINESS_FAILURE_GUIDE[ch.name];
        if (!guide) continue;
        info(`  ${c.bold(ch.name)}`);
        kv([
          ["  what", guide.what],
          ["  fix", guide.fix],
        ]);
      }
    }
  }
  info("");
  info(report.summary);
  return report.ok ? 0 : 1;
}
