import { randomBytes } from "node:crypto";
import { Router } from "express";
import type { Pool } from "pg";
import { z } from "zod";
import {
  createApiKeyVerifier,
  createRequireAuth,
  hashApiKey,
  type AuthenticatedRequest,
} from "./authenticated.js";

const allowedScopes = new Set(["read", "trade"]);

const createApiKeySchema = z.object({
  name: z.string().trim().min(1).max(120),
  scopes: z.array(z.string()).optional(),
});

function normalizeScopes(scopes?: string[]): string[] {
  const normalized = (scopes ?? ["read"])
    .map((scope) => scope.trim().toLowerCase())
    .filter((scope) => allowedScopes.has(scope));
  return Array.from(new Set(normalized.length > 0 ? normalized : ["read"]));
}

function createSecret(): string {
  return `anm_live_${randomBytes(32).toString("base64url")}`;
}

function requireSession(req: AuthenticatedRequest, res: any, next: any) {
  if (req.authMethod === "apiKey") {
    return res.status(403).json({ error: "Use a logged-in session to manage API keys" });
  }
  return next();
}

function mapApiKey(row: any) {
  return {
    id: row.id,
    name: row.name,
    keyPrefix: row.key_prefix,
    scopes: Array.isArray(row.scopes) ? row.scopes : [],
    createdAt: row.created_at ? new Date(row.created_at).toISOString() : null,
    lastUsedAt: row.last_used_at ? new Date(row.last_used_at).toISOString() : null,
    revokedAt: row.revoked_at ? new Date(row.revoked_at).toISOString() : null,
  };
}

export function createApiKeysRouter(pgPool: Pool, authServiceUrl: string): Router {
  const router = Router();
  const requireAuth = createRequireAuth(authServiceUrl, {
    verifyApiKey: createApiKeyVerifier(pgPool),
  });

  router.get("/me/api-keys", requireAuth, requireSession, async (req: AuthenticatedRequest, res) => {
    try {
      const result = await pgPool.query(
        `
          SELECT id::text, name, key_prefix, scopes, created_at, last_used_at, revoked_at
          FROM api_keys
          WHERE user_id = $1::uuid
          ORDER BY created_at DESC
        `,
        [req.userId]
      );
      res.json({ apiKeys: result.rows.map(mapApiKey) });
    } catch (error) {
      console.error("Error listing API keys:", error);
      res.status(500).json({ error: "Failed to list API keys" });
    }
  });

  router.post("/me/api-keys", requireAuth, requireSession, async (req: AuthenticatedRequest, res) => {
    try {
      const body = createApiKeySchema.parse(req.body);
      const secret = createSecret();
      const scopes = normalizeScopes(body.scopes);
      const result = await pgPool.query(
        `
          INSERT INTO api_keys (user_id, name, key_prefix, key_hash, scopes)
          VALUES ($1::uuid, $2, $3, $4, $5::jsonb)
          RETURNING id::text, name, key_prefix, scopes, created_at, last_used_at, revoked_at
        `,
        [
          req.userId,
          body.name,
          secret.slice(0, 18),
          hashApiKey(secret),
          JSON.stringify(scopes),
        ]
      );

      res.status(201).json({
        apiKey: mapApiKey(result.rows[0]),
        secret,
      });
    } catch (error) {
      console.error("Error creating API key:", error);
      res.status(400).json({ error: "Failed to create API key" });
    }
  });

  router.delete("/me/api-keys/:id", requireAuth, requireSession, async (req: AuthenticatedRequest, res) => {
    try {
      const result = await pgPool.query(
        `
          UPDATE api_keys
          SET revoked_at = NOW()
          WHERE id = $1::uuid
            AND user_id = $2::uuid
            AND revoked_at IS NULL
          RETURNING id::text
        `,
        [req.params.id, req.userId]
      );

      if (result.rows.length === 0) {
        return res.status(404).json({ error: "API key not found" });
      }

      res.json({ revoked: true });
    } catch (error) {
      console.error("Error revoking API key:", error);
      res.status(500).json({ error: "Failed to revoke API key" });
    }
  });

  return router;
}
