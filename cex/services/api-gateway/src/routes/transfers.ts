import { createDecipheriv, randomUUID } from "node:crypto";
import { Router } from "express";
import type { Pool } from "pg";
import { z } from "zod";
import { createRequireAuth, type AuthenticatedRequest } from "./authenticated.js";

const depositAddressSchema = z.object({
  assetNetworkId: z.string().uuid(),
});

const createWithdrawalSchema = z.object({
  assetNetworkId: z.string().uuid(),
  destinationAddress: z.string().min(1).max(200),
  destinationTag: z.string().max(120).optional(),
  amountAtoms: z.string().regex(/^\d+$/),
  clientWithdrawalId: z.string().max(120).optional(),
});

type TransferRouterOptions = {
  authServiceUrl: string;
  withdrawalsServiceUrl: string;
  bitgoBaseUrl?: string;
  bitgoAccessToken?: string;
  configEncryptionKey?: string;
  adminApiKey?: string;
  animicaRpcUrl?: string;
  animicaRpcAdminToken?: string;
};

type AssetNetworkRow = {
  asset_network_id: string;
  asset_symbol: string;
  asset_decimals: number;
  network_code: string;
  network_type: string;
  bitgo_coin: string | null;
  rpc_url: string | null;
  deposits_enabled: boolean;
  withdrawals_enabled: boolean;
  provider: string;
};

function stripTrailingSlash(value: string): string {
  return value.endsWith("/") ? value.slice(0, -1) : value;
}

function getString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

const IV_LENGTH = 12;
const TAG_LENGTH = 16;

function decryptSecret(payload: string, key: Buffer): string {
  const raw = Buffer.from(payload, "base64");
  const iv = raw.subarray(0, IV_LENGTH);
  const tag = raw.subarray(IV_LENGTH, IV_LENGTH + TAG_LENGTH);
  const encrypted = raw.subarray(IV_LENGTH + TAG_LENGTH);
  const decipher = createDecipheriv("aes-256-gcm", key, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(encrypted), decipher.final()]).toString("utf8");
}

function normalizeEncryptionKey(key: string): Buffer {
  const normalized = key.trim();
  const buffer =
    normalized.length === 64 ? Buffer.from(normalized, "hex") : Buffer.from(normalized, "base64");
  if (buffer.length !== 32) {
    throw new Error("CONFIG_ENCRYPTION_KEY must be 32 bytes (base64 or hex)");
  }
  return buffer;
}

async function getBitgoRuntimeConfig(pgPool: Pool, options: TransferRouterOptions) {
  const fallbackBaseUrl =
    options.bitgoBaseUrl ||
    (process.env.BITGO_ENV === "prod" ? "https://app.bitgo.com" : "https://app.bitgo-test.com");

  const result = await pgPool.query("SELECT * FROM bitgo_configs WHERE id = 'default' LIMIT 1").catch(() => null);
  const row = result?.rows?.[0];
  if (row?.enabled && row.access_token_encrypted) {
    if (!options.configEncryptionKey) {
      throw new Error("CONFIG_ENCRYPTION_KEY is required to decrypt admin BitGo settings");
    }
    const key = normalizeEncryptionKey(options.configEncryptionKey);
    const environment = String(row.environment || "test").toLowerCase();
    return {
      baseUrl:
        row.base_url ||
        (environment === "prod" ? "https://app.bitgo.com" : "https://app.bitgo-test.com"),
      accessToken: decryptSecret(row.access_token_encrypted, key),
    };
  }

  return {
    baseUrl: fallbackBaseUrl,
    accessToken: options.bitgoAccessToken,
  };
}

async function getAssetNetwork(pgPool: Pool, assetNetworkId: string): Promise<AssetNetworkRow | null> {
  const result = await pgPool.query(
    `
      SELECT
        asset_networks.id::text AS asset_network_id,
        assets.symbol AS asset_symbol,
        assets.decimals AS asset_decimals,
        networks.code AS network_code,
        networks.type AS network_type,
        asset_networks.bitgo_coin,
        COALESCE(asset_networks.metadata->>'rpc_url', networks.metadata->>'rpc_url') AS rpc_url,
        asset_networks.deposits_enabled,
        asset_networks.withdrawals_enabled,
        COALESCE(
          asset_networks.metadata->>'provider',
          CASE
            WHEN asset_networks.bitgo_coin IS NOT NULL THEN 'BITGO'
            WHEN networks.code = 'ANIMICA' OR networks.type IN ('ANIMICA', 'ACCOUNT') THEN 'ANIMICA_NODE'
            WHEN networks.type = 'UTXO' THEN 'BITCOIN_NODE'
            ELSE 'OTHER'
          END
        ) AS provider
      FROM asset_networks
      JOIN assets ON assets.id = asset_networks.asset_id
      JOIN networks ON networks.id = asset_networks.network_id
      WHERE asset_networks.id = $1::uuid
        AND assets.active = true
        AND networks.active = true
      LIMIT 1
    `,
    [assetNetworkId]
  );

  return result.rows[0] ?? null;
}

async function getExistingDepositAddress(pgPool: Pool, userId: string, assetNetworkId: string) {
  const result = await pgPool.query(
    `
      SELECT
        user_deposit_addresses.id::text,
        user_deposit_addresses.address,
        user_deposit_addresses.tag,
        user_deposit_addresses.label,
        user_deposit_addresses.assigned_at,
        wallets.provider,
        wallets.wallet_id AS provider_wallet_id
      FROM user_deposit_addresses
      JOIN wallets ON wallets.id = user_deposit_addresses.wallet_id
      WHERE user_deposit_addresses.user_id = $1::uuid
        AND user_deposit_addresses.asset_network_id = $2::uuid
        AND user_deposit_addresses.status = 'ACTIVE'
      ORDER BY user_deposit_addresses.assigned_at DESC
      LIMIT 1
    `,
    [userId, assetNetworkId]
  );

  return result.rows[0] ?? null;
}

async function getActiveWallet(pgPool: Pool, assetNetworkId: string, provider?: string) {
  const values = [assetNetworkId];
  const providerSql = provider ? "AND provider = $2" : "";
  if (provider) values.push(provider);

  const result = await pgPool.query(
    `
      SELECT id::text, provider, wallet_id, metadata
      FROM wallets
      WHERE asset_network_id = $1::uuid
        AND status = 'ACTIVE'
        ${providerSql}
      ORDER BY created_at DESC
      LIMIT 1
    `,
    values
  );

  return result.rows[0] ?? null;
}

async function getOrCreateAnimicaWallet(pgPool: Pool, assetNetworkId: string) {
  const existing = await getActiveWallet(pgPool, assetNetworkId, "ANIMICA_NODE");
  if (existing) return existing;

  const result = await pgPool.query(
    `
      INSERT INTO wallets (provider, wallet_id, asset_network_id, status, metadata)
      VALUES ('ANIMICA_NODE', $1, $2::uuid, 'ACTIVE', '{"purpose":"DEPOSIT"}'::jsonb)
      ON CONFLICT (provider, wallet_id) DO UPDATE SET
        status = 'ACTIVE',
        updated_at = NOW()
      RETURNING id::text, provider, wallet_id, metadata
    `,
    [`animica-node:${assetNetworkId}`, assetNetworkId]
  );

  return result.rows[0];
}

async function createAnimicaAddress(
  options: TransferRouterOptions,
  rpcUrl: string | undefined,
  label: string
): Promise<{ address: string; tag: string | null; raw: unknown }> {
  if (!rpcUrl) {
    throw new Error("Animica RPC URL is not configured");
  }
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (options.animicaRpcAdminToken) headers["x-animica-admin-token"] = options.animicaRpcAdminToken;

  const response = await fetch(rpcUrl, {
    method: "POST",
    headers,
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: randomUUID(),
      method: "wallet.createAddress",
      params: [label],
    }),
  });

  const payload = await response.json().catch(() => null);
  if (!response.ok || payload?.error) {
    throw new Error(payload?.error?.message || "Animica node failed to create a deposit address");
  }

  const result = payload?.result;
  const address = getString(result?.address) || getString(result);
  if (!address) {
    throw new Error("Animica node did not return a deposit address");
  }

  return { address, tag: null, raw: payload };
}

async function createBitgoAddress(
  pgPool: Pool,
  options: TransferRouterOptions,
  coin: string,
  walletId: string,
  label: string
): Promise<{ address: string; tag: string | null; raw: unknown }> {
  const config = await getBitgoRuntimeConfig(pgPool, options);
  if (!config.accessToken || !config.baseUrl) {
    throw new Error("BitGo access token or API URL is not configured");
  }

  const baseUrl = stripTrailingSlash(config.baseUrl);
  const response = await fetch(`${baseUrl}/api/v2/${encodeURIComponent(coin)}/wallet/${encodeURIComponent(walletId)}/address`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${config.accessToken}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({ label }),
  });

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(payload?.error || payload?.message || "BitGo failed to create a deposit address");
  }

  const address = getString(payload?.address) || getString(payload?.coinSpecific?.address);
  const tag = getString(payload?.destinationTag) || getString(payload?.memo) || getString(payload?.tag);
  if (!address) {
    throw new Error("BitGo did not return a deposit address");
  }

  return { address, tag, raw: payload };
}

async function callJsonRpc(rpcUrl: string, method: string, params: unknown[]) {
  const url = new URL(rpcUrl);
  const headers: Record<string, string> = { "content-type": "application/json" };

  if (url.username || url.password) {
    const username = decodeURIComponent(url.username);
    const password = decodeURIComponent(url.password);
    headers.authorization = `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}`;
    url.username = "";
    url.password = "";
  }

  const response = await fetch(url.toString(), {
    method: "POST",
    headers,
    body: JSON.stringify({
      jsonrpc: "1.0",
      id: randomUUID(),
      method,
      params,
    }),
  });

  const payload = await response.json().catch(() => null);
  if (!response.ok || payload?.error) {
    throw new Error(payload?.error?.message || `${method} RPC call failed`);
  }

  return payload;
}

async function createBitcoinStyleAddress(
  rpcUrl: string,
  label: string
): Promise<{ address: string; tag: string | null; raw: unknown }> {
  const payload = await callJsonRpc(rpcUrl, "getnewaddress", [label]);
  const address = getString(payload?.result);
  if (!address) {
    throw new Error("Bitcoin-style node did not return a deposit address");
  }
  return { address, tag: null, raw: payload };
}

async function storeDepositAddress(
  pgPool: Pool,
  userId: string,
  assetNetworkId: string,
  walletUuid: string,
  address: string,
  tag: string | null,
  label: string
) {
  const result = await pgPool.query(
    `
      INSERT INTO user_deposit_addresses (
        user_id,
        asset_network_id,
        wallet_id,
        address,
        tag,
        label,
        status
      )
      VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, 'ACTIVE')
      ON CONFLICT (asset_network_id, address, tag) DO UPDATE SET
        user_id = EXCLUDED.user_id,
        wallet_id = EXCLUDED.wallet_id,
        label = EXCLUDED.label,
        status = 'ACTIVE'
      RETURNING id::text, address, tag, label, assigned_at
    `,
    [userId, assetNetworkId, walletUuid, address, tag, label]
  );

  return result.rows[0];
}

async function proxyWithdrawalRequest(
  options: TransferRouterOptions,
  path: string,
  init: RequestInit
): Promise<Response> {
  const url = `${stripTrailingSlash(options.withdrawalsServiceUrl)}${path}`;
  return fetch(url, init);
}

export function createTransfersRouter(pgPool: Pool, options: TransferRouterOptions): Router {
  const router = Router();
  const requireAuth = createRequireAuth(options.authServiceUrl);

  router.get("/me/deposit-addresses", requireAuth, async (req: AuthenticatedRequest, res) => {
    try {
      const userId = req.userId!;
      const assetNetworkId = req.query.assetNetworkId as string | undefined;

      const params: unknown[] = [userId];
      let filterSql = "";
      if (assetNetworkId) {
        filterSql = "AND user_deposit_addresses.asset_network_id = $2::uuid";
        params.push(assetNetworkId);
      }

      const result = await pgPool.query(
        `
          SELECT
            user_deposit_addresses.id::text,
            user_deposit_addresses.asset_network_id::text AS asset_network_id,
            user_deposit_addresses.address,
            user_deposit_addresses.tag,
            user_deposit_addresses.label,
            user_deposit_addresses.assigned_at,
            assets.symbol,
            networks.code AS network_code
          FROM user_deposit_addresses
          JOIN asset_networks ON asset_networks.id = user_deposit_addresses.asset_network_id
          JOIN assets ON assets.id = asset_networks.asset_id
          JOIN networks ON networks.id = asset_networks.network_id
          WHERE user_deposit_addresses.user_id = $1::uuid
            AND user_deposit_addresses.status = 'ACTIVE'
            ${filterSql}
          ORDER BY user_deposit_addresses.assigned_at DESC
        `,
        params
      );

      res.json({
        depositAddresses: result.rows.map((row) => ({
          id: row.id,
          assetNetworkId: row.asset_network_id,
          symbol: row.symbol,
          networkCode: row.network_code,
          address: row.address,
          tag: row.tag,
          label: row.label,
          assignedAt: new Date(row.assigned_at).getTime(),
        })),
      });
    } catch (error) {
      console.error("Error fetching deposit addresses:", error);
      res.status(500).json({ error: "Failed to fetch deposit addresses" });
    }
  });

  router.post("/me/deposit-addresses", requireAuth, async (req: AuthenticatedRequest, res) => {
    try {
      const userId = req.userId!;
      const body = depositAddressSchema.parse(req.body);
      const assetNetwork = await getAssetNetwork(pgPool, body.assetNetworkId);

      if (!assetNetwork) {
        return res.status(404).json({ error: "Asset network not found" });
      }
      if (!assetNetwork.deposits_enabled) {
        return res.status(400).json({ error: "Deposits are paused for this asset network" });
      }

      const existing = await getExistingDepositAddress(pgPool, userId, body.assetNetworkId);
      if (existing) {
        return res.json({
          depositAddress: {
            id: existing.id,
            assetNetworkId: body.assetNetworkId,
            symbol: assetNetwork.asset_symbol,
            networkCode: assetNetwork.network_code,
            address: existing.address,
            tag: existing.tag,
            label: existing.label,
            assignedAt: new Date(existing.assigned_at).getTime(),
            created: false,
          },
        });
      }

      const label = `${assetNetwork.asset_symbol}-${userId}-${Date.now()}`;
      const provider = assetNetwork.provider;
      let wallet;
      let createdAddress: { address: string; tag: string | null; raw: unknown };

      if (provider === "ANIMICA_NODE") {
        wallet = await getOrCreateAnimicaWallet(pgPool, body.assetNetworkId);
        createdAddress = await createAnimicaAddress(
          options,
          getString(wallet.metadata?.rpc_url) || assetNetwork.rpc_url || options.animicaRpcUrl,
          label
        );
      } else if (provider === "BITGO" && assetNetwork.bitgo_coin) {
        wallet = await getActiveWallet(pgPool, body.assetNetworkId, "BITGO");
        if (!wallet) {
          return res.status(409).json({
            error: "Deposit wallet not configured",
            message: `Configure an active BitGo wallet for ${assetNetwork.asset_symbol} before creating deposit addresses.`,
          });
        }
        createdAddress = await createBitgoAddress(pgPool, options, assetNetwork.bitgo_coin, wallet.wallet_id, label);
      } else if (provider === "BITCOIN_NODE") {
        wallet = await getActiveWallet(pgPool, body.assetNetworkId, "BITCOIN_NODE");
        if (!wallet) {
          return res.status(409).json({
            error: "Deposit wallet not configured",
            message: `Configure an active Bitcoin-style node wallet for ${assetNetwork.asset_symbol} before creating deposit addresses.`,
          });
        }
        const rpcUrl = getString(wallet.metadata?.rpc_url) || assetNetwork.rpc_url;
        if (!rpcUrl) {
          return res.status(409).json({
            error: "Deposit RPC not configured",
            message: `Configure an RPC URL for ${assetNetwork.asset_symbol} before creating deposit addresses.`,
          });
        }
        createdAddress = await createBitcoinStyleAddress(rpcUrl, label);
      } else {
        return res.status(409).json({
          error: "Deposit provider not configured",
          message: `No deposit address provider is configured for ${assetNetwork.asset_symbol}.`,
        });
      }

      const stored = await storeDepositAddress(
        pgPool,
        userId,
        body.assetNetworkId,
        wallet.id,
        createdAddress.address,
        createdAddress.tag,
        label
      );

      res.status(201).json({
        depositAddress: {
          id: stored.id,
          assetNetworkId: body.assetNetworkId,
          symbol: assetNetwork.asset_symbol,
          networkCode: assetNetwork.network_code,
          address: stored.address,
          tag: stored.tag,
          label: stored.label,
          assignedAt: new Date(stored.assigned_at).getTime(),
          created: true,
        },
      });
    } catch (error: any) {
      if (error instanceof z.ZodError) {
        return res.status(400).json({ error: "Invalid request", details: error.errors });
      }
      console.error("Error creating deposit address:", error);
      res.status(500).json({ error: "Failed to create deposit address", message: error.message });
    }
  });

  router.get("/withdrawals", requireAuth, async (req: AuthenticatedRequest, res) => {
    try {
      const search = new URLSearchParams();
      for (const [key, value] of Object.entries(req.query)) {
        if (typeof value === "string") search.set(key, value);
      }

      const queryString = search.toString();
      const upstream = await proxyWithdrawalRequest(options, `/withdrawals${queryString ? `?${queryString}` : ""}`, {
        method: "GET",
        headers: { authorization: `Bearer user-${req.userId}` },
      });
      const body = await upstream.text();
      res.status(upstream.status).type(upstream.headers.get("content-type") || "application/json").send(body);
    } catch (error) {
      console.error("Error fetching withdrawals:", error);
      res.status(502).json({ error: "Withdrawals service unavailable" });
    }
  });

  router.post("/withdrawals", requireAuth, async (req: AuthenticatedRequest, res) => {
    try {
      const body = createWithdrawalSchema.parse(req.body);
      const assetNetwork = await getAssetNetwork(pgPool, body.assetNetworkId);

      if (!assetNetwork) {
        return res.status(404).json({ error: "Asset network not found" });
      }
      if (!assetNetwork.withdrawals_enabled) {
        return res.status(400).json({ error: "Withdrawals are paused for this asset network" });
      }

      const idempotencyKey = req.headers["idempotency-key"]?.toString() || `withdrawal-${req.userId}-${randomUUID()}`;
      const upstream = await proxyWithdrawalRequest(options, "/withdrawals", {
        method: "POST",
        headers: {
          authorization: `Bearer user-${req.userId}`,
          "content-type": "application/json",
          "idempotency-key": idempotencyKey,
        },
        body: JSON.stringify({
          assetNetworkId: body.assetNetworkId,
          destinationAddress: body.destinationAddress,
          destinationTag: body.destinationTag,
          amount: body.amountAtoms,
          clientWithdrawalId: body.clientWithdrawalId,
        }),
      });

      const responseBody = await upstream.text();
      res.status(upstream.status).type(upstream.headers.get("content-type") || "application/json").send(responseBody);
    } catch (error: any) {
      if (error instanceof z.ZodError) {
        return res.status(400).json({ error: "Invalid request", details: error.errors });
      }
      console.error("Error creating withdrawal:", error);
      res.status(502).json({ error: "Failed to create withdrawal", message: error.message });
    }
  });

  return router;
}
