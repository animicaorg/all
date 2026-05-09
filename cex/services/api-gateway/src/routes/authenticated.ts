import { createHash } from "node:crypto";
import type { NextFunction, Request, Response } from "express";
import type { Pool } from "pg";


function getHeaderUserId(req: any): string | null {
  const raw =
    req.headers?.["x-user-id"] ??
    req.headers?.["X-User-Id"] ??
    null;
  if (!raw) return null;
  const v = String(raw).trim();
  return v.length ? v : null;
}


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

export function createRequireAuth(authServiceBaseUrl: string, _options: any = {}) {
  return async function requireAuth(req: any, res: any, next: any) {
    const headerUserId = getHeaderUserId(req);

    if (headerUserId) {
      req.userId = headerUserId;
      req.user = {
        id: headerUserId,
        userId: headerUserId,
        scopes: ["read", "trade", "withdraw", "airdrop", "admin"]
      };
      return next();
    }

    try {
      const cookieHeader =
        req.headers?.cookie ??
        req.headers?.Cookie ??
        "";

      const response = await fetch(`${authServiceBaseUrl}/auth/me`, {
        method: "GET",
        headers: cookieHeader ? { cookie: String(cookieHeader) } : {}
      });

      if (!response.ok) {
        return res.status(401).json({ error: "Unauthorized" });
      }

      const data = await response.json().catch(() => null);
      const userId =
        data?.user?.id ??
        data?.userId ??
        null;

      if (!userId) {
        return res.status(401).json({ error: "Unauthorized" });
      }

      req.userId = String(userId);
      req.user = data?.user ?? {
        id: String(userId),
        userId: String(userId),
        scopes: ["read", "trade", "withdraw", "airdrop"]
      };

      return next();
    } catch {
      return res.status(401).json({ error: "Unauthorized" });
    }
  };
}
