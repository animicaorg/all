/**
 * CSRF Protection Middleware
 * Protects against Cross-Site Request Forgery attacks
 */

import type { Request, Response, NextFunction } from 'express';
import { generateCsrfToken, verifyCsrfToken } from '@cex/security/auth';

export interface CsrfRequest extends Request {
  csrfToken?: string;
}

export interface CsrfConfig {
  /**
   * Cookie name for CSRF token
   */
  cookieName?: string;

  /**
   * Header name for CSRF token
   */
  headerName?: string;

  /**
   * Cookie options
   */
  cookieOptions?: {
    httpOnly?: boolean;
    secure?: boolean;
    sameSite?: 'strict' | 'lax' | 'none';
    maxAge?: number;
  };

  /**
   * Methods to protect (default: POST, PUT, DELETE, PATCH)
   */
  protectedMethods?: string[];

  /**
   * Skip CSRF check for certain paths (regex)
   */
  skip?: RegExp;
}

/**
 * CSRF protection middleware
 */
export function createCsrfMiddleware(config: CsrfConfig = {}) {
  const cookieName = config.cookieName || 'csrf_token';
  const headerName = config.headerName || 'x-csrf-token';
  const protectedMethods = config.protectedMethods || ['POST', 'PUT', 'DELETE', 'PATCH'];

  const cookieOptions = {
    httpOnly: true,
    secure: config.cookieOptions?.secure ?? true,
    sameSite: (config.cookieOptions?.sameSite as 'strict' | 'lax' | 'none') || 'strict',
    maxAge: config.cookieOptions?.maxAge || 3600000, // 1 hour
  };

  return (req: CsrfRequest, res: Response, next: NextFunction) => {
    // Skip if path matches skip pattern
    if (config.skip && config.skip.test(req.path)) {
      next();
      return;
    }

    // Skip if not a protected method
    if (!protectedMethods.includes(req.method)) {
      // For GET requests, generate/refresh token
      if (req.method === 'GET') {
        let token = req.cookies?.[cookieName];

        if (!token) {
          token = generateCsrfToken();
          res.cookie(cookieName, token, cookieOptions);
        }

        req.csrfToken = token;
      }

      next();
      return;
    }

    // For protected methods, verify token
    const cookieToken = req.cookies?.[cookieName];
    const headerToken =
      req.headers[headerName] || req.headers[headerName.toLowerCase()] || req.body?._csrf;

    if (!cookieToken || !headerToken) {
      res.status(403).json({
        error: 'CsrfTokenMissing',
        message: 'CSRF token is required',
      });
      return;
    }

    if (!verifyCsrfToken(headerToken as string, cookieToken)) {
      res.status(403).json({
        error: 'CsrfTokenInvalid',
        message: 'Invalid CSRF token',
      });
      return;
    }

    req.csrfToken = cookieToken;
    next();
  };
}

/**
 * Helper to get CSRF token for forms/AJAX
 */
export function getCsrfToken(req: CsrfRequest): string | undefined {
  return req.csrfToken;
}
