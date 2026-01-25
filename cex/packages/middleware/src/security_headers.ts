/**
 * Security Headers Middleware
 * Sets secure HTTP headers to protect against common attacks
 */

import type { Request, Response, NextFunction } from 'express';

export interface SecurityHeadersConfig {
  /**
   * Enable HSTS (HTTP Strict Transport Security)
   */
  hsts?: boolean;

  /**
   * HSTS max age in seconds (default: 1 year)
   */
  hstsMaxAge?: number;

  /**
   * Enable HSTS preload
   */
  hstsPreload?: boolean;

  /**
   * Content Security Policy
   */
  csp?: string;

  /**
   * Frame options (DENY, SAMEORIGIN, or ALLOW-FROM uri)
   */
  frameOptions?: 'DENY' | 'SAMEORIGIN' | string;

  /**
   * Referrer policy
   */
  referrerPolicy?:
    | 'no-referrer'
    | 'no-referrer-when-downgrade'
    | 'origin'
    | 'origin-when-cross-origin'
    | 'same-origin'
    | 'strict-origin'
    | 'strict-origin-when-cross-origin'
    | 'unsafe-url';

  /**
   * Permissions policy
   */
  permissionsPolicy?: string;
}

/**
 * Default security headers configuration
 */
const DEFAULT_CONFIG: Required<SecurityHeadersConfig> = {
  hsts: true,
  hstsMaxAge: 31536000, // 1 year
  hstsPreload: false,
  csp: "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self'; connect-src 'self'; frame-ancestors 'none';",
  frameOptions: 'DENY',
  referrerPolicy: 'strict-origin-when-cross-origin',
  permissionsPolicy: 'geolocation=(), microphone=(), camera=()',
};

/**
 * Security headers middleware
 */
export function createSecurityHeadersMiddleware(config: SecurityHeadersConfig = {}) {
  const finalConfig = { ...DEFAULT_CONFIG, ...config };

  return (req: Request, res: Response, next: NextFunction) => {
    // HSTS
    if (finalConfig.hsts) {
      const hstsValue = `max-age=${finalConfig.hstsMaxAge}; includeSubDomains${
        finalConfig.hstsPreload ? '; preload' : ''
      }`;
      res.setHeader('Strict-Transport-Security', hstsValue);
    }

    // Content Security Policy
    if (finalConfig.csp) {
      res.setHeader('Content-Security-Policy', finalConfig.csp);
    }

    // X-Frame-Options
    res.setHeader('X-Frame-Options', finalConfig.frameOptions);

    // X-Content-Type-Options
    res.setHeader('X-Content-Type-Options', 'nosniff');

    // X-XSS-Protection (legacy, but still useful)
    res.setHeader('X-XSS-Protection', '1; mode=block');

    // Referrer-Policy
    res.setHeader('Referrer-Policy', finalConfig.referrerPolicy);

    // Permissions-Policy
    if (finalConfig.permissionsPolicy) {
      res.setHeader('Permissions-Policy', finalConfig.permissionsPolicy);
    }

    // X-DNS-Prefetch-Control
    res.setHeader('X-DNS-Prefetch-Control', 'off');

    // Remove powered-by header
    res.removeHeader('X-Powered-By');

    next();
  };
}

/**
 * CORS configuration for API endpoints
 */
export interface CorsConfig {
  /**
   * Allowed origins (array of URLs or '*')
   */
  origins: string[] | '*';

  /**
   * Allowed methods
   */
  methods?: string[];

  /**
   * Allowed headers
   */
  allowedHeaders?: string[];

  /**
   * Exposed headers
   */
  exposedHeaders?: string[];

  /**
   * Allow credentials
   */
  credentials?: boolean;

  /**
   * Max age for preflight cache
   */
  maxAge?: number;
}

/**
 * CORS middleware
 */
export function createCorsMiddleware(config: CorsConfig) {
  const methods = config.methods || ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'];
  const allowedHeaders = config.allowedHeaders || [
    'Content-Type',
    'Authorization',
    'X-Request-ID',
    'X-API-Key',
  ];
  const exposedHeaders = config.exposedHeaders || ['X-Request-ID', 'X-RateLimit-Remaining'];
  const maxAge = config.maxAge || 86400; // 24 hours

  return (req: Request, res: Response, next: NextFunction) => {
    const origin = req.headers.origin;

    // Check if origin is allowed
    if (config.origins === '*') {
      res.setHeader('Access-Control-Allow-Origin', '*');
    } else if (origin && config.origins.includes(origin)) {
      res.setHeader('Access-Control-Allow-Origin', origin);
      if (config.credentials) {
        res.setHeader('Access-Control-Allow-Credentials', 'true');
      }
    } else if (origin) {
      // Origin not allowed - don't set CORS headers
      // Browser will block the request
      next();
      return;
    }

    res.setHeader('Access-Control-Allow-Methods', methods.join(', '));
    res.setHeader('Access-Control-Allow-Headers', allowedHeaders.join(', '));
    res.setHeader('Access-Control-Expose-Headers', exposedHeaders.join(', '));
    res.setHeader('Access-Control-Max-Age', maxAge.toString());

    // Handle preflight
    if (req.method === 'OPTIONS') {
      res.status(204).send();
      return;
    }

    next();
  };
}
