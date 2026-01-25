/**
 * Service-to-Service Authentication
 * Provides JWT-based authentication for internal service calls
 */

import jwt from 'jsonwebtoken';
import type { Request, Response, NextFunction } from 'express';
import { LocalSigner } from '@cex/security/signing';
import type { Logger } from '@cex/observability';

/**
 * Service token payload
 */
export interface ServiceTokenPayload {
  /**
   * Issuer service (who created the token)
   */
  iss: string;

  /**
   * Subject (service ID)
   */
  sub: string;

  /**
   * Audience (target service)
   */
  aud: string;

  /**
   * Issued at (Unix timestamp)
   */
  iat: number;

  /**
   * Expiration (Unix timestamp)
   */
  exp: number;

  /**
   * Optional scopes/permissions
   */
  scopes?: string[];
}

/**
 * Extended request with service auth
 */
export interface ServiceAuthRequest extends Request {
  serviceAuth?: {
    serviceId: string;
    issuer: string;
    scopes: string[];
  };
}

/**
 * Service auth configuration
 */
export interface ServiceAuthConfig {
  /**
   * This service's ID
   */
  serviceId: string;

  /**
   * Signing key(s) for verification
   * If using keyring, provide comma-separated: "kid1:base64key1,kid2:base64key2"
   */
  signingKey: string;

  /**
   * Token expiration in seconds (default: 300 = 5 minutes)
   */
  tokenExpiry?: number;

  /**
   * Logger instance
   */
  logger?: Logger;
}

/**
 * Generate a service token for calling another service
 */
export function generateServiceToken(
  config: ServiceAuthConfig,
  targetService: string,
  scopes?: string[]
): string {
  const now = Math.floor(Date.now() / 1000);
  const expiry = config.tokenExpiry || 300;

  const payload: ServiceTokenPayload = {
    iss: config.serviceId,
    sub: config.serviceId,
    aud: targetService,
    iat: now,
    exp: now + expiry,
    scopes: scopes || [],
  };

  // Sign with the first (or only) key
  const firstKey = config.signingKey.split(',')[0].split(':')[1] || config.signingKey;
  return jwt.sign(payload, Buffer.from(firstKey, 'base64'), {
    algorithm: 'HS256',
  });
}

/**
 * Verify a service token
 */
export function verifyServiceToken(
  token: string,
  config: ServiceAuthConfig
): ServiceTokenPayload | null {
  try {
    // Parse keyring - try all keys for verification (rotation support)
    const keys = config.signingKey.includes(':')
      ? config.signingKey.split(',').map((entry) => entry.split(':')[1])
      : [config.signingKey];

    let lastError: Error | null = null;

    for (const key of keys) {
      try {
        const decoded = jwt.verify(token, Buffer.from(key, 'base64'), {
          algorithms: ['HS256'],
          audience: config.serviceId, // This service must be the audience
        }) as ServiceTokenPayload;

        return decoded;
      } catch (error) {
        lastError = error as Error;
        continue;
      }
    }

    // If we get here, none of the keys worked
    config.logger?.warn(
      { error: lastError?.message },
      'Service token verification failed with all keys'
    );
    return null;
  } catch (error) {
    config.logger?.warn({ error }, 'Service token verification error');
    return null;
  }
}

/**
 * Express middleware for service authentication
 */
export function createServiceAuthMiddleware(config: ServiceAuthConfig) {
  return (req: ServiceAuthRequest, res: Response, next: NextFunction) => {
    const authHeader = req.headers.authorization;

    if (!authHeader) {
      config.logger?.warn(
        { path: req.path, method: req.method },
        'Missing Authorization header on internal endpoint'
      );
      res.status(401).json({
        error: 'Unauthorized',
        message: 'Service authentication required',
      });
      return;
    }

    // Extract token
    const match = authHeader.match(/^Bearer\s+(.+)$/i);
    if (!match) {
      config.logger?.warn(
        { path: req.path, method: req.method },
        'Invalid Authorization header format'
      );
      res.status(401).json({
        error: 'Unauthorized',
        message: 'Invalid authorization format',
      });
      return;
    }

    const token = match[1];

    // Verify token
    const payload = verifyServiceToken(token, config);
    if (!payload) {
      res.status(401).json({
        error: 'Unauthorized',
        message: 'Invalid or expired service token',
      });
      return;
    }

    // Attach service auth to request
    req.serviceAuth = {
      serviceId: payload.sub,
      issuer: payload.iss,
      scopes: payload.scopes || [],
    };

    config.logger?.debug(
      {
        caller: payload.sub,
        path: req.path,
        method: req.method,
      },
      'Service authenticated'
    );

    next();
  };
}

/**
 * Middleware to require specific scopes
 */
export function requireServiceScopes(...requiredScopes: string[]) {
  return (req: ServiceAuthRequest, res: Response, next: NextFunction) => {
    if (!req.serviceAuth) {
      res.status(401).json({
        error: 'Unauthorized',
        message: 'Service authentication required',
      });
      return;
    }

    const hasAllScopes = requiredScopes.every((scope) =>
      req.serviceAuth!.scopes.includes(scope)
    );

    if (!hasAllScopes) {
      res.status(403).json({
        error: 'Forbidden',
        message: `Missing required scopes: ${requiredScopes.join(', ')}`,
      });
      return;
    }

    next();
  };
}

/**
 * Middleware to restrict to specific caller services
 */
export function requireServiceCaller(...allowedServices: string[]) {
  return (req: ServiceAuthRequest, res: Response, next: NextFunction) => {
    if (!req.serviceAuth) {
      res.status(401).json({
        error: 'Unauthorized',
        message: 'Service authentication required',
      });
      return;
    }

    if (!allowedServices.includes(req.serviceAuth.serviceId)) {
      res.status(403).json({
        error: 'Forbidden',
        message: `Service ${req.serviceAuth.serviceId} not allowed to call this endpoint`,
      });
      return;
    }

    next();
  };
}
