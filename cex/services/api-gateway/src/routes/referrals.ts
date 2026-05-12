import { randomBytes } from "node:crypto";
import { Router } from "express";
import type { NextFunction, Response } from "express";
import type { Pool, PoolClient } from "pg";
import { z } from "zod";
import {
  createApiKeyVerifier,
  createRequireAuth,
  requireApiKeyScope,
  type AuthenticatedRequest,
} from "./authenticated.js";

const AIRDROP_SETTINGS_ID = "default";
const AIRDROP_ASSET = "ANM";
const AIRDROP_POOL_ACCOUNT_ID = "system:airdrop";
const ANM_DECIMALS = 9;
const DEFAULT_REWARD_ATOMS = "100000000000";
const CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const ACTIVATION_EVENTS = new Set<ReferralQualificationEvent>(["airdrop_claim", "order_submitted"]);

export type ReferralQualificationEvent = "airdrop_claim" | "order_submitted" | "status_check" | "manual";

export type ReferralProcessingOptions = {
  rewardAtoms?: string;
  requireEmailVerified?: boolean;
  minAccountAgeSeconds?: number;
};

type ReferralsRouterOptions = ReferralProcessingOptions & {
  authServiceUrl: string;
  frontendUrl: string;
  adminApiKey?: string;
};

type Queryable = Pick<Pool | PoolClient, "query">;

type ReferralRow = {
  id: string;
  status: string;
  reward_atoms?: string | number | null;
  referred_reward_atoms?: string | number | null;
  referrer_user_id: string;
  referred_user_id: string;
  metadata?: unknown;
  email_verified?: boolean;
  last_login_at?: Date | string | null;
  user_created_at?: Date | string | null;
};

function atomsToDecimal(atoms: string | bigint, decimals: number): string {
  const value = typeof atoms === "bigint" ? atoms : BigInt(atoms || "0");
  const raw = value.toString().padStart(decimals + 1, "0");
  const whole = raw.slice(0, -decimals) || "0";
  const fraction = raw.slice(-decimals).replace(/0+$/, "");
  return `${whole}${fraction ? `.${fraction}` : ""}`;
}

function asAtomBigInt(value: unknown): bigint {
  return BigInt(String(value ?? "0"));
}

function parseMetadata(value: unknown): Record<string, any> {
  if (!value) return {};
  if (typeof value === "string") {
    try {
      return JSON.parse(value);
    } catch {
      return {};
    }
  }
  return typeof value === "object" ? { ...(value as Record<string, any>) } : {};
}

function generateReferralCode(): string {
  const bytes = randomBytes(8);
  let code = "";
  for (const byte of bytes) {
    code += CODE_ALPHABET[byte % CODE_ALPHABET.length];
  }
  return code;
}

function normalizeFrontendUrl(value: string): string {
  return value.endsWith("/") ? value.slice(0, -1) : value;
}

function maskEmail(email: string | null | undefined): string | null {
  if (!email) return null;
  const [local, domain] = email.split("@");
  if (!domain) return null;
  const prefix = local.slice(0, 2);
  return `${prefix}${"*".repeat(Math.max(2, local.length - prefix.length))}@${domain}`;
}

async function ensureAirdropSettings(client: Queryable): Promise<{ asset: string; poolAccountId: string }> {
  await client.query(
    `
      INSERT INTO airdrop_settings (
        id, asset, claim_amount, claim_amount_atoms, cooldown_seconds, enabled, pool_account_id
      )
      VALUES ($1, $2, 1, 1000000000, 14400, true, $3)
      ON CONFLICT (id) DO NOTHING
    `,
    [AIRDROP_SETTINGS_ID, AIRDROP_ASSET, AIRDROP_POOL_ACCOUNT_ID]
  );

  const result = await client.query(
    `
      SELECT asset, pool_account_id
      FROM airdrop_settings
      WHERE id = $1
      LIMIT 1
    `,
    [AIRDROP_SETTINGS_ID]
  );

  return {
    asset: String(result.rows[0]?.asset ?? AIRDROP_ASSET),
    poolAccountId: String(result.rows[0]?.pool_account_id ?? AIRDROP_POOL_ACCOUNT_ID),
  };
}

async function ensureReferralCode(client: Queryable, userId: string): Promise<string> {
  const existing = await client.query(
    "SELECT code FROM referral_codes WHERE user_id = $1::uuid AND active = true LIMIT 1",
    [userId]
  );
  if (existing.rows[0]?.code) {
    return String(existing.rows[0].code);
  }

  for (let attempt = 0; attempt < 8; attempt += 1) {
    try {
      const result = await client.query(
        `
          INSERT INTO referral_codes (user_id, code, active)
          VALUES ($1::uuid, $2, true)
          ON CONFLICT (user_id)
          DO UPDATE SET active = true, updated_at = NOW()
          RETURNING code
        `,
        [userId, generateReferralCode()]
      );
      return String(result.rows[0].code);
    } catch (error: any) {
      if (error?.code !== "23505") throw error;
    }
  }

  throw new Error("Could not create referral code");
}

async function updateReferralPendingState(
  client: Queryable,
  referralId: string,
  status: string,
  reason: string,
  metadata: Record<string, any>
) {
  await client.query(
    `
      UPDATE referrals
      SET status = $2,
          qualification_reason = $3,
          metadata = $4::jsonb,
          updated_at = NOW()
      WHERE id = $1::uuid
    `,
    [referralId, status, reason, JSON.stringify(metadata)]
  );
}

async function creditReferralRewards(
  client: Queryable,
  referral: ReferralRow,
  rewardAtoms: string,
  metadata: Record<string, any>
): Promise<{ status: string; reason: string }> {
  const credited = await client.query(
    `
      SELECT reward_role
      FROM referral_reward_events
      WHERE referral_id = $1::uuid
        AND status = 'credited'
        AND reward_role IN ('referrer', 'referred')
      FOR UPDATE
    `,
    [referral.id]
  );

  const creditedRoles = new Set(
    credited.rows
      .map((row: any) => String(row.reward_role))
      .filter((role: string) => role === "referrer" || role === "referred")
  );

  const rewards: Array<{ role: "referrer" | "referred"; userId: string; amountAtoms: string }> = [];
  if (!creditedRoles.has("referrer")) {
    rewards.push({ role: "referrer", userId: referral.referrer_user_id, amountAtoms: rewardAtoms });
  }
  if (!creditedRoles.has("referred")) {
    rewards.push({
      role: "referred",
      userId: referral.referred_user_id,
      amountAtoms: String(referral.referred_reward_atoms ?? DEFAULT_REWARD_ATOMS),
    });
  }

  if (rewards.length === 0) {
    await updateReferralPendingState(client, referral.id, "rewarded", "already_rewarded", metadata);
    return { status: "rewarded", reason: "already_rewarded" };
  }

  const settings = await ensureAirdropSettings(client);
  if (settings.asset !== AIRDROP_ASSET) {
    await updateReferralPendingState(client, referral.id, "pending", "airdrop_asset_not_anm", metadata);
    return { status: "pending", reason: "airdrop_asset_not_anm" };
  }

  await client.query(
    `
      INSERT INTO balances (account_id, asset, available, locked, available_atoms, locked_atoms, updated_at)
      VALUES ($1, $2, 0, 0, 0, 0, NOW())
      ON CONFLICT (account_id, asset) DO NOTHING
    `,
    [settings.poolAccountId, settings.asset]
  );

  const pool = await client.query(
    `
      SELECT available_atoms
      FROM balances
      WHERE account_id = $1
        AND asset = $2
      FOR UPDATE
    `,
    [settings.poolAccountId, settings.asset]
  );

  const totalRewardAtoms = rewards.reduce((sum, reward) => sum + asAtomBigInt(reward.amountAtoms), 0n);
  if (asAtomBigInt(pool.rows[0]?.available_atoms) < totalRewardAtoms) {
    for (const reward of rewards) {
      await client.query(
        `
          INSERT INTO referral_reward_events (
            referral_id,
            amount_atoms,
            asset,
            source,
            status,
            reward_role,
            recipient_user_id,
            metadata
          )
          VALUES ($1::uuid, $2::numeric, $3, 'airdrop_pool', 'insufficient_pool', $4, $5::uuid, $6::jsonb)
        `,
        [
          referral.id,
          reward.amountAtoms,
          settings.asset,
          reward.role,
          reward.userId,
          JSON.stringify({
            attemptedAt: new Date().toISOString(),
            requiredTotalAtoms: totalRewardAtoms.toString(),
          }),
        ]
      );
    }
    await updateReferralPendingState(client, referral.id, "pending_insufficient_pool", "airdrop_pool_insufficient", metadata);
    return { status: "pending_insufficient_pool", reason: "airdrop_pool_insufficient" };
  }

  await client.query(
    `
      UPDATE balances
      SET available = available - $3::numeric,
          available_atoms = available_atoms - $4::numeric,
          updated_at = NOW()
      WHERE account_id = $1
        AND asset = $2
    `,
    [settings.poolAccountId, settings.asset, atomsToDecimal(totalRewardAtoms, ANM_DECIMALS), totalRewardAtoms.toString()]
  );

  for (const reward of rewards) {
    await client.query(
      `
        INSERT INTO balances (account_id, asset, available, locked, available_atoms, locked_atoms, updated_at)
        VALUES ($1, $2, $3::numeric, 0, $4::numeric, 0, NOW())
        ON CONFLICT (account_id, asset)
        DO UPDATE SET
          available = balances.available + EXCLUDED.available,
          available_atoms = balances.available_atoms + EXCLUDED.available_atoms,
          updated_at = NOW()
      `,
      [`user:${reward.userId}`, settings.asset, atomsToDecimal(reward.amountAtoms, ANM_DECIMALS), reward.amountAtoms]
    );

    await client.query(
      `
        INSERT INTO referral_reward_events (
          referral_id,
          amount_atoms,
          asset,
          source,
          status,
          reward_role,
          recipient_user_id,
          metadata
        )
        VALUES ($1::uuid, $2::numeric, $3, 'airdrop_pool', 'credited', $4, $5::uuid, $6::jsonb)
      `,
      [
        referral.id,
        reward.amountAtoms,
        settings.asset,
        reward.role,
        reward.userId,
        JSON.stringify({
          creditedAt: new Date().toISOString(),
          referrerUserId: referral.referrer_user_id,
          referredUserId: referral.referred_user_id,
          rewardRole: reward.role,
        }),
      ]
    );
  }

  const paidRoles = new Set([...creditedRoles, ...rewards.map((reward) => reward.role)]);
  const rewardReferrerNow = rewards.some((reward) => reward.role === "referrer");
  const rewardReferredNow = rewards.some((reward) => reward.role === "referred");
  const fullyRewarded = paidRoles.has("referrer") && paidRoles.has("referred");

  await client.query(
    `
      UPDATE referrals
      SET status = $2,
          qualification_reason = $3,
          rewarded_at = CASE WHEN $4 THEN COALESCE(rewarded_at, NOW()) ELSE rewarded_at END,
          referred_rewarded_at = CASE WHEN $5 THEN COALESCE(referred_rewarded_at, NOW()) ELSE referred_rewarded_at END,
          metadata = $6::jsonb,
          updated_at = NOW()
      WHERE id = $1::uuid
    `,
    [
      referral.id,
      fullyRewarded ? "rewarded" : "qualified",
      fullyRewarded ? "rewarded" : "partial_rewarded",
      rewardReferrerNow,
      rewardReferredNow,
      JSON.stringify(metadata),
    ]
  );

  return {
    status: fullyRewarded ? "rewarded" : "qualified",
    reason: fullyRewarded ? "rewarded" : "partial_rewarded",
  };
}

export async function processReferralQualification(
  client: PoolClient,
  referredUserId: string,
  event: ReferralQualificationEvent,
  options: ReferralProcessingOptions = {}
): Promise<{ status: string; reason: string }> {
  await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [`referral:${referredUserId}`]);

  const result = await client.query(
    `
      SELECT
        referrals.id::text,
        referrals.status,
        referrals.reward_atoms::text,
        referrals.referred_reward_atoms::text,
        referrals.referrer_user_id::text,
        referrals.referred_user_id::text,
        referrals.metadata,
        users.email_verified,
        users.last_login_at,
        users.created_at AS user_created_at
      FROM referrals
      JOIN users ON users.id = referrals.referred_user_id
      WHERE referrals.referred_user_id = $1::uuid
      LIMIT 1
      FOR UPDATE OF referrals
    `,
    [referredUserId]
  );

  const referral = result.rows[0] as ReferralRow | undefined;
  if (!referral || referral.status === "rejected") {
    return { status: referral?.status ?? "none", reason: "not_processable" };
  }

  const metadata = parseMetadata(referral.metadata);
  if (referral.status === "rewarded") {
    return creditReferralRewards(client, referral, options.rewardAtoms ?? String(referral.reward_atoms ?? DEFAULT_REWARD_ATOMS), metadata);
  }

  if (ACTIVATION_EVENTS.has(event)) {
    metadata.activationEvents = {
      ...(metadata.activationEvents ?? {}),
      [event]: new Date().toISOString(),
    };
  }

  const requireEmailVerified = options.requireEmailVerified ?? true;
  if (requireEmailVerified && !referral.email_verified) {
    await updateReferralPendingState(client, referral.id, "pending", "waiting_email_verification", metadata);
    return { status: "pending", reason: "waiting_email_verification" };
  }

  if (!referral.last_login_at) {
    await updateReferralPendingState(client, referral.id, "pending", "waiting_first_login", metadata);
    return { status: "pending", reason: "waiting_first_login" };
  }

  const minAgeMs = Math.max(0, options.minAccountAgeSeconds ?? 0) * 1000;
  if (minAgeMs > 0) {
    const createdAt = new Date(referral.user_created_at ?? 0).getTime();
    if (Date.now() - createdAt < minAgeMs) {
      await updateReferralPendingState(client, referral.id, "pending", "waiting_account_age", metadata);
      return { status: "pending", reason: "waiting_account_age" };
    }
  }

  const activationEvents = metadata.activationEvents ?? {};
  const activated = Boolean(activationEvents.airdrop_claim || activationEvents.order_submitted);
  if (!activated) {
    await updateReferralPendingState(client, referral.id, "pending", "waiting_activation", metadata);
    return { status: "pending", reason: "waiting_activation" };
  }

  await updateReferralPendingState(client, referral.id, "qualified", "qualified", metadata);
  return creditReferralRewards(client, referral, options.rewardAtoms ?? String(referral.reward_atoms ?? DEFAULT_REWARD_ATOMS), metadata);
}

export async function processReferralQualificationInTransaction(
  pgPool: Pool,
  referredUserId: string,
  event: ReferralQualificationEvent,
  options: ReferralProcessingOptions = {}
): Promise<{ status: string; reason: string }> {
  const client = await pgPool.connect();
  try {
    await client.query("BEGIN");
    const result = await processReferralQualification(client, referredUserId, event, options);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw error;
  } finally {
    client.release();
  }
}

function requireAdminKey(options: ReferralsRouterOptions) {
  return (req: AuthenticatedRequest, res: Response, next: NextFunction) => {
    const configured = options.adminApiKey?.trim();
    if (!configured) {
      return res.status(403).json({ error: "Admin key is not configured" });
    }

    const provided = typeof req.headers["x-admin-api-key"] === "string" ? req.headers["x-admin-api-key"].trim() : "";
    if (provided !== configured) {
      return res.status(403).json({ error: "Invalid admin key" });
    }
    return next();
  };
}

export function createReferralsRouter(pgPool: Pool, options: ReferralsRouterOptions): Router {
  const router = Router();
  const requireAuth = createRequireAuth(options.authServiceUrl, {
    verifyApiKey: createApiKeyVerifier(pgPool),
  });
  const requireReadScope = requireApiKeyScope("read");
  const frontendUrl = normalizeFrontendUrl(options.frontendUrl);

  router.get("/me/referral", requireAuth, requireReadScope, async (req: AuthenticatedRequest, res) => {
    try {
      await processReferralQualificationInTransaction(pgPool, req.userId!, "status_check", options).catch(() => undefined);
      const code = await ensureReferralCode(pgPool, req.userId!);
      const stats = await pgPool.query(
        `
          SELECT
            COUNT(*)::int AS total_referrals,
            COUNT(*) FILTER (WHERE status IN ('qualified', 'rewarded'))::int AS qualified_referrals,
            COUNT(*) FILTER (WHERE status = 'rewarded')::int AS rewarded_referrals,
            COALESCE(SUM(CASE WHEN status = 'rewarded' THEN reward_atoms ELSE 0 END), 0)::text AS total_earned_atoms
          FROM referrals
          WHERE referrer_user_id = $1::uuid
        `,
        [req.userId]
      );
      const recent = await pgPool.query(
        `
          SELECT referrals.id::text,
                 referrals.status,
                 referrals.qualification_reason,
                 referrals.reward_atoms::text,
                 referrals.rewarded_at,
                 referrals.created_at,
                 users.email
          FROM referrals
          JOIN users ON users.id = referrals.referred_user_id
          WHERE referrals.referrer_user_id = $1::uuid
          ORDER BY referrals.created_at DESC
          LIMIT 10
        `,
        [req.userId]
      );

      const row = stats.rows[0] ?? {};
      const earnedAtoms = String(row.total_earned_atoms ?? "0");
      res.json({
        code,
        referralLink: `${frontendUrl}/register?ref=${encodeURIComponent(code)}`,
        reward: {
          asset: AIRDROP_ASSET,
          amount: atomsToDecimal(options.rewardAtoms ?? DEFAULT_REWARD_ATOMS, ANM_DECIMALS),
          amountAtoms: options.rewardAtoms ?? DEFAULT_REWARD_ATOMS,
          signupAmount: atomsToDecimal(DEFAULT_REWARD_ATOMS, ANM_DECIMALS),
          signupAmountAtoms: DEFAULT_REWARD_ATOMS,
          source: "airdrop_pool",
        },
        totals: {
          referrals: Number(row.total_referrals ?? 0),
          qualified: Number(row.qualified_referrals ?? 0),
          rewarded: Number(row.rewarded_referrals ?? 0),
          earnedAtoms,
          earned: atomsToDecimal(earnedAtoms, ANM_DECIMALS),
        },
        recent: recent.rows.map((item: any) => ({
          id: item.id,
          status: item.status,
          reason: item.qualification_reason,
          referredEmail: maskEmail(item.email),
          rewardAtoms: item.reward_atoms,
          reward: atomsToDecimal(item.reward_atoms, ANM_DECIMALS),
          createdAt: item.created_at,
          rewardedAt: item.rewarded_at,
        })),
      });
    } catch (error) {
      console.error("Error fetching referral summary:", error);
      res.status(500).json({ error: "Failed to fetch referral summary" });
    }
  });

  router.get("/me/referral/history", requireAuth, requireReadScope, async (req: AuthenticatedRequest, res) => {
    try {
      const query = z
        .object({
          limit: z.coerce.number().int().min(1).max(100).default(25),
          offset: z.coerce.number().int().min(0).default(0),
        })
        .parse(req.query);

      const result = await pgPool.query(
        `
          SELECT referrals.id::text,
                 referrals.status,
                 referrals.qualification_reason,
                 referrals.reward_atoms::text,
                 referrals.rewarded_at,
                 referrals.created_at,
                 referrals.updated_at,
                 users.email
          FROM referrals
          JOIN users ON users.id = referrals.referred_user_id
          WHERE referrals.referrer_user_id = $1::uuid
          ORDER BY referrals.created_at DESC
          LIMIT $2 OFFSET $3
        `,
        [req.userId, query.limit, query.offset]
      );

      res.json({
        referrals: result.rows.map((row: any) => ({
          id: row.id,
          status: row.status,
          reason: row.qualification_reason,
          referredEmail: maskEmail(row.email),
          rewardAtoms: row.reward_atoms,
          reward: atomsToDecimal(row.reward_atoms, ANM_DECIMALS),
          createdAt: row.created_at,
          updatedAt: row.updated_at,
          rewardedAt: row.rewarded_at,
        })),
      });
    } catch (error) {
      console.error("Error fetching referral history:", error);
      res.status(500).json({ error: "Failed to fetch referral history" });
    }
  });

  router.post("/internal/referrals/process", requireAdminKey(options), async (req, res) => {
    try {
      const body = z
        .object({
          referredUserId: z.string().uuid().optional(),
          limit: z.number().int().min(1).max(250).default(50),
        })
        .parse(req.body ?? {});

      const users = body.referredUserId
        ? { rows: [{ referred_user_id: body.referredUserId }] }
        : await pgPool.query(
            `
              SELECT referred_user_id::text
              FROM referrals
              WHERE status IN ('pending', 'qualified', 'pending_insufficient_pool')
              ORDER BY created_at ASC
              LIMIT $1
            `,
            [body.limit]
          );

      const results = [];
      for (const row of users.rows) {
        results.push({
          referredUserId: row.referred_user_id,
          result: await processReferralQualificationInTransaction(pgPool, row.referred_user_id, "manual", options),
        });
      }

      res.json({ processed: results.length, results });
    } catch (error) {
      console.error("Error processing referrals:", error);
      res.status(500).json({ error: "Failed to process referrals" });
    }
  });

  return router;
}
