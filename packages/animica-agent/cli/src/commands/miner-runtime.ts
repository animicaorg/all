/**
 * `animica-agent miner start | stop` and related useful-work commands.
 *
 * The runtime here is the real useful-work miner — it polls a coordinator,
 * runs jobs locally, creates receipts, and updates the persisted job
 * journal. Three invocation modes:
 *
 *   --once             process one iteration of up to --max-jobs and exit
 *   --max-iterations N loop N iterations then exit
 *   (no flag)          daemon: loop until SIGINT
 *
 * `miner stop` is implemented by writing a stop sentinel that any running
 * daemon polls on disk. The previous design that relied on a parent pidfile
 * would not work because daemon mode supervises its own loop; the sentinel
 * approach is portable and crash-safe.
 */

import { existsSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import {
  buildRollup,
  HttpCoordinator,
  LocalCoordinator,
  loadConfig,
  MinerRuntime,
  safeStringify,
  type Coordinator,
} from "@animica/agent-core";

import { boolFlag, stringFlag } from "../args.js";
import { c, fail, header, info, kv, ok, table } from "../output.js";

const STOP_SENTINEL = "miner.stop";

function pickCoordinator(cfgUrl: string | undefined, dataDir: string): Coordinator {
  if (cfgUrl) return new HttpCoordinator(cfgUrl);
  return new LocalCoordinator({ dataDir });
}

function stopSentinelPath(stateDir: string): string {
  return join(stateDir, STOP_SENTINEL);
}

export async function runMinerStart(positionals: string[], options: Record<string, string | boolean>): Promise<number> {
  const { config, paths } = loadConfig();
  const once = boolFlag(options, "once", false);
  const concurrency = Math.max(1, Number.parseInt(stringFlag(options, "concurrency", "1") as string, 10));
  const maxJobs = Math.max(1, Number.parseInt(stringFlag(options, "max-jobs", "5") as string, 10));
  const maxIterations = stringFlag(options, "max-iterations")
    ? Number.parseInt(stringFlag(options, "max-iterations") as string, 10)
    : undefined;
  const idleSleepMs = Math.max(50, Number.parseInt(stringFlag(options, "idle-ms", "5000") as string, 10));
  const url = stringFlag(options, "coordinator-url");
  const coordinator = pickCoordinator(url, join(paths.stateDir, "coordinator"));

  // Clear any pre-existing stop sentinel from a previous session.
  const sentinel = stopSentinelPath(paths.stateDir);
  if (existsSync(sentinel)) rmSync(sentinel, { force: true });

  const runtime = new MinerRuntime(config, {
    coordinator,
    stateDir: paths.stateDir,
    concurrency,
    idleSleepMs,
  });

  header(`useful-work miner — starting`);
  kv([
    ["coordinator", coordinator.name],
    ["stateDir", paths.stateDir],
    ["concurrency", runtime.concurrency()],
    ["worker", config.workerName ?? "(unset)"],
    ["minerAddress", config.minerAddress ?? "(unset)"],
    ["mode", once ? "once" : maxIterations ? `iterations=${maxIterations}` : "daemon"],
  ]);

  if (once) {
    const r = await runtime.runOnce(maxJobs);
    info("");
    kv([
      ["processed", r.processed],
      ["recoverable", r.recoverable],
      ["failedPermanent", r.failedPermanent],
      ["records", r.records.length],
    ]);
    return r.failedPermanent > 0 ? 2 : 0;
  }

  // Wire SIGINT to graceful stop.
  let stopping = false;
  const stop = async () => {
    if (stopping) return;
    stopping = true;
    info(c.dim("stop requested, draining …"));
    await runtime.stop();
    if (existsSync(sentinel)) rmSync(sentinel, { force: true });
    info(c.green("stopped cleanly"));
    process.exit(0);
  };
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);

  // Also poll the on-disk sentinel so `miner stop` from another shell works.
  const sentinelTimer = setInterval(() => {
    if (existsSync(sentinel)) {
      clearInterval(sentinelTimer);
      stop().catch(() => undefined);
    }
  }, 500);
  // Allow the timer to not keep the process alive on its own.
  sentinelTimer.unref?.();

  const totals = await runtime.run({ maxIterations, maxJobsPerIteration: maxJobs });
  clearInterval(sentinelTimer);
  if (boolFlag(options, "json", false)) {
    process.stdout.write(
      safeStringify(
        { processed: totals.processed, recoverable: totals.recoverable, failedPermanent: totals.failedPermanent, records: totals.records.length },
        { indent: 2 },
      ) + "\n",
    );
  } else {
    kv([
      ["processed", totals.processed],
      ["recoverable", totals.recoverable],
      ["failedPermanent", totals.failedPermanent],
    ]);
  }
  return totals.failedPermanent > 0 ? 2 : 0;
}

export function runMinerStop(): number {
  const { paths } = loadConfig();
  const sentinel = stopSentinelPath(paths.stateDir);
  writeFileSync(sentinel, new Date().toISOString(), "utf8");
  ok(`stop sentinel written: ${sentinel}`);
  info(c.dim("any running `animica-agent miner start` will drain and exit within a few seconds."));
  return 0;
}

export function runMinerRuntimeStatus(options: Record<string, string | boolean>): number {
  const { config, paths } = loadConfig();
  const roll = buildRollup(paths.stateDir, config);
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(roll, { indent: 2 }) + "\n");
    return 0;
  }
  header("Useful-work miner runtime status");
  kv([
    ["jobs.inFlight", roll.jobs.inFlight],
    ["jobs.terminal", roll.jobs.terminal],
    ["lastUpdatedAt", roll.jobs.lastUpdatedAt ?? "—"],
    ["settlement.ready.count", roll.settlement.ready.length],
    ["settlement.totalANM", roll.settlement.totalFormatted],
  ]);
  header("Per-worker");
  table(
    ["worker", "jobs", "paid", "rewardANM"],
    roll.byWorker.map((w) => [w.worker, w.jobsTotal, w.jobsPaid, w.rewardsFormatted]),
  );
  header("Per-address");
  table(
    ["address", "jobs", "paid", "rewardANM"],
    roll.byAddress.map((a) => [
      a.address.length > 24 ? a.address.slice(0, 22) + "…" : a.address,
      a.jobsTotal,
      a.jobsPaid,
      a.rewardsFormatted,
    ]),
  );
  if (!roll.byAddress.length && !roll.byWorker.length) {
    info(c.dim("(no jobs processed yet — run `animica-agent miner start --once` to begin)"));
  }
  void fail;
  return 0;
}
