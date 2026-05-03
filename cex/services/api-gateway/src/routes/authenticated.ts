import type { NextFunction, Request, Response } from "express";

export interface AuthenticatedRequest extends Request {
  userId?: string;
}

type AuthUser = {
  id?: string;
  userId?: string;
};

function stripTrailingSlash(value: string): string {
  return value.endsWith("/") ? value.slice(0, -1) : value;
}

export function createRequireAuth(authServiceUrl: string) {
  const authServiceBaseUrl = stripTrailingSlash(authServiceUrl);

  return async (req: AuthenticatedRequest, res: Response, next: NextFunction) => {
    const headerUserId = req.headers["x-user-id"];
    if (typeof headerUserId === "string" && headerUserId.trim()) {
      req.userId = headerUserId.trim();
      return next();
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
      return next();
    } catch {
      return res.status(502).json({ error: "Auth service unavailable" });
    }
  };
}
