import type Database from "better-sqlite3";
import { randomBytes } from "node:crypto";
import { TUTORIALS, BADGES } from "./catalog.js";

/**
 * Thin data-access wrappers over the SQLite store. Keeping these on a
 * single repo class makes it easy to swap in Postgres later without
 * touching the route handlers.
 */
export class Repo {
  constructor(private readonly db: Database.Database) {}

  /** Seeds catalog rows (idempotent) so JOINs in stats queries are clean. */
  seedCatalog(): void {
    const tx = this.db.transaction(() => {
      const upsertTutorial = this.db.prepare(
        `INSERT INTO tutorials (id, title, description, track, difficulty, reward_anim, estimated_minutes, step_count, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, strftime('%s', 'now'))
         ON CONFLICT(id) DO UPDATE SET
           title = excluded.title,
           description = excluded.description,
           track = excluded.track,
           difficulty = excluded.difficulty,
           reward_anim = excluded.reward_anim,
           estimated_minutes = excluded.estimated_minutes,
           step_count = excluded.step_count,
           updated_at = excluded.updated_at`,
      );
      const upsertStep = this.db.prepare(
        `INSERT INTO tutorial_steps (tutorial_id, step_id, ordinal, title, reward_anim)
         VALUES (?, ?, ?, ?, ?)
         ON CONFLICT(tutorial_id, step_id) DO UPDATE SET
           ordinal = excluded.ordinal,
           title = excluded.title,
           reward_anim = excluded.reward_anim`,
      );
      for (const t of TUTORIALS) {
        upsertTutorial.run(
          t.id,
          t.title,
          t.description,
          t.track,
          t.difficulty,
          t.rewardAnim,
          t.estimatedMinutes,
          t.steps.length,
        );
        t.steps.forEach((s, i) => {
          upsertStep.run(t.id, s.id, i, s.title, s.rewardAnim);
        });
      }
    });
    tx();
  }

  listTutorials() {
    return this.db
      .prepare(
        `SELECT id, title, description, difficulty, reward_anim, estimated_minutes, step_count
         FROM tutorials ORDER BY track, id`,
      )
      .all() as Array<{
      id: string;
      title: string;
      description: string;
      difficulty: string;
      reward_anim: string;
      estimated_minutes: number;
      step_count: number;
    }>;
  }

  getProgress(address: string) {
    return this.db
      .prepare(
        `SELECT tutorial_id, step_id, completed_at FROM progress WHERE address = ? ORDER BY completed_at`,
      )
      .all(address) as Array<{
      tutorial_id: string;
      step_id: string;
      completed_at: number;
    }>;
  }

  recordProgress(
    address: string,
    tutorialId: string,
    stepId: string,
    signatureHex: string | null,
  ): void {
    this.db
      .prepare(
        `INSERT INTO progress (address, tutorial_id, step_id, signature_hex)
         VALUES (?, ?, ?, ?)
         ON CONFLICT(address, tutorial_id, step_id) DO NOTHING`,
      )
      .run(address, tutorialId, stepId, signatureHex);
  }

  countCompletedSteps(address: string, tutorialId: string): number {
    return (
      this.db
        .prepare(
          `SELECT COUNT(*) AS n FROM progress WHERE address = ? AND tutorial_id = ?`,
        )
        .get(address, tutorialId) as { n: number }
    ).n;
  }

  issueNonce(
    address: string,
    tutorialId: string,
    stepId: string,
  ): { nonce: string } {
    const nonce = randomBytes(16).toString("hex");
    this.db
      .prepare(
        `INSERT INTO claim_nonces (nonce, address, tutorial_id, step_id) VALUES (?, ?, ?, ?)`,
      )
      .run(nonce, address, tutorialId, stepId);
    return { nonce };
  }

  consumeNonce(
    nonce: string,
    address: string,
    tutorialId: string,
    stepId: string,
    ttlSeconds: number,
  ): { ok: true } | { ok: false; reason: string } {
    const row = this.db
      .prepare(
        `SELECT address, tutorial_id, step_id, issued_at, redeemed_at FROM claim_nonces WHERE nonce = ?`,
      )
      .get(nonce) as
      | {
          address: string;
          tutorial_id: string;
          step_id: string;
          issued_at: number;
          redeemed_at: number | null;
        }
      | undefined;
    if (!row) return { ok: false, reason: "nonce not found" };
    if (row.redeemed_at !== null) return { ok: false, reason: "nonce already redeemed" };
    const now = Math.floor(Date.now() / 1000);
    if (now - row.issued_at > ttlSeconds) return { ok: false, reason: "nonce expired" };
    if (
      row.address !== address ||
      row.tutorial_id !== tutorialId ||
      row.step_id !== stepId
    ) {
      return { ok: false, reason: "nonce mismatch" };
    }
    return { ok: true };
  }

  redeemNonce(nonce: string, payoutTxHash: string | null): void {
    this.db
      .prepare(
        `UPDATE claim_nonces SET redeemed_at = strftime('%s', 'now'), payout_tx_hash = ? WHERE nonce = ?`,
      )
      .run(payoutTxHash, nonce);
  }

  recordAchievement(
    address: string,
    badgeId: string,
    rewardAnim: string,
    payoutTxHash: string | null,
  ): boolean {
    const r = this.db
      .prepare(
        `INSERT OR IGNORE INTO achievements (address, badge_id, reward_anim, payout_tx_hash) VALUES (?, ?, ?, ?)`,
      )
      .run(address, badgeId, rewardAnim, payoutTxHash);
    return r.changes > 0;
  }

  getAchievements(address: string) {
    const earned = this.db
      .prepare(`SELECT badge_id, awarded_at, reward_anim FROM achievements WHERE address = ?`)
      .all(address) as Array<{
      badge_id: string;
      awarded_at: number;
      reward_anim: string;
    }>;
    const byId = new Map(earned.map((b) => [b.badge_id, b]));
    return BADGES.map((b) => {
      const row = byId.get(b.id);
      return {
        id: b.id,
        title: b.title,
        description: b.description,
        awardedAt: row ? new Date(row.awarded_at * 1000).toISOString() : null,
        rewardAnim: b.rewardAnim,
      };
    });
  }

  recordDeposit(address: string, txHash: string, amountAnim: string): boolean {
    const r = this.db
      .prepare(
        `INSERT OR IGNORE INTO deposits (tx_hash, address, amount_anim) VALUES (?, ?, ?)`,
      )
      .run(txHash, address, amountAnim);
    return r.changes > 0;
  }

  getContributorCount(): number {
    return (
      this.db
        .prepare(`SELECT COUNT(DISTINCT address) AS n FROM deposits`)
        .get() as { n: number }
    ).n;
  }

  getPoolStats() {
    const rows = this.db
      .prepare(`SELECT key, value FROM pool_stats`)
      .all() as Array<{ key: string; value: string }>;
    const stats = Object.fromEntries(rows.map((r) => [r.key, r.value]));
    return {
      balanceBaseUnits: stats.balance_base_units ?? "0",
      paidOutBaseUnits: stats.paid_out_base_units ?? "0",
      pendingClaimsBaseUnits: stats.pending_claims_base_units ?? "0",
    };
  }

  setPoolStat(key: string, value: string): void {
    this.db
      .prepare(
        `INSERT INTO pool_stats (key, value, updated_at) VALUES (?, ?, strftime('%s', 'now'))
         ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`,
      )
      .run(key, value);
  }

  incrementPoolStat(key: string, deltaBaseUnits: bigint): void {
    const cur = BigInt(
      (
        this.db.prepare(`SELECT value FROM pool_stats WHERE key = ?`).get(key) as
          | { value: string }
          | undefined
      )?.value ?? "0",
    );
    this.setPoolStat(key, (cur + deltaBaseUnits).toString());
  }
}
