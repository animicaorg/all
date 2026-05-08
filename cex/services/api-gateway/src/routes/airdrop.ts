import { randomUUID } from "node:crypto";
import { Router } from "express";
import type { Pool, PoolClient } from "pg";
import { z } from "zod";
import {
  createApiKeyVerifier,
  createRequireAuth,
  requireApiKeyScope,
  type AuthenticatedRequest,
} from "./authenticated.js";

const AIRDROP_SETTINGS_ID = "default";
const DEFAULT_ASSET = "ANM";
const DEFAULT_DECIMALS = 9;

type AirdropRouterOptions = {
  authServiceUrl: string;
  adminApiKey?: string;
};

type AirdropSettings = {
  asset: string;
  claimAmount: string;
  claimAmountAtoms: string;
  cooldownSeconds: number;
  enabled: boolean;
  poolAccountId: string;
};

const depositSchema = z.object({
  amountAtoms: z.string().regex(/^\d+$/),
});

const settingsSchema = z.object({
  claimAmount: z.string().regex(/^\d+(\.\d+)?$/).optional(),
  cooldownSeconds: z.number().int().min(60).max(30 * 24 * 60 * 60).optional(),
  enabled: z.boolean().optional(),
});

function atomsToDecimal(atoms: string | bigint, decimals: number): string {
  const value = typeof atoms === "bigint" ? atoms : BigInt(atoms || "0");
  const negative = value < 0n;
  const absolute = negative ? -value : value;
  const raw = absolute.toString().padStart(decimals + 1, "0");
  const integer = raw.slice(0, -decimals) || "0";
  const fraction = raw.slice(-decimals).replace(/0+$/, "");
  return `${negative ? "-" : ""}${integer}${fraction ? `.${fraction}` : ""}`;
}

function decimalToAtoms(value: string, decimals: number): string {
  const normalized = value.trim();
  if (!/^\d+(\.\d+)?$/.test(normalized)) {
    throw new Error("Invalid decimal amount");
  }

  const [whole, fraction = ""] = normalized.split(".");
  if (fraction.length > decimals) {
    throw new Error(`Amount supports up to ${decimals} decimals`);
  }

  return BigInt(`${whole}${fraction.padEnd(decimals, "0")}`).toString();
}

function asAtomBigInt(value: unknown): bigint {
  return BigInt(String(value ?? "0"));
}

async function getAssetDecimals(pgPool: Pool | PoolClient, asset: string): Promise<number> {
  const result = await pgPool.query("SELECT decimals FROM assets WHERE symbol = $1 LIMIT 1", [asset]);
  return Number(result.rows[0]?.decimals ?? DEFAULT_DECIMALS);
}

async function ensureSettings(pgPool: Pool | PoolClient): Promise<AirdropSettings> {
  await pgPool.query(
    `
      INSERT INTO airdrop_settings (
        id, asset, claim_amount, claim_amount_atoms, cooldown_seconds, enabled, pool_account_id
      )
      VALUES ('default', 'ANM', 1, 1000000000, 14400, true, 'system:airdrop')
      ON CONFLICT (id) DO NOTHING
    `
  );

  const result = await pgPool.query(
    `
      SELECT asset, claim_amount, claim_amount_atoms, cooldown_seconds, enabled, pool_account_id
      FROM airdrop_settings
      WHERE id = $1
      LIMIT 1
    `,
    [AIRDROP_SETTINGS_ID]
  );
  const row = result.rows[0];
  return {
    asset: row.asset,
    claimAmount: String(row.claim_amount),
    claimAmountAtoms: String(row.claim_amount_atoms),
    cooldownSeconds: Number(row.cooldown_seconds),
    enabled: Boolean(row.enabled),
    poolAccountId: row.pool_account_id,
  };
}

async function getPoolAtoms(pgPool: Pool | PoolClient, settings: AirdropSettings): Promise<string> {
  const result = await pgPool.query(
    `
      SELECT COALESCE(available_atoms, 0) AS available_atoms
      FROM balances
      WHERE account_id = $1
        AND asset = $2
      LIMIT 1
    `,
    [settings.poolAccountId, settings.asset]
  );
  return String(result.rows[0]?.available_atoms ?? "0");
}

async function buildStatus(pgPool: Pool | PoolClient, userId: string) {
  const settings = await ensureSettings(pgPool);
  const decimals = await getAssetDecimals(pgPool, settings.asset);
  const poolBalanceAtoms = await getPoolAtoms(pgPool, settings);
  const lastClaimResult = await pgPool.query(
    `
      SELECT claimed_at
      FROM airdrop_claims
      WHERE user_id = $1::uuid
        AND asset = $2
      ORDER BY claimed_at DESC
      LIMIT 1
    `,
    [userId, settings.asset]
  );

  const lastClaimAt = lastClaimResult.rows[0]?.claimed_at
    ? new Date(lastClaimResult.rows[0].claimed_at)
    : null;
  const nextClaimAt = lastClaimAt
    ? new Date(lastClaimAt.getTime() + settings.cooldownSeconds * 1000)
    : null;
  const now = Date.now();
  const enoughPool = asAtomBigInt(poolBalanceAtoms) >= asAtomBigInt(settings.claimAmountAtoms);
  const cooldownDone = !nextClaimAt || nextClaimAt.getTime() <= now;

  return {
    settings: {
      asset: settings.asset,
      claimAmount: settings.claimAmount,
      claimAmountAtoms: settings.claimAmountAtoms,
      cooldownSeconds: settings.cooldownSeconds,
      enabled: settings.enabled,
    },
    poolBalance: atomsToDecimal(poolBalanceAtoms, decimals),
    poolBalanceAtoms,
    claimable: settings.enabled && cooldownDone && enoughPool,
    lastClaimAt: lastClaimAt?.toISOString() ?? null,
    nextClaimAt: nextClaimAt?.toISOString() ?? null,
  };
}

function requireAdminKey(options: AirdropRouterOptions) {
  return (req: AuthenticatedRequest, res: any, next: any) => {
    const configured = options.adminApiKey?.trim();
    if (!configured) {
      return res.status(403).json({ error: "Admin key is not configured" });
    }

    const header = req.headers["x-admin-api-key"];
    const provided = typeof header === "string" ? header.trim() : "";
    if (provided !== configured) {
      return res.status(403).json({ error: "Invalid admin key" });
    }

    return next();
  };
}

function requireSession(req: AuthenticatedRequest, res: any, next: any) {
  if (req.authMethod === "apiKey") {
    return res.status(403).json({ error: "Use a logged-in session for airdrop transfers" });
  }
  return next();
}

export function createAirdropRouter(pgPool: Pool, options: AirdropRouterOptions): Router {
  const router = Router();
  const requireAuth = createRequireAuth(options.authServiceUrl, {
    verifyApiKey: createApiKeyVerifier(pgPool),
  });
  const requireReadScope = requireApiKeyScope("read");

  router.get("/airdrop", requireAuth, requireReadScope, async (req: AuthenticatedRequest, res) => {
    try {
      res.json(await buildStatus(pgPool, req.userId!));
    } catch (error) {
      console.error("Error fetching airdrop status:", error);
      res.status(500).json({ error: "Failed to fetch airdrop status" });
    }
  });

  router.post("/airdrop/claim", requireAuth, requireSession, async (req: AuthenticatedRequest, res) => {
    const client = await pgPool.connect();
    try {
      await client.query("BEGIN");
      await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [`airdrop:${req.userId}`]);
      const settings = await ensureSettings(client);
      const decimals = await getAssetDecimals(client, settings.asset);

      const lastClaim = await client.query(
        `
          SELECT claimed_at
          FROM airdrop_claims
          WHERE user_id = $1::uuid
            AND asset = $2
          ORDER BY claimed_at DESC
          LIMIT 1
          FOR UPDATE
        `,
        [req.userId, settings.asset]
      );

      const lastClaimAt = lastClaim.rows[0]?.claimed_at
        ? new Date(lastClaim.rows[0].claimed_at)
        : null;
      if (!settings.enabled) {
        await client.query("ROLLBACK");
        return res.status(400).json({ error: "Airdrop is paused" });
      }

      if (lastClaimAt) {
        const nextClaimAt = new Date(lastClaimAt.getTime() + settings.cooldownSeconds * 1000);
        if (nextClaimAt.getTime() > Date.now()) {
          await client.query("ROLLBACK");
          return res.status(429).json({
            error: "Airdrop cooldown active",
            nextClaimAt: nextClaimAt.toISOString(),
          });
        }
      }

      await client.query(
        `
          INSERT INTO balances (account_id, asset, available, locked, available_atoms, locked_atoms, updated_at)
          VALUES ($1, $2, 0, 0, 0, 0, NOW())
          ON CONFLICT (account_id, asset) DO NOTHING
        `,
        [settings.poolAccountId, settings.asset]
      );

      const poolResult = await client.query(
        `
          SELECT available_atoms
          FROM balances
          WHERE account_id = $1
            AND asset = $2
          FOR UPDATE
        `,
        [settings.poolAccountId, settings.asset]
      );
      const poolAtoms = asAtomBigInt(poolResult.rows[0]?.available_atoms);
      const claimAtoms = asAtomBigInt(settings.claimAmountAtoms);
      if (poolAtoms < claimAtoms) {
        await client.query("ROLLBACK");
        return res.status(400).json({ error: "Airdrop pool is empty" });
      }

      await client.query(
        `
          UPDATE balances
          SET
            available = available - $3::numeric,
            available_atoms = available_atoms - $4::numeric,
            updated_at = NOW()
          WHERE account_id = $1
            AND asset = $2
        `,
        [settings.poolAccountId, settings.asset, settings.claimAmount, settings.claimAmountAtoms]
      );

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
        [`user:${req.userId}`, settings.asset, settings.claimAmount, settings.claimAmountAtoms]
      );

      const claimId = randomUUID();
      await client.query(
        `
          INSERT INTO airdrop_claims (id, user_id, asset, amount, amount_atoms)
          VALUES ($1, $2::uuid, $3, $4::numeric, $5::numeric)
        `,
        [claimId, req.userId, settings.asset, settings.claimAmount, settings.claimAmountAtoms]
      );

      await client.query("COMMIT");
      res.json({
        id: claimId,
        asset: settings.asset,
        amount: settings.claimAmount,
        amountAtoms: settings.claimAmountAtoms,
        amountDisplay: `${atomsToDecimal(settings.claimAmountAtoms, decimals)} ${settings.asset}`,
        claimedAt: new Date().toISOString(),
      });
    } catch (error) {
      await client.query("ROLLBACK").catch(() => undefined);
      console.error("Error claiming airdrop:", error);
      res.status(500).json({ error: "Failed to claim airdrop" });
    } finally {
      client.release();
    }
  });

  router.post("/airdrop/deposit", requireAuth, requireSession, async (req: AuthenticatedRequest, res) => {
    const parsed = depositSchema.safeParse(req.body);
    if (!parsed.success) {
      return res.status(400).json({ error: "Invalid airdrop deposit" });
    }
    const body = parsed.data;
    const client = await pgPool.connect();
    try {
      await client.query("BEGIN");
      const settings = await ensureSettings(client);
      const decimals = await getAssetDecimals(client, settings.asset);
      const amount = atomsToDecimal(body.amountAtoms, decimals);

      const userBalance = await client.query(
        `
          SELECT available_atoms
          FROM balances
          WHERE account_id = $1
            AND asset = $2
          FOR UPDATE
        `,
        [`user:${req.userId}`, settings.asset]
      );
      if (asAtomBigInt(userBalance.rows[0]?.available_atoms) < asAtomBigInt(body.amountAtoms)) {
        await client.query("ROLLBACK");
        return res.status(400).json({ error: "Insufficient ANM balance" });
      }

      await client.query(
        `
          UPDATE balances
          SET
            available = available - $3::numeric,
            available_atoms = available_atoms - $4::numeric,
            updated_at = NOW()
          WHERE account_id = $1
            AND asset = $2
        `,
        [`user:${req.userId}`, settings.asset, amount, body.amountAtoms]
      );

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
        [settings.poolAccountId, settings.asset, amount, body.amountAtoms]
      );

      const depositId = randomUUID();
      await client.query(
        `
          INSERT INTO airdrop_deposits (id, user_id, asset, amount, amount_atoms)
          VALUES ($1, $2::uuid, $3, $4::numeric, $5::numeric)
        `,
        [depositId, req.userId, settings.asset, amount, body.amountAtoms]
      );

      await client.query("COMMIT");
      res.json({
        id: depositId,
        asset: settings.asset,
        amount,
        amountAtoms: body.amountAtoms,
        depositedAt: new Date().toISOString(),
      });
    } catch (error) {
      await client.query("ROLLBACK").catch(() => undefined);
      console.error("Error depositing to airdrop:", error);
      res.status(500).json({ error: "Failed to deposit to airdrop" });
    } finally {
      client.release();
    }
  });

  router.patch(
    "/airdrop/settings",
    requireAuth,
    requireAdminKey(options),
    async (req: AuthenticatedRequest, res) => {
      try {
        const body = settingsSchema.parse(req.body);
        const current = await ensureSettings(pgPool);
        const decimals = await getAssetDecimals(pgPool, current.asset);
        const claimAmount = body.claimAmount ?? current.claimAmount;
        const claimAmountAtoms = body.claimAmount
          ? decimalToAtoms(body.claimAmount, decimals)
          : current.claimAmountAtoms;

        await pgPool.query(
          `
            UPDATE airdrop_settings
            SET
              claim_amount = $2::numeric,
              claim_amount_atoms = $3::numeric,
              cooldown_seconds = $4,
              enabled = $5,
              updated_at = NOW()
            WHERE id = $1
          `,
          [
            AIRDROP_SETTINGS_ID,
            claimAmount,
            claimAmountAtoms,
            body.cooldownSeconds ?? current.cooldownSeconds,
            body.enabled ?? current.enabled,
          ]
        );

        res.json(await buildStatus(pgPool, req.userId!));
      } catch (error) {
        console.error("Error updating airdrop settings:", error);
        res.status(400).json({ error: "Failed to update airdrop settings" });
      }
    }
  );

  return router;
}
