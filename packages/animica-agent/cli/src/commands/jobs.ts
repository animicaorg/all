import {
  formatANM,
  hashArtifact,
  HttpCoordinator,
  JobRunner,
  LocalCoordinator,
  loadConfig,
  resolveWalletIdentity,
  detectMinerIdentity,
  type Coordinator,
  type Submission,
} from "@animica/agent-core";
import { join } from "node:path";
import { readFileSync, existsSync } from "node:fs";

import { boolFlag, stringFlag } from "../args.js";
import { c, fail, header, info, ok, table } from "../output.js";

function pickCoordinator(): Coordinator {
  const { config, paths } = loadConfig();
  const url = stringFlag({ url: "" } as Record<string, string | boolean>, "url");
  if (config.aicfMode === "enabled" && config.providerBaseUrl) {
    return new HttpCoordinator(config.providerBaseUrl);
  }
  if (url) return new HttpCoordinator(url);
  return new LocalCoordinator({ dataDir: join(paths.stateDir, "jobs") });
}

export async function runJobs(positionals: string[], options: Record<string, string | boolean>): Promise<number> {
  const verb = positionals[0] ?? "list";
  const coord = pickCoordinator();
  if (verb === "list") {
    const jobs = await coord.listJobs();
    header(`Jobs available via ${coord.name}`);
    table(
      ["id", "kind", "tier", "model", "reward(cap)", "expires"],
      jobs.map((j) => [
        j.id.slice(0, 8),
        j.kind,
        j.tier,
        `${j.modelTarget}@${j.modelVersion}`,
        `${formatANM(j.rewardCapRaw)} ANM`,
        j.expiresAt,
      ]),
    );
    return 0;
  }
  if (verb === "accept") {
    const id = positionals[1] ?? stringFlag(options, "id");
    if (!id) {
      fail("usage: animica-agent jobs accept <id>");
      return 64;
    }
    const job = await coord.getJob(id);
    if (!job) {
      fail("no such job");
      return 1;
    }
    header(`Job ${job.id}`);
    info(JSON.stringify(job, null, 2));
    if (boolFlag(options, "run", false)) {
      const { config, paths } = loadConfig();
      const runner = new JobRunner(config, paths.stateDir);
      const result = await runner.run(job);
      info("");
      if (result.kind === "unsupported") {
        fail(`runner did not execute: ${result.reason}`);
        return 2;
      }
      info(c.dim(`artifact: ${result.artifactPath}`));
      info(c.dim(`metric:   ${result.submission.metric.toFixed(4)}  elapsedMs=${result.submission.elapsedMs}`));
      const outcome = await coord.submit(result.submission);
      info(`submission ${result.submission.id} -> ${outcome.status} (quality=${outcome.quality.toFixed(3)})`);
      if (outcome.reason) info(c.dim(`reason: ${outcome.reason}`));
      return outcome.status === "rejected" ? 2 : 0;
    }
    info("");
    info(c.dim("Run with `--run` to execute the job locally, or submit a custom artifact:"));
    info(c.cyan(`  animica-agent jobs accept ${job.id} --run`));
    info(c.cyan(`  animica-agent jobs submit ${job.id} --artifact <path> --metric <n>`));
    return 0;
  }
  if (verb === "submit") {
    const id = positionals[1] ?? stringFlag(options, "id");
    const artifactPath = stringFlag(options, "artifact");
    const metric = Number.parseFloat(stringFlag(options, "metric", "0.5") as string);
    if (!id || !artifactPath) {
      fail("usage: animica-agent jobs submit <id> --artifact <path> --metric <n>");
      return 64;
    }
    if (!existsSync(artifactPath)) {
      fail(`artifact not found: ${artifactPath}`);
      return 1;
    }
    const buf = readFileSync(artifactPath);
    const hash = hashArtifact(buf);
    const { config } = loadConfig();
    const wallet = resolveWalletIdentity(config);
    const identity = detectMinerIdentity(config);
    const minerAddress = identity.payoutAddress ?? wallet?.address;
    if (!minerAddress) {
      fail("no miner/wallet identity; run `animica-agent miner connect` or `animica-agent wallet connect`");
      return 1;
    }
    const submission: Submission = {
      id: crypto.randomUUID(),
      jobId: id,
      minerAddress,
      worker: identity.worker,
      artifactHash: hash,
      artifactPointer: artifactPath,
      metric,
      elapsedMs: Number.parseInt(stringFlag(options, "elapsed", "1") as string, 10) || 1,
      submittedAt: new Date().toISOString(),
    };
    const outcome = await coord.submit(submission);
    ok(`submission ${submission.id} -> ${outcome.status} (quality=${outcome.quality.toFixed(3)})`);
    if (outcome.reason) info(c.dim(`reason: ${outcome.reason}`));
    void boolFlag;
    return outcome.status === "rejected" ? 2 : 0;
  }
  fail(`unknown jobs verb: ${verb}`);
  return 64;
}

export async function runRewards(options: Record<string, string | boolean>): Promise<number> {
  const { config } = loadConfig();
  const wallet = resolveWalletIdentity(config);
  const identity = detectMinerIdentity(config);
  const target = stringFlag(options, "miner") ?? identity.payoutAddress ?? wallet?.address;
  if (!target) {
    fail("no miner/wallet identity to query rewards for");
    return 64;
  }
  const coord = pickCoordinator();
  const rewards = await coord.recentRewards(target, Number.parseInt(stringFlag(options, "limit", "20") as string, 10));
  header(`Rewards for ${target}`);
  table(
    ["id", "submission", "amount", "status", "settledAt"],
    rewards.map((r) => [r.id.slice(0, 8), r.submissionId.slice(0, 8), `${formatANM(r.rawAmount)} ANM`, r.status, r.settledAt ?? ""]),
  );
  return 0;
}

export async function runLeaderboard(options: Record<string, string | boolean>): Promise<number> {
  const coord = pickCoordinator();
  const top = await coord.leaderboard(stringFlag(options, "model"), Number.parseInt(stringFlag(options, "limit", "25") as string, 10));
  header("Leaderboard");
  table(["minerAddress", "score"], top.map((r) => [r.minerAddress, r.score]));
  return 0;
}

export async function runAdapters(options: Record<string, string | boolean>): Promise<number> {
  const model = stringFlag(options, "model") ?? "animica-agent";
  const coord = pickCoordinator();
  const list = await coord.adapters(model);
  header(`Adapters for ${model}`);
  table(["id", "version", "status", "metric"], list.map((a) => [a.id.slice(0, 8), a.version, a.status, a.metric]));
  return 0;
}
