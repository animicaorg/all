import {
  BillingEngine,
  DEFAULT_PRICING,
  estimate as estimateFn,
  formatANM,
  loadConfig,
  NoopSigner,
  parseANM,
  resolveWalletIdentity,
  OfflineSettlement,
  NodeSettlement,
} from "@animica/agent-core";

import { boolFlag, stringFlag } from "../args.js";
import { c, fail, header, info, kv, ok, table } from "../output.js";

function engine(): BillingEngine {
  const { config, paths } = loadConfig();
  const settlement = stringFlag({ x: "" } as Record<string, string | boolean>, "x")
    ? new NodeSettlement(new NoopSigner())
    : new OfflineSettlement();
  return new BillingEngine(paths.stateDir, config, undefined, settlement);
}

export function runPricing(): number {
  header("Pricing");
  const p = DEFAULT_PRICING;
  kv([
    ["base.perAction", `${formatANM(p.base.perAction)} ANM`],
    ["base.label", p.base.label],
    ["premium.perAction", `${formatANM(p.premium.perAction)} ANM`],
    ["premium.perKInput", `${formatANM(p.premium.perKInputTokens)} ANM / 1k tokens`],
    ["premium.perKOutput", `${formatANM(p.premium.perKOutputTokens)} ANM / 1k tokens`],
    ["minerSubsidy", `${(p.minerSubsidy * 100).toFixed(0)}%`],
  ]);
  return 0;
}

export function runBudget(positionals: string[], options: Record<string, string | boolean>): number {
  const e = engine();
  const verb = positionals[0] ?? "show";
  if (verb === "set") {
    const daily = stringFlag(options, "daily");
    const monthly = stringFlag(options, "monthly");
    const session = stringFlag(options, "session");
    const cfg = e.setBudget({
      ...(daily ? { dailyMaxRaw: parseANM(daily) } : {}),
      ...(monthly ? { monthlyMaxRaw: parseANM(monthly) } : {}),
      ...(session ? { sessionMaxRaw: parseANM(session) } : {}),
      ...(options.confirm !== undefined ? { confirmExpensiveAction: boolFlag(options, "confirm") } : {}),
    });
    ok(`updated budget: daily=${formatANM(cfg.dailyMaxRaw)} session=${formatANM(cfg.sessionMaxRaw)} monthly=${formatANM(cfg.monthlyMaxRaw)} ANM`);
    return 0;
  }
  if (verb === "reset-session") {
    e.resetSession();
    ok("session counter reset");
    return 0;
  }
  const s = e.getBudget();
  header("Budget");
  kv([
    ["session.max", `${formatANM(s.config.sessionMaxRaw)} ANM`],
    ["session.spent", `${formatANM(s.sessionSpentRaw)} ANM`],
    ["daily.window", s.dailyWindow],
    ["daily.max", `${formatANM(s.config.dailyMaxRaw)} ANM`],
    ["daily.spent", `${formatANM(s.dailySpentRaw)} ANM`],
    ["monthly.window", s.monthlyWindow],
    ["monthly.max", `${formatANM(s.config.monthlyMaxRaw)} ANM`],
    ["monthly.spent", `${formatANM(s.monthlySpentRaw)} ANM`],
    ["confirmExpensiveAction", s.config.confirmExpensiveAction],
  ]);
  return 0;
}

export function runReceipts(positionals: string[], options: Record<string, string | boolean>): number {
  const e = engine();
  const verb = positionals[0] ?? "list";
  if (verb === "export") {
    process.stdout.write(e.exportReceipts());
    return 0;
  }
  const list = e.listReceipts(Number.parseInt(stringFlag(options, "limit", "20") as string, 10));
  header(`Receipts (most recent ${list.length})`);
  table(
    ["at", "id", "kind", "status", "cost", "tx"],
    list.map((r) => [
      r.at,
      r.id.slice(0, 8),
      r.kind,
      r.status,
      `${formatANM(r.actualCostRaw ?? r.estimate.raw)} ANM`,
      r.txHash ?? "",
    ]),
  );
  return 0;
}

export function runAllowance(positionals: string[], options: Record<string, string | boolean>): number {
  const { config } = loadConfig();
  const e = engine();
  const wallet = resolveWalletIdentity(config)?.address ?? stringFlag(options, "wallet");
  const verb = positionals[0] ?? "list";
  if (verb === "grant") {
    if (!wallet) {
      fail("no wallet address; pass --wallet or run `animica-agent wallet connect`");
      return 64;
    }
    const cap = parseANM(stringFlag(options, "cap", "1") as string);
    const days = Number.parseInt(stringFlag(options, "days", "30") as string, 10);
    const expiresAt = new Date(Date.now() + days * 86400_000).toISOString();
    const perTaskCap = stringFlag(options, "per-task")
      ? parseANM(stringFlag(options, "per-task") as string)
      : undefined;
    const perSessionCap = stringFlag(options, "per-session")
      ? parseANM(stringFlag(options, "per-session") as string)
      : undefined;
    const g = e.grant({
      wallet,
      capRaw: cap,
      expiresAt,
      perTaskCapRaw: perTaskCap,
      perSessionCapRaw: perSessionCap,
    });
    ok(`granted allowance ${g.id}: cap=${formatANM(g.capRaw)} ANM expires=${g.expiresAt}`);
    return 0;
  }
  if (verb === "revoke") {
    const id = positionals[1] ?? stringFlag(options, "id");
    if (!id) {
      fail("usage: animica-agent allowance revoke <id>");
      return 64;
    }
    if (!e.revoke(id)) {
      fail("no allowance with that id");
      return 1;
    }
    ok(`revoked allowance ${id}`);
    return 0;
  }
  const list = e.listAllowances();
  header(`Allowances (${list.length})`);
  table(
    ["id", "wallet", "cap", "consumed", "expires", "status"],
    list.map((g) => [
      g.id.slice(0, 8),
      g.wallet.slice(0, 16) + (g.wallet.length > 16 ? "…" : ""),
      `${formatANM(g.capRaw)} ANM`,
      `${formatANM(g.consumedRaw)} ANM`,
      g.expiresAt,
      g.revokedAt ? "revoked" : "active",
    ]),
  );
  return 0;
}

export function runEstimate(positionals: string[], options: Record<string, string | boolean>): number {
  const kind = (positionals[0] ?? "code-task") as "code-task" | "chat-turn" | "scaffold" | "rpc-call";
  const input = Number.parseInt(stringFlag(options, "input", "0") as string, 10);
  const output = Number.parseInt(stringFlag(options, "output", "0") as string, 10);
  const premium = boolFlag(options, "premium", false);
  const subsidy = boolFlag(options, "subsidy", false);
  const e = estimateFn({ kind, inputTokens: input, outputTokens: output, premium, minerSubsidized: subsidy });
  header(`Estimate (${kind})`);
  kv([
    ["tier", e.tier],
    ["raw", e.raw.toString()],
    ["formatted", `${e.formattedANM} ANM`],
  ]);
  info(c.dim("breakdown:"));
  for (const row of e.breakdown) {
    info(`  ${row.component.padEnd(28)}  ${formatANM(row.raw)} ANM`);
  }
  return 0;
}
