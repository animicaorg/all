import { createHash } from "node:crypto";
import type { NextFunction, Request, Response } from "express";
import type { Pool } from "pg";

export interface AuthenticatedRequest extends Request {
  userId?: string;
  apiKeyId?: string;
  apiKeyScopes?: string[];
  authMethod?: "session" | "apiKey";
}

type AuthUser = {
  id?: string;
  userId?: string;
};

type ApiKeyPrincipal = {
  userId: string;
  keyId: string;
  scopes: string[];
};

type RequireAuthOptions = {
  verifyApiKey?: (apiKey: string) => Promise<ApiKeyPrincipal | null>;
};

function stripTrailingSlash(value: string): string {
  return value.endsWith("/") ? value.slice(0, -1) : value;
}

function normalizeScopes(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String);
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed.map(String) : [];
    } catch {
      return [];
    }
  }
  return [];
}

function getApiKeyFromRequest(req: Request): string | null {
  const authorization = req.headers.authorization;
  if (typeof authorization === "string") {
    const [scheme, token] = authorization.split(/\s+/, 2);
    if (/^bearer$/i.test(scheme) && token) return token.trim();
  }

  const apiKeyHeader = req.headers["x-api-key"];
  if (typeof apiKeyHeader === "string" && apiKeyHeader.trim()) return apiKeyHeader.trim();
  return null;
}

export function hashApiKey(apiKey: string): string {
  return createHash("sha256").update(apiKey, "utf8").digest("hex");
}

export function createApiKeyVerifier(pgPool: Pool) {
  return async (apiKey: string): Promise<ApiKeyPrincipal | null> => {
    const keyHash = hashApiKey(apiKey);
    const result = await pgPool.query(
      `
        UPDATE api_keys
        SET last_used_at = NOW()
        FROM users
        WHERE key_hash = $1
          AND revoked_at IS NULL
          AND users.id = api_keys.user_id
          AND users.active = true
          AND users.email_verified = true
        RETURNING api_keys.id::text, api_keys.user_id::text, api_keys.scopes
      `,
      [keyHash]
    );

    const row = result.rows[0];
    if (!row) return null;

    return {
      userId: row.user_id,
      keyId: row.id,
      scopes: normalizeScopes(row.scopes),
    };
  };
}

export function hasApiKeyScope(req: AuthenticatedRequest, scope: string): boolean {
  if (req.authMethod !== "apiKey") return true;
  const scopes = req.apiKeyScopes ?? [];
  return scopes.includes("*") || scopes.includes(scope);
}

export function requireApiKeyScope(scope: string) {
  return (req: AuthenticatedRequest, res: Response, next: NextFunction) => {
    if (!hasApiKeyScope(req, scope)) {
      return res.status(403).json({ error: "API key scope required", scope });
    }
    return next();
  };
}

export function createRequireAuth(authServiceUrl: string, options: RequireAuthOptions = {}) {
  const authServiceBaseUrl = stripTrailingSlash(authServiceUrl);

  return async (req: AuthenticatedRequest, res: Response, next: NextFunction) => {
    const apiKey = getApiKeyFromRequest(req);
    if (apiKey && options.verifyApiKey) {
      try {
        const principal = await options.verifyApiKey(apiKey);
        if (!principal) {
          return res.status(401).json({ error: "Invalid API key" });
        }
        req.userId = principal.userId;
        req.apiKeyId = principal.keyId;
        req.apiKeyScopes = principal.scopes;
        req.authMethod = "apiKey";
        return next();
      } catch {
        return res.status(500).json({ error: "API key authentication failed" });
      }
    }

    const cookie = req.headers.cookie;
    if (!cookie) {
      return res.status(401).json({ error: "Unauthorized" });
    }

    try {
      const response = await fetch(`${authServiceBaseUrl}/auth/me`, {
        headers: { cookie },
      });

      if (!response.ok) {
        return res.status(401).json({ error: "Unauthorized" });
      }

      const user = (await response.json()) as AuthUser;
      const userId = user.id || user.userId;
      if (!userId) {
        return res.status(401).json({ error: "Unauthorized" });
      }

      req.userId = userId;
      req.authMethod = "session";
      return next();
    } catch {
      return res.status(502).json({ error: "Auth service unavailable" });
    }
  };
}
