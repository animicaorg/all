import cors from "cors";
import express, { type Request, type Response, type NextFunction } from "express";
import { z } from "zod";

import { loadConfig } from "./config.js";
import { openDb } from "./db.js";
import { Repo } from "./repo.js";
import { BADGES, TUTORIALS, tutorialById, stepById } from "./catalog.js";
import {
  getPoolBalanceBaseUnits,
  submitTransfer,
  verifySignature,
} from "./chain.js";
import { animToBaseUnits } from "./units.js";

const cfg = loadConfig();
const db = openDb(cfg.dbPath);
const repo = new Repo(db);
repo.seedCatalog();

const app = express();

app.disable("x-powered-by");
app.use(express.json({ limit: "32kb" }));
app.use(
  cors({
    origin: (origin, cb) => {
      if (!origin) return cb(null, true);
      if (cfg.corsOrigins.includes("*")) return cb(null, true);
      if (cfg.corsOrigins.includes(origin)) return cb(null, true);
      cb(new Error(`origin ${origin} not allowed`));
    },
  }),
);

const ADDR_RE = /^anim1[a-z0-9]{6,80}$/i;
const addressSchema = z.string().regex(ADDR_RE, "invalid address");

app.get("/health", (_req, res) => {
  res.json({ ok: true, payoutsEnabled: cfg.payoutsEnabled });
});

app.get("/tutorials", (_req, res) => {
  res.json(
    TUTORIALS.map((t) => ({
      id: t.id,
      title: t.title,
      description: t.description,
      difficulty: t.difficulty,
      rewardAnim: t.rewardAnim,
      estimatedMinutes: t.estimatedMinutes,
      steps: t.steps.length,
    })),
  );
});

app.get("/tutorials/:id", (req, res) => {
  const t = tutorialById(req.params.id);
  if (!t) return res.status(404).json({ error: "tutorial not found" });
  res.json({
    summary: {
      id: t.id,
      title: t.title,
      description: t.description,
      difficulty: t.difficulty,
      rewardAnim: t.rewardAnim,
      estimatedMinutes: t.estimatedMinutes,
      steps: t.steps.length,
    },
    steps: t.steps.map((s) => ({ id: s.id, title: s.title, rewardAnim: s.rewardAnim })),
  });
});

app.get("/progress/:address", (req, res) => {
  const parsed = addressSchema.safeParse(req.params.address);
  if (!parsed.success) return res.status(400).json({ error: parsed.error.issues[0]?.message ?? "invalid address" });
  const rows = repo.getProgress(parsed.data);
  res.json({
    progress: rows.map((r) => ({
      tutorialId: r.tutorial_id,
      stepId: r.step_id,
      completedAt: new Date(r.completed_at * 1000).toISOString(),
    })),
  });
});

app.get("/achievements/:address", (req, res) => {
  const parsed = addressSchema.safeParse(req.params.address);
  if (!parsed.success) return res.status(400).json({ error: parsed.error.issues[0]?.message ?? "invalid address" });
  res.json({ badges: repo.getAchievements(parsed.data) });
});

const startClaimSchema = z.object({
  tutorialId: z.string().min(1),
  stepId: z.string().min(1),
  address: addressSchema,
});

app.post("/claim/start", (req, res) => {
  const parsed = startClaimSchema.safeParse(req.body);
  if (!parsed.success) return res.status(400).json({ error: parsed.error.issues[0]?.message ?? "bad request" });
  const { tutorialId, stepId, address } = parsed.data;
  const tutorial = tutorialById(tutorialId);
  if (!tutorial) return res.status(404).json({ error: "tutorial not found" });
  const step = stepById(tutorial, stepId);
  if (!step) return res.status(404).json({ error: "step not found" });
  const { nonce } = repo.issueNonce(address, tutorialId, stepId);
  // The displayed reward is the full tutorial payout when finalizing on
  // the last step; otherwise the per-step amount.
  const isFinalStep = tutorial.steps[tutorial.steps.length - 1]?.id === stepId;
  res.json({ nonce, rewardAnim: isFinalStep ? tutorial.rewardAnim : step.rewardAnim });
});

const finalizeSchema = z.object({
  tutorialId: z.string().min(1),
  stepId: z.string().min(1),
  address: addressSchema,
  nonce: z.string().min(16),
  signature: z.string().min(2),
  message: z.string().min(20).max(2_000),
});

app.post("/claim/finalize", async (req, res) => {
  const parsed = finalizeSchema.safeParse(req.body);
  if (!parsed.success) return res.status(400).json({ error: parsed.error.issues[0]?.message ?? "bad request" });
  const { tutorialId, stepId, address, nonce, signature, message } = parsed.data;

  const tutorial = tutorialById(tutorialId);
  if (!tutorial) return res.status(404).json({ error: "tutorial not found" });
  const step = stepById(tutorial, stepId);
  if (!step) return res.status(404).json({ error: "step not found" });

  // Make sure the signed message matches what the client claims to have
  // signed. The client constructs the message from these same parts; we
  // reject when they diverge so a stolen signature for prompt A can't
  // claim a payout for prompt B.
  const expected = [
    "Animica Academy completion attestation",
    `tutorial: ${tutorialId}`,
    `step: ${stepId}`,
    `address: ${address}`,
    `nonce: ${nonce}`,
  ].join("\n");
  if (message !== expected) {
    return res.status(400).json({ error: "message does not match attestation template" });
  }

  const nonceCheck = repo.consumeNonce(nonce, address, tutorialId, stepId, cfg.nonceTtlSeconds);
  if (!nonceCheck.ok) {
    return res.json({ paid: false, txHash: null, amountBaseUnits: "0", reason: nonceCheck.reason });
  }

  const sigOk = await verifySignature(cfg, message, signature, address);
  if (!sigOk) {
    repo.redeemNonce(nonce, null);
    return res.status(401).json({ error: "signature verification failed" });
  }

  // Record the step as completed before we attempt payout — this way, if
  // the on-chain submission fails we still credit progress and the user
  // can retry the claim through a new nonce.
  repo.recordProgress(address, tutorialId, stepId, signature);

  // Determine the payout amount: per-step reward, or the full tutorial
  // reward if this is the final step and all the earlier steps have
  // already been recorded.
  let amountAnim: string;
  const isFinalStep = tutorial.steps[tutorial.steps.length - 1]?.id === stepId;
  if (isFinalStep && repo.countCompletedSteps(address, tutorialId) >= tutorial.steps.length) {
    amountAnim = tutorial.rewardAnim;
  } else {
    amountAnim = step.rewardAnim;
  }
  const amountBaseUnits = animToBaseUnits(amountAnim);

  // Refuse if the pool can't cover the claim. We check the cached
  // value first for a fast path; if it looks short we re-read from the
  // chain to avoid a stale-cache false negative.
  let cachedBalance = BigInt(repo.getPoolStats().balanceBaseUnits);
  if (cachedBalance < amountBaseUnits) {
    try {
      cachedBalance = await getPoolBalanceBaseUnits(cfg);
      repo.setPoolStat("balance_base_units", cachedBalance.toString());
    } catch {
      /* keep cached estimate */
    }
  }
  if (cfg.payoutsEnabled && cachedBalance < amountBaseUnits) {
    repo.redeemNonce(nonce, null);
    return res.json({
      paid: false,
      txHash: null,
      amountBaseUnits: amountBaseUnits.toString(),
      reason: "reward pool depleted",
    });
  }

  let txHash: string | null = null;
  try {
    txHash = await submitTransfer(cfg, address, amountBaseUnits, `academy:${tutorialId}:${stepId}`);
  } catch (e) {
    repo.redeemNonce(nonce, null);
    return res
      .status(502)
      .json({ paid: false, txHash: null, amountBaseUnits: amountBaseUnits.toString(), reason: (e as Error).message });
  }

  repo.redeemNonce(nonce, txHash);
  if (txHash !== null) {
    repo.incrementPoolStat("balance_base_units", -amountBaseUnits);
    repo.incrementPoolStat("paid_out_base_units", amountBaseUnits);
  }

  // Award track badges if the user has now completed every required tutorial.
  for (const badge of BADGES) {
    const allDone = badge.requiredTutorials.every(
      (tid) => {
        const t = tutorialById(tid);
        return t && repo.countCompletedSteps(address, tid) >= t.steps.length;
      },
    );
    if (allDone) {
      const newlyAwarded = repo.recordAchievement(address, badge.id, badge.rewardAnim, null);
      if (newlyAwarded && cfg.payoutsEnabled) {
        try {
          const bonusUnits = animToBaseUnits(badge.rewardAnim);
          const bonusTx = await submitTransfer(cfg, address, bonusUnits, `academy-badge:${badge.id}`);
          if (bonusTx !== null) {
            repo.incrementPoolStat("balance_base_units", -bonusUnits);
            repo.incrementPoolStat("paid_out_base_units", bonusUnits);
          }
        } catch {
          // Bonus failure is non-fatal — the badge is awarded, the bonus
          // can be retopped manually if needed.
        }
      }
    }
  }

  res.json({
    paid: txHash !== null || !cfg.payoutsEnabled,
    txHash,
    amountBaseUnits: amountBaseUnits.toString(),
  });
});

app.get("/pool/status", async (_req, res) => {
  const stats = repo.getPoolStats();
  let balance = BigInt(stats.balanceBaseUnits);
  try {
    balance = await getPoolBalanceBaseUnits(cfg);
    repo.setPoolStat("balance_base_units", balance.toString());
  } catch {
    /* chain unreachable — use last cached */
  }
  res.json({
    poolAddress: cfg.rewardPoolAddress,
    balanceBaseUnits: balance.toString(),
    paidOutBaseUnits: stats.paidOutBaseUnits,
    pendingClaimsBaseUnits: stats.pendingClaimsBaseUnits,
    contributors: repo.getContributorCount(),
  });
});

const depositSchema = z.object({
  address: addressSchema,
  txHash: z.string().min(8).max(200),
  amountAnim: z.string().regex(/^\d+(\.\d{1,9})?$/),
});

app.post("/pool/deposit", (req, res) => {
  const parsed = depositSchema.safeParse(req.body);
  if (!parsed.success) return res.status(400).json({ error: parsed.error.issues[0]?.message ?? "bad request" });
  const { address, txHash, amountAnim } = parsed.data;
  // The actual on-chain transfer is authoritative; this endpoint is only
  // for the contributor leaderboard. The indexer/replay loop reconciles
  // the canonical balance from chain state.
  const recorded = repo.recordDeposit(address, txHash, amountAnim);
  res.json({ recorded });
});

app.use((err: Error, _req: Request, res: Response, _next: NextFunction) => {
  console.error("[academy] unhandled", err);
  res.status(500).json({ error: err.message });
});

app.listen(cfg.port, () => {
  console.log(
    `[academy] listening on :${cfg.port} · payouts=${cfg.payoutsEnabled ? "on" : "off"} · pool=${cfg.rewardPoolAddress}`,
  );
});
